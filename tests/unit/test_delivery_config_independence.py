"""
tests/unit/test_delivery_config_independence.py — fix item 1 (22-Aug-2026).

Covers T-1 .. T-7 of the build card.

THE DEFECT THIS PINS: a NULL delivery key silently inherited the intraday value, so
the delivery (CNC) book was sized on the intraday risk settings on live money. The
justifying comment claimed the delivery path "is never taken live" and that the keys
"are never read" — false on both counts.

DISCIPLINE NOTE ON T-1: the entry/SL prices of the three recorded CNC trades are not
on this machine (the local dev DB holds zero trades and the VM was not touched). What
the card supplies — and what is asserted below — is the RISK leg: risk_rs / sl_distance
= qty_by_risk (CLSEL 105.87/5.78 -> 18, MANINDS 105.87/14.46 -> 7, KRONOX 105.87/4.11
-> 25). Every OTHER leg is pinned by property instead of by anchor: each is asserted
equal to a closed-form recomputation using the GLOBAL percentages, which is literally
what the pre-change code did for a positional entry. So T-1 is anchor + property, not
anchor alone.
"""
from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from capital.position_sizer import PositionSizer, SizingResult
from capital.risk_engine import RiskEngine
from core.exceptions import ConfigSchemaError

_REPO = Path(__file__).resolve().parents[2]
_CFG = _REPO / "config"

# The five delivery keys this build makes explicit, with the section each lives in.
_DELIVERY_KEYS = {
    "position_sizing": (
        "delivery_risk_per_trade_pct",
        "delivery_max_concentration_pct",
        "delivery_max_position_value_pct",
    ),
    "risk": (
        "delivery_max_sector_exposure_pct",
        "delivery_daily_loss_limit_pct",
    ),
}

# Each delivery key and the global key whose value it must equal TODAY (B-3).
_TWINS = [
    ("position_sizing", "delivery_risk_per_trade_pct", "position_sizing", "risk_per_trade_pct"),
    ("position_sizing", "delivery_max_concentration_pct", "position_sizing", "max_concentration_pct"),
    ("position_sizing", "delivery_max_position_value_pct", "position_sizing", "max_position_value_pct"),
    ("risk", "delivery_max_sector_exposure_pct", "risk", "max_sector_exposure_pct"),
    ("risk", "delivery_daily_loss_limit_pct", "risk", "daily_loss_limit_pct"),
]


# ═════════════════════════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════════════════════════

class _Snap:
    def __init__(self, total, intraday_avail, positional_avail, daily_pnl=0.0):
        self.total = total
        self.intraday_avail = intraday_avail
        self.positional_avail = positional_avail
        self.daily_realized_pnl = daily_pnl
        self.intraday_reserved = 0.0
        self.intraday_used = 0.0
        self.positional_reserved = 0.0
        self.positional_used = 0.0
        self.ts = "2026-08-22T10:00:00+05:30"


class _FM:
    def __init__(self, snap):
        self._snap = snap

    def get_snapshot(self):
        return self._snap

    def get_unrealized_mtm_status(self):
        return 0.0, True

    def count_live_reservations(self):
        return 0

    def get_live_reservations(self):
        return {}


_LEV = {"INTRADAY": 5.0, "COVER_ORDER": 6.0, "DELIVERY": 1.0, "BRACKET_ORDER": 5.0}

# Shipped values, restated here so a drift between config and test is visible rather
# than silently absorbed. test_shipped_delivery_values_equal_todays_effective_values
# asserts these ARE what config carries.
G_RISK, G_CONC, G_POSVAL = 0.01, 0.10, 0.40


def _sizer(*, g_risk=G_RISK, g_conc=G_CONC, g_posval=G_POSVAL,
           d_risk=G_RISK, d_conc=G_CONC, d_posval=G_POSVAL,
           total=10_587.0, intraday_avail=7_410.9, positional_avail=3_176.1,
           logger=None):
    return PositionSizer(
        fund_manager=_FM(_Snap(total, intraday_avail, positional_avail)),
        leverage_map=_LEV,
        risk_per_trade_pct=g_risk,
        max_concentration_pct=g_conc,
        max_position_value_pct=g_posval,
        min_qty_threshold=1,
        tier_multipliers={"HIGH": 1.0, "MEDIUM": 0.70, "LOW": 0.50},
        logger=logger or logging.getLogger("t_item1"),
        delivery_risk_per_trade_pct=d_risk,
        delivery_max_concentration_pct=d_conc,
        delivery_max_position_value_pct=d_posval,
    )


def _pre_change_legs(total, avail, entry, sl, leverage, g_risk, g_conc):
    """The pre-change arithmetic for a POSITIONAL entry, written out.

    Before this build a positional entry with null delivery keys used the GLOBAL
    percentages. That is exactly this function. Asserting the new code against it is
    the behaviour-neutrality check, not a restatement of the new code.
    """
    sl_distance = abs(entry - sl)
    return {
        "qty_by_risk": int(math.floor((total * g_risk) / sl_distance)),
        "qty_by_capital": int(math.floor(avail / (entry / leverage))),
        "qty_by_concentration": int(math.floor((total * g_conc) / entry)),
    }


def _load_system_yaml():
    return yaml.safe_load((_CFG / "system_config.yaml").read_bytes())


def _config_dir_with(tmp_path, section, key, action):
    """Copy the REAL config dir and remove / null one key. Returns the new dir."""
    dst = tmp_path / "config"
    shutil.copytree(_CFG, dst)
    data = yaml.safe_load((dst / "system_config.yaml").read_bytes())
    assert key in data[section], f"{section}.{key} missing from the shipped config"
    if action == "remove":
        del data[section][key]
    elif action == "null":
        data[section][key] = None
    else:                                     # pragma: no cover - programmer error
        raise AssertionError(action)
    (dst / "system_config.yaml").write_bytes(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8"))
    return dst


# ═════════════════════════════════════════════════════════════════════════════
# T-1 — BEHAVIOUR NEUTRAL
# ═════════════════════════════════════════════════════════════════════════════

# (symbol, sl_distance, expected qty_by_risk) — risk_rs = 10_587 x 1% = 105.87
_CNC_TRADES = [("CLSEL", 5.78, 18), ("MANINDS", 14.46, 7), ("KRONOX", 4.11, 25)]


@pytest.mark.parametrize("symbol,sl_distance,expected_qty_by_risk", _CNC_TRADES)
def test_t1_recorded_cnc_risk_legs_are_unchanged(symbol, sl_distance, expected_qty_by_risk):
    """T-1 anchor: the three measured CNC trades reproduce their recorded risk qty."""
    entry = 100.0
    res = _sizer().calculate(symbol, "BUY", entry, entry - sl_distance, "DELIVERY",
                             score_tier="HIGH")
    assert res.breakdown["qty_by_risk"] == expected_qty_by_risk, (
        f"{symbol}: risk leg moved — {res.breakdown['qty_by_risk']} != "
        f"{expected_qty_by_risk}. THE BUILD IS WRONG, not the test."
    )
    # risk_rs itself, so a capital change cannot silently rescue the ratio
    assert abs(10_587.0 * G_RISK - 105.87) < 1e-9


@pytest.mark.parametrize("symbol,sl_distance,_expected", _CNC_TRADES)
def test_t1_every_leg_equals_the_pre_change_arithmetic(symbol, sl_distance, _expected):
    """T-1 property: EVERY leg of a positional entry equals what the old fallback
    produced — not just the risk leg the anchors cover."""
    entry, total, pos_avail = 100.0, 10_587.0, 3_176.1
    res = _sizer().calculate(symbol, "BUY", entry, entry - sl_distance, "DELIVERY",
                             score_tier="HIGH")
    before = _pre_change_legs(total, pos_avail, entry, entry - sl_distance,
                              _LEV["DELIVERY"], G_RISK, G_CONC)
    for leg, expected in before.items():
        assert res.breakdown[leg] == expected, f"{symbol}: {leg} moved"
    assert res.breakdown["raw_qty"] == min(before.values())


def test_t1_shipped_delivery_values_equal_todays_effective_values():
    """B-3 at the config level: each shipped delivery value IS the number delivery was
    already inheriting. This is the whole behaviour-neutrality guarantee, expressed as
    a property (twin == global) rather than as five magic constants."""
    data = _load_system_yaml()
    for d_sec, d_key, g_sec, g_key in _TWINS:
        assert data[d_sec][d_key] == data[g_sec][g_key], (
            f"{d_sec}.{d_key} ({data[d_sec][d_key]}) != today's effective value "
            f"{g_sec}.{g_key} ({data[g_sec][g_key]}) — deploy would MOVE a quantity"
        )


# ═════════════════════════════════════════════════════════════════════════════
# T-2 — INDEPENDENCE, BOTH DIRECTIONS
# ═════════════════════════════════════════════════════════════════════════════

def test_t2_changing_an_mis_key_does_not_move_any_delivery_limit():
    """MIS -> delivery: moving every global percentage leaves delivery sizing
    bit-identical."""
    base = _sizer().calculate("SYM", "BUY", 100.0, 94.22, "DELIVERY", score_tier="HIGH")
    moved = _sizer(g_risk=0.05, g_conc=0.90, g_posval=0.99).calculate(
        "SYM", "BUY", 100.0, 94.22, "DELIVERY", score_tier="HIGH")
    assert base.qty == moved.qty
    assert base.breakdown == moved.breakdown
    assert base.constraint == moved.constraint


def test_t2_changing_a_delivery_key_does_not_move_any_intraday_limit():
    """delivery -> MIS: moving every delivery percentage leaves intraday sizing
    bit-identical."""
    base = _sizer().calculate("SYM", "BUY", 100.0, 94.22, "INTRADAY", score_tier="HIGH")
    moved = _sizer(d_risk=0.05, d_conc=0.90, d_posval=0.99).calculate(
        "SYM", "BUY", 100.0, 94.22, "INTRADAY", score_tier="HIGH")
    assert base.qty == moved.qty
    assert base.breakdown == moved.breakdown
    assert base.constraint == moved.constraint


def test_t2_delivery_keys_actually_bind_on_a_delivery_entry():
    """The independence assertions above would also pass if the delivery keys were
    simply ignored. This is the check that could go red in that case."""
    tight = _sizer(d_risk=0.001).calculate("SYM", "BUY", 100.0, 94.22, "DELIVERY",
                                           score_tier="HIGH")
    loose = _sizer(d_risk=0.05).calculate("SYM", "BUY", 100.0, 94.22, "DELIVERY",
                                          score_tier="HIGH")
    assert tight.breakdown["qty_by_risk"] < loose.breakdown["qty_by_risk"]
    tight_c = _sizer(d_conc=0.001).calculate("SYM", "BUY", 100.0, 94.22, "DELIVERY",
                                             score_tier="HIGH")
    loose_c = _sizer(d_conc=0.90).calculate("SYM", "BUY", 100.0, 94.22, "DELIVERY",
                                            score_tier="HIGH")
    assert tight_c.breakdown["qty_by_concentration"] < loose_c.breakdown["qty_by_concentration"]


def _engine(store, *, total=100_000.0, daily_pnl=0.0,
            g_sector=0.40, g_loss=0.03, d_sector=0.40, d_loss=0.03):
    log = logging.getLogger("t_item1_engine")
    log.handlers.clear()
    return RiskEngine(
        fund_manager=_FM(_Snap(total, total * 0.7, total * 0.3, daily_pnl)),
        state_store=store,
        max_open_positions=100, max_daily_trades=100,
        max_sector_exposure_pct=g_sector,
        max_consecutive_losses=100,
        daily_loss_limit_pct=g_loss,
        sector_lookup_fn=lambda _s: "IT",
        logger=log, kill_switch=None,
        delivery_max_sector_exposure_pct=d_sector,
        delivery_daily_loss_limit_pct=d_loss,
        # BUG-NI9 (23-Aug-2026): the delivery COUNT caps now REFUSE rather than
        # defaulting to 3/5. Set high so the COUNT caps never bind -- this file
        # tests the per-book LOSS and SECTOR limits, and a count rejection would
        # mask exactly what it is asserting.
        max_open_delivery_positions=100,
        max_daily_delivery_trades=100,
    )


def _sizing(bucket):
    return SizingResult(success=True, qty=10, margin_required=100.0, risk_amount=10.0,
                        bucket=bucket, constraint="RISK", reason="ok", breakdown={})


@pytest.fixture()
def store(tmp_path):
    from core.state_store import StateStore
    s = StateStore(tmp_path / "item1.db")
    yield s
    s.close()


def test_t2_gate_daily_loss_is_per_book_both_directions(store):
    """The DAILY_LOSS pre-trade gate: a loss of 4% of capital rejects the book whose
    limit is 3% and admits the book whose limit is 50% — in BOTH assignments."""
    loss = -0.04 * 100_000.0

    # delivery tight (3%), intraday loose (50%)
    e = _engine(store, daily_pnl=loss, g_loss=0.50, d_loss=0.03)
    assert not e.approve("TCS", "BUY", "DELIVERY", _sizing("positional"), "s1").approved
    assert e.approve("TCS", "BUY", "INTRADAY", _sizing("intraday"), "s2").approved

    # and the mirror image
    e2 = _engine(store, daily_pnl=loss, g_loss=0.03, d_loss=0.50)
    assert e2.approve("TCS", "BUY", "DELIVERY", _sizing("positional"), "s3").approved
    assert not e2.approve("TCS", "BUY", "INTRADAY", _sizing("intraday"), "s4").approved


def test_t2_gate_sector_cap_is_per_book(store):
    """SECTOR_EXPOSURE: the delivery entry is measured against the delivery cap."""
    e = _engine(store, g_sector=0.90, d_sector=1e-9)
    log = logging.getLogger("t_item1_engine")

    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.msgs = []

        def emit(self, r):
            self.msgs.append(r.getMessage())

    h = _Cap()
    log.addHandler(h)
    try:
        # sector_cap_mode defaults to "observe": the cap LOGS a would-reject rather
        # than rejecting, so the delivery cap binding is visible in that record.
        e.approve("TCS", "BUY", "DELIVERY", _sizing("positional"), "s5")
        assert any("sector_cap_would_reject" in m for m in h.msgs), (
            "the delivery sector cap did not bind on a delivery entry")
        h.msgs.clear()
        e.approve("TCS", "BUY", "INTRADAY", _sizing("intraday"), "s6")
        assert not any("sector_cap_would_reject" in m for m in h.msgs), (
            "the delivery sector cap leaked onto an intraday entry")
    finally:
        log.removeHandler(h)


# ═════════════════════════════════════════════════════════════════════════════
# T-3 / T-4 — A MISSING or NULL DELIVERY KEY REJECTS AT STARTUP, NAMING THE KEY
# ═════════════════════════════════════════════════════════════════════════════

_ALL_KEYS = [(sec, k) for sec, keys in _DELIVERY_KEYS.items() for k in keys]


@pytest.mark.parametrize("action", ["remove", "null"])
@pytest.mark.parametrize("section,key", _ALL_KEYS)
def test_t3_t4_missing_or_null_delivery_key_rejects_naming_the_key(
        tmp_path, section, key, action):
    """T-3 (missing) and T-4 (null): config load REJECTS, and the key is named in the
    line the boot actually logs. ⛔ Asserted through main._config_error_detail — the
    real operator-facing rendering — not through the exception repr."""
    from core.config_loader import load_all
    from main import _config_error_detail

    cfg_dir = _config_dir_with(tmp_path, section, key, action)
    with pytest.raises(ConfigSchemaError) as exc_info:
        load_all(cfg_dir)

    detail = _config_error_detail(exc_info.value)
    assert key in detail, (
        f"the boot log would NOT name {section}.{key} ({action}); it said: {detail!r}")
    assert "system_config.yaml" in str(exc_info.value)


@pytest.mark.parametrize("section,key", _ALL_KEYS)
def test_t3_t4_rejection_is_not_a_fallback(tmp_path, section, key):
    """⛔ The key point: it must REJECT, not inherit. A load that returns an AppConfig
    carrying the global value in the delivery slot is the exact defect being removed."""
    from core.config_loader import load_all

    cfg_dir = _config_dir_with(tmp_path, section, key, "null")
    try:
        cfg = load_all(cfg_dir)
    except ConfigSchemaError:
        return                        # correct: rejected
    raise AssertionError(
        f"{section}.{key}=null LOADED instead of rejecting; value is "
        f"{getattr(getattr(cfg.system, section), key)!r} — the silent fallback is back"
    )


def test_t3_sizer_refuses_rather_than_inheriting():
    """Defence in depth below the config layer: a sizer wired without delivery limits
    refuses a positional entry instead of borrowing the intraday number."""
    s = _sizer(d_risk=None)
    with pytest.raises(ValueError) as e:
        s.calculate("SYM", "BUY", 100.0, 94.22, "DELIVERY", score_tier="HIGH")
    assert "delivery_risk_per_trade_pct" in str(e.value)


def test_t3_gate_refuses_rather_than_inheriting(store):
    """Same, for the pre-trade gate."""
    e = _engine(store, d_loss=None)
    with pytest.raises(ValueError) as exc:
        e.approve("TCS", "BUY", "DELIVERY", _sizing("positional"), "s7")
    assert "delivery_daily_loss_limit_pct" in str(exc.value)


# ═════════════════════════════════════════════════════════════════════════════
# T-5 — A VALID CONFIG BOOTS CLEANLY  (the §5 falsifier: MAKE THIS THOROUGH)
# ═════════════════════════════════════════════════════════════════════════════

def test_t5_the_shipped_config_loads():
    """The single most important assertion in this file. If this can ever go red on a
    VALID config, Monday's UNATTENDED 08:15 boot does not start and the day is lost."""
    from core.config_loader import load_all
    cfg = load_all(_CFG)
    ps, rk = cfg.system.position_sizing, cfg.system.risk
    for key in _DELIVERY_KEYS["position_sizing"]:
        v = getattr(ps, key)
        assert isinstance(v, float) and 0 < v <= 1, f"{key} = {v!r}"
    for key in _DELIVERY_KEYS["risk"]:
        v = getattr(rk, key)
        assert isinstance(v, float) and 0 < v <= 1, f"{key} = {v!r}"


def test_t5_valid_variants_are_not_rejected(tmp_path):
    """A rejection path that fires on anything except a missing/invalid value would be
    worse than the defect. Each variant below is VALID and must load."""
    from core.config_loader import load_all
    data = _load_system_yaml()

    variants = {}

    # (a) keys reordered inside their sections
    reordered = dict(data)
    for sec in ("position_sizing", "risk"):
        reordered[sec] = dict(reversed(list(data[sec].items())))
    variants["reordered"] = reordered

    # (b) the same values written in exponent form
    expform = yaml.safe_load(
        yaml.safe_dump(data, sort_keys=False).replace("0.01\n", "1.0e-2\n"))
    variants["exponent_form"] = expform

    # (c) an integer-valued percentage (1 == 100%, still inside (0, 1])
    intval = yaml.safe_load(yaml.safe_dump(data, sort_keys=False))
    intval["position_sizing"]["delivery_max_position_value_pct"] = 1
    variants["int_valued_pct"] = intval

    # (d) a full yaml round-trip with comments stripped and keys sorted
    variants["round_tripped_sorted"] = yaml.safe_load(
        yaml.safe_dump(data, sort_keys=True))

    for name, payload in variants.items():
        dst = tmp_path / name
        shutil.copytree(_CFG, dst)
        (dst / "system_config.yaml").write_bytes(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).encode("utf-8"))
        try:
            load_all(dst)
        except Exception as exc:                       # noqa: BLE001
            raise AssertionError(f"VALID variant {name!r} was rejected: {exc}") from exc


def test_t5_the_rejection_path_is_reachable_only_by_removing_or_nulling(tmp_path):
    """The control for the test above: prove the check could have gone red. A config
    that is byte-for-byte the shipped one except for one deleted key MUST fail — so
    'the valid variants all loaded' is evidence, not a vacuous pass."""
    from core.config_loader import load_all
    cfg_dir = _config_dir_with(tmp_path, "risk", "delivery_daily_loss_limit_pct", "remove")
    with pytest.raises(ConfigSchemaError):
        load_all(cfg_dir)


# ═════════════════════════════════════════════════════════════════════════════
# T-6 — INTRADAY SIZING UNCHANGED
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tier", ["HIGH", "MEDIUM", "LOW"])
@pytest.mark.parametrize("entry,sl", [(100.0, 94.22), (2500.0, 2450.0), (37.5, 33.39)])
def test_t6_intraday_result_is_identical_however_delivery_is_configured(tier, entry, sl):
    """MIS LONG is 231 of 266 entries. Nothing about it may move — including when the
    delivery keys are absent entirely."""
    baseline = _sizer().calculate("SYM", "BUY", entry, sl, "INTRADAY", score_tier=tier)
    for kw in ({"d_risk": 0.05, "d_conc": 0.99, "d_posval": 0.99},
               {"d_risk": None, "d_conc": None, "d_posval": None}):
        other = _sizer(**kw).calculate("SYM", "BUY", entry, sl, "INTRADAY", score_tier=tier)
        assert (baseline.success, baseline.qty, baseline.constraint,
                baseline.margin_required, baseline.risk_amount, baseline.breakdown) == \
               (other.success, other.qty, other.constraint,
                other.margin_required, other.risk_amount, other.breakdown)


def test_t6_intraday_legs_equal_the_pre_change_arithmetic():
    """And the intraday legs still equal the closed-form global computation."""
    entry, sl, total, intraday_avail = 100.0, 94.22, 10_587.0, 7_410.9
    res = _sizer().calculate("SYM", "BUY", entry, sl, "INTRADAY", score_tier="HIGH")
    before = _pre_change_legs(total, intraday_avail, entry, sl,
                              _LEV["INTRADAY"], G_RISK, G_CONC)
    for leg, expected in before.items():
        assert res.breakdown[leg] == expected, f"intraday {leg} moved"


# ═════════════════════════════════════════════════════════════════════════════
# T-7 — THE DRIFT COMPARATOR IS UNAFFECTED
# ═════════════════════════════════════════════════════════════════════════════

def test_t7_drift_comparator_reads_no_sizing_or_risk_percentage():
    """CHECK 1 / CHECK 2 must stay a function of the FundManager snapshot and broker
    margins alone. This is a containment check that CAN go red: wire any of these ten
    keys into the drift comparator and it fails."""
    src = (_REPO / "orders" / "order_reconciler.py").read_text(encoding="utf-8")
    start = src.index("CHECK 1: what broker cash SHOULD read")
    end = src.index("FIX-189 (P1-B): suppress the overnight false positive", start)
    body = src[start:end]

    forbidden = [
        "risk_per_trade_pct", "max_concentration_pct", "max_position_value_pct",
        "max_sector_exposure_pct", "daily_loss_limit_pct",
        "delivery_risk_per_trade_pct", "delivery_max_concentration_pct",
        "delivery_max_position_value_pct", "delivery_max_sector_exposure_pct",
        "delivery_daily_loss_limit_pct",
    ]
    for key in forbidden:
        assert key not in body, f"the drift comparator now reads {key}"

    # and the two comparands are still exactly the recorded algebra
    assert "expected = real_capital - held - snapshot.daily_realized_pnl" in body
    assert "margin_residual = held_today - margins.used" in body


def test_t7_drift_operands_are_snapshot_fields_only():
    """CHECK 1's `held` is the four FundManager bucket fields — unchanged by this
    build, which touches no capital accounting at all."""
    src = (_REPO / "orders" / "order_reconciler.py").read_text(encoding="utf-8")
    assert (
        "held = (\n"
        "            snapshot.intraday_reserved + snapshot.intraday_used\n"
        "            + snapshot.positional_reserved + snapshot.positional_used\n"
        "        )"
    ) in src


# ═════════════════════════════════════════════════════════════════════════════
# B-5 — THE FALSE COMMENT IS GONE
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("relpath", [
    "core/config_loader.py",
    "capital/position_sizer.py",
    "config/system_config.yaml",
    "main.py",
])
def test_b5_no_surviving_claim_that_delivery_is_never_live(relpath):
    """The stale comment is what hid this for weeks. It must not survive anywhere it
    could be read as current."""
    text = (_REPO / relpath).read_text(encoding="utf-8")
    lowered = text.lower()
    for phrase in ("never taken live", "these are never read",
                   "is never sized live", "never occurs live"):
        assert phrase not in lowered, f"{relpath} still claims: {phrase!r}"
    # the specific scaffold framing, in either spelling
    assert "delivery-scoped sizing scaffold" not in lowered, relpath


def test_b5_position_value_cap_reports_what_it_enforced():
    """P-6: the CRITICAL log and the rejection reason must state the ENFORCED pct.
    Populating the delivery key is exactly what separates it from the global."""
    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, r):
            self.records.append(r)

    h = _Cap()
    log = logging.getLogger("t_item1_posval")
    log.handlers.clear()
    log.addHandler(h)
    # delivery cap 1% vs global 40% -> the cap binds, and must be REPORTED as 1%
    res = _sizer(d_posval=0.01, g_posval=0.40, logger=log).calculate(
        "SYM", "BUY", 100.0, 94.22, "DELIVERY", score_tier="HIGH")
    assert res.constraint == "POSITION_VALUE_CAP", res.constraint
    assert "1%" in res.reason and "40%" not in res.reason, res.reason
    crit = [r for r in h.records if r.levelno == logging.CRITICAL]
    assert crit, "no CRITICAL was logged for a position-value-cap rejection"
    assert getattr(crit[-1], "max_position_value_pct", None) == 0.01
