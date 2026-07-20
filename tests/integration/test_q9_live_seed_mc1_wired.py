"""
tests/integration/test_q9_live_seed_mc1_wired.py — the LIVE capital seed (M-C1), wired.

THE GAP THIS CLOSES. Every wired test in the Q9 programme (batches 1-5) ran with the
`wired_system` fixture, which is `paper_mode=True`. Production runs `main.py --mode live`. The
programme's parity claim was STRUCTURAL — one shared restore path, only the seed differs — and
that argument is sound, but it stops exactly where the two modes genuinely diverge: THE SEED.

    PAPER seed  main.py:2243   selected_account.paper_capital        static; excludes today's P&L
    LIVE  seed  main.py:2255   broker.get_margins().net
                               - fund_manager.today_realized_pnl_carryover()      <-- M-C1

WHY THE SUBTRACTION EXISTS. On a mid-day warm restart the broker's `net` ALREADY includes today's
realized P&L, and rehydrate Phase 2 (fund_manager.py:1703-1711) then RE-ADDS it
(`bucket avail += pnl`, `_total += pnl`). Without the subtraction today's P&L is counted TWICE
and live reservable capital silently INFLATES — the system would trade on capital it does not
have. Measured here: without it, `_total` lands 18,094.54 above `broker.net`, exactly the
carryover.

🔴 SAFETY — THIS FILE CANNOT REACH A REAL BROKER, BY CONSTRUCTION.
"Live mode" here means the live SEEDING ARITHMETIC, never a live BROKER CONNECTION. There is no
live-mode fixture: the only thing the live seed needs from a broker is one number, so this file
uses `_StubBroker`, a local object whose `get_margins()` returns a value the test chooses. No
adapter is constructed, no credentials are read, `kiteconnect` is never imported.
`TestNoBrokerReachable` pins that for the whole test tree.

⭐ §A4 — WHAT EXECUTES, AND WHAT IS REPLICATED (rule F, stated honestly; the trap that has
fired three times running — batch 4 twice, batch 5's reused KillSwitch).

  REAL production code, driven by these tests:
      fund_manager.today_realized_pnl_carryover()        :1777   the live-seed subtrahend
      fund_manager._today_release_used_pnl_rows()        :1757   the SHARED row selection
      fund_manager.initialize(seed)                      :408    main.py:2259
      fund_manager.rehydrate_from_open_trades()          :1626   main.py:2286, Phase 2

  REPLICATED, because it is not callable:
      the two-line expression `broker.get_margins().net - ...carryover()` is INLINE inside
      main()'s body (main.py:2255-2258) with no function boundary, so no test can invoke it
      without running main() itself — which in live mode is precisely what must never happen.
      It is instead PINNED STRUCTURALLY by
      test_q9_post_restart_capital_wired.TestParity::test_the_live_seed_still_subtracts_the_carryover,
      which fails if the subtraction is removed or the two sides stop sharing the row helper.

  ⇒ These tests prove the MECHANISM — that the subtrahend is exactly what Phase 2 re-adds, so the
    seed cancels to broker.net. That is the part that can silently break. The arithmetic that
    consumes it is two lines under a regex pin. RUNTIME-PROVEN, not assumed:
    `_today_release_used_pnl_rows` CALL COUNT == 2 (once per side), asserted below.

⚠️ §A2 — THE CANCELLATION IS CONTRACT-INDEPENDENT, SO IT SURVIVES E4/W10.
Both sides sum `pnl_delta` from the SAME helper, so `net - Σ + Σ == net` holds whatever
`pnl_delta` means. If either side ever computes its rows independently the cancellation breaks
SILENTLY, with neither number looking wrong on its own — the same shape as E4/W10 itself.
TestSharedHelper pins it.

  ⚠️ CORRECTED 20-Jul-2026 — §A2 as originally written contained a FALSE claim, and it is
  removed here rather than left to be re-read as fact. It said the daily-loss reader "is not
  involved in the cancellation at all" / "the seed and Phase 2 never consult it".
  **That is false:** `rehydrate_from_open_trades` calls `get_daily_realized_net_pnl` at
  fund_manager.py:1736. The true claim is narrower — the value is read AFTER Phase 2 and AFTER
  the invariant check, and is used ONLY as a log field. Shared code path; no causal dependency.
  §A2 also leaned on the reader being a *numerically different* quantity (the observed
  reader=-18,189.08 vs carryover=-18,094.54, differing by exactly Σcosts). That difference was a
  CONSEQUENCE of the old contract, never the REASON for independence — post-E4/W10 the two are
  equal during the session and differ only after the EOD reset.
  The conclusion (the cancellation survives E4/W10) STANDS, on three structural reasons set out
  in TestSharedHelper's docstring and in
  docs/audit/mc1_live_seed_rederivation_20jul2026.md (verdict: STILL SOUND, NEW REASON).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from capital.fund_manager import FundManager
from core.time_authority import now_ist
from tests.integration.conftest import PAPER_CAPITAL, SystemContext
from tests.integration.test_q9_daily_loss_limit_wired import _drive_losing_close

TOL = 0.01

# An arbitrary, controlled "broker margin". Deliberately NOT round and NOT equal to
# PAPER_CAPITAL, so a seed that silently fell back to the paper value would be obvious.
BROKER_NET = 471_234.56


class _StubBroker:
    """The ONLY thing the live seed needs from a broker: one number.

    Not a ZerodhaAdapter, not a KiteConnect, no credentials, no network. Deliberately minimal —
    a fuller double would be a place for a real client to creep in later.
    """

    class _Margins:
        def __init__(self, net: float) -> None:
            self.net = net

    def __init__(self, net: float = BROKER_NET) -> None:
        self._net = net
        self.calls = 0

    def get_margins(self) -> "_StubBroker._Margins":
        self.calls += 1
        return self._Margins(self._net)


def _fresh_fm(ctx: SystemContext) -> FundManager:
    """A FundManager over the SAME store — a restart, as main.py builds one."""
    return FundManager(
        state_store=ctx.store, bus=ctx.bus, logger=ctx.fund_manager._log,
        intraday_bucket_pct=0.70, positional_bucket_pct=0.30,
        daily_loss_limit_pct=0.02, leverage_map=ctx.fund_manager._leverage_map,
    )


def _boot(ctx: SystemContext, seed: float) -> tuple:
    """initialize(seed) -> rehydrate(), i.e. main.py:2259 then :2286. Returns (fm, counts)."""
    fm = _fresh_fm(ctx)
    counts = {"helper": 0}
    real = fm._today_release_used_pnl_rows

    def _spy(s):
        counts["helper"] += 1
        return real(s)

    fm._today_release_used_pnl_rows = _spy
    fm.initialize(seed)
    counts["summary"] = fm.rehydrate_from_open_trades()
    return fm, counts


def _live_seed(ctx: SystemContext, broker: _StubBroker) -> tuple:
    """The live seed exactly as main.py:2255-2258 composes it, using the REAL carryover."""
    fm_probe = _fresh_fm(ctx)
    carry = fm_probe.today_realized_pnl_carryover()      # REAL production function
    return broker.get_margins().net - carry, carry


def _pic(fm) -> dict:
    s = fm.get_snapshot()
    return {
        "total": s.total,
        "intraday_avail": s.intraday_avail, "positional_avail": s.positional_avail,
        "intraday_reserved": s.intraday_reserved, "positional_reserved": s.positional_reserved,
        "intraday_used": s.intraday_used, "positional_used": s.positional_used,
        "daily_realized_pnl": s.daily_realized_pnl,
    }


def _assert_identity(fm, stage: str) -> None:
    """Batch 3's I1 — the GLOBAL identity (per-bucket does NOT hold; fund_manager.py:2235)."""
    p = _pic(fm)
    lhs = round(p["intraday_avail"] + p["positional_avail"] + p["intraday_reserved"]
                + p["positional_reserved"] + p["intraday_used"] + p["positional_used"], 2)
    rhs = round(p["total"], 2)
    assert abs(lhs - rhs) <= TOL, (
        f"CAPITAL INVARIANT BROKEN at {stage!r}: avail+reserved+used={lhs:.2f} != total={rhs:.2f}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. 🔴 SAFETY FIRST — no test can reach a real broker
# ═════════════════════════════════════════════════════════════════════════════

class TestNoBrokerReachable:
    """§1. This is the one place in the project where a harness could, in principle, load real
    credentials and transact against the real account. These assertions cover the WHOLE test
    tree, not just this file."""

    def test_the_stub_broker_is_not_an_adapter_and_holds_no_credentials(self):
        b = _StubBroker()
        assert type(b).__module__ == __name__, "the stub must be local to this file"
        assert not hasattr(b, "place_order"), "the stub must not be able to transact"
        assert not hasattr(b, "kite"), "the stub must hold no client"
        assert b.get_margins().net == BROKER_NET
        for attr in vars(b):
            assert "token" not in attr.lower() and "key" not in attr.lower()

    def test_the_wired_fixture_never_builds_a_real_client(self):
        conftest = (Path(__file__).resolve().parent / "conftest.py").read_text(encoding="utf-8")
        assert "paper_mode=True" in conftest, "the integration fixture must stay paper-mode"
        assert "kite_client=None" in conftest, (
            "the integration fixture must pass kite_client=None — a real client must be "
            "unreachable from any integration test"
        )

    # The two doors to a real broker. Deliberately NARROW: importing kiteconnect's EXCEPTION
    # classes (`from kiteconnect import exceptions as kex`) is legitimate and widespread in the
    # adapter tests — it carries no credential risk. What must never appear is CONSTRUCTING the
    # client, or reading the credential environment variables.
    _CLIENT_CTOR = re.compile(r"(?<![\w.])KiteConnect\s*\(")
    _CRED_READ = re.compile(
        r"os\.environ\s*\[\s*[\"']ZERODHA_(API_KEY|ACCESS_TOKEN)[\"']\s*\]"
        r"|os\.environ\.get\s*\(\s*[\"']ZERODHA_(API_KEY|ACCESS_TOKEN)[\"']"
    )

    def test_no_test_constructs_a_real_client_or_reads_live_credentials(self):
        """The credential doors are `main._build_kite_client` (main.py:381, which reads
        os.environ['ZERODHA_API_KEY'/'ZERODHA_ACCESS_TOKEN']) and constructing KiteConnect
        directly. Neither may be opened from the test tree."""
        root = Path(__file__).resolve().parents[1]           # tests/
        offenders = []
        for py in sorted(root.rglob("*.py")):
            if py.name == Path(__file__).name:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if self._CLIENT_CTOR.search(line):
                    offenders.append(f"{py.name}:{i}: constructs a real client — {s}")
                if self._CRED_READ.search(line):
                    offenders.append(f"{py.name}:{i}: reads a live credential — {s}")
        assert offenders == [], (
            "A TEST CAN NOW REACH REAL BROKER CREDENTIALS. 'Live mode' in this project means the "
            "live SEEDING code path, never a live BROKER CONNECTION.\n" + "\n".join(offenders)
        )

    def test_the_guard_would_catch_the_real_doors_but_not_exception_imports(self):
        """Anti-vacuity + precision: the patterns must match the real doors and must NOT fire on
        the legitimate exception imports the adapter tests rely on."""
        assert self._CLIENT_CTOR.search('kite = KiteConnect(api_key=os.environ["ZERODHA_API_KEY"])')
        assert self._CRED_READ.search('KiteConnect(api_key=os.environ["ZERODHA_API_KEY"])')
        assert self._CRED_READ.search('tok = os.environ.get("ZERODHA_ACCESS_TOKEN")')
        # ...and must stay quiet on these:
        assert not self._CLIENT_CTOR.search("from kiteconnect import exceptions as kex")
        assert not self._CLIENT_CTOR.search(
            "from kiteconnect.exceptions import TokenException, NetworkException")
        assert not self._CRED_READ.search('assert "ZERODHA_API_KEY_LFL836" in secrets')
        assert not self._CLIENT_CTOR.search("mock_kite = MagicMock(spec=KiteConnect)")


# ═════════════════════════════════════════════════════════════════════════════
# 2. THE CANCELLATION — positive, negative, and the double-count it prevents
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestLiveSeedCancellation:

    def test_live_seed_lands_total_exactly_on_broker_net(self, wired_system):
        """B1 POSITIVE. The seed subtracts the carryover, Phase 2 re-adds it, and `_total`
        lands on `broker.net` — by construction, not by luck."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)

        broker = _StubBroker()
        seed, carry = _live_seed(ctx, broker)

        # ⭐ ANTI-VACUITY, THE WHOLE GAME (§A5): with no P&L the carryover is 0, the subtraction
        # is a no-op, and deleting it entirely would change nothing. Assert it bites FIRST.
        assert carry != 0.0, (
            "the carryover is ZERO — the subtraction is a no-op and this test would pass with "
            "M-C1 deleted. The scenario must realize P&L before restarting."
        )
        assert seed != BROKER_NET, "the seed must differ from broker.net, or nothing was subtracted"
        assert seed == pytest.approx(BROKER_NET - carry, abs=TOL)

        fm, counts = _boot(ctx, seed)

        # §A4: BOTH sides went through the shared helper — once each.
        assert counts["helper"] == 1, (
            f"Phase 2 hit the shared row helper {counts['helper']} times, expected 1"
        )
        assert counts["summary"]["replayed_pnl_rows"] >= 2, (
            f"Phase 2 replayed {counts['summary']['replayed_pnl_rows']} P&L rows — it must "
            f"re-apply the very rows the seed subtracted, or there is nothing to cancel"
        )

        # ⭐ THE ASSERTION THIS BATCH EXISTS FOR.
        assert fm.get_snapshot().total == pytest.approx(BROKER_NET, abs=TOL), (
            f"LIVE SEED DID NOT CANCEL: _total={fm.get_snapshot().total:.4f} but broker.net="
            f"{BROKER_NET:.4f} (drift {fm.get_snapshot().total - BROKER_NET:+.4f}; carryover "
            f"was {carry:.4f}). Live reservable capital is wrong on a warm restart."
        )
        _assert_identity(fm, "live-seeded")

    def test_without_the_subtraction_todays_pnl_is_counted_twice(self, wired_system):
        """The failure M-C1 prevents, made explicit: seed with the raw broker.net and the
        carryover is applied a second time by Phase 2."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)
        _, carry = _live_seed(ctx, _StubBroker())
        assert carry != 0.0

        fm_wrong, _ = _boot(ctx, BROKER_NET)          # NO subtraction
        drift = fm_wrong.get_snapshot().total - BROKER_NET

        assert drift == pytest.approx(carry, abs=TOL), (
            f"expected the un-subtracted seed to drift by exactly the carryover "
            f"({carry:.4f}), got {drift:.4f}"
        )
        # A loss day inflates nothing, but a WIN day would: the sign carries through.
        assert abs(drift) > TOL, "the double-count must be observable"

    def test_with_no_realized_pnl_the_subtraction_is_a_noop(self, wired_system):
        """B2 NEGATIVE. Without this, 'the seed equals broker.net' cannot distinguish a working
        cancellation from a subtraction that never runs at all."""
        ctx = wired_system
        broker = _StubBroker()
        seed, carry = _live_seed(ctx, broker)

        assert carry == 0.0, f"cold-boot scenario must have no realized P&L, got {carry}"
        assert seed == pytest.approx(BROKER_NET, abs=TOL), "with no P&L the seed IS broker.net"

        fm, counts = _boot(ctx, seed)
        assert counts["summary"]["replayed_pnl_rows"] == 0, "no P&L rows should be carried"
        assert fm.get_snapshot().total == pytest.approx(BROKER_NET, abs=TOL)
        _assert_identity(fm, "cold-live-seeded")

    def test_the_capital_picture_quantity_by_quantity(self, wired_system):
        """B3. An aggregate match can hide two compensating errors — and this is a bug class
        DEFINED by two numbers cancelling. So assert each quantity, not the sum."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        broker = _StubBroker()
        seed, carry = _live_seed(ctx, broker)
        assert carry != 0.0

        fm, _ = _boot(ctx, seed)
        p = _pic(fm)

        assert p["total"] == pytest.approx(BROKER_NET, abs=TOL)
        # No open positions in this scenario -> nothing committed or reserved.
        assert p["intraday_reserved"] == pytest.approx(0.0, abs=TOL)
        assert p["positional_reserved"] == pytest.approx(0.0, abs=TOL)
        assert p["intraday_used"] == pytest.approx(0.0, abs=TOL)
        assert p["positional_used"] == pytest.approx(0.0, abs=TOL)
        # Available is the whole of total, split by the configured bucket percentages.
        assert p["intraday_avail"] + p["positional_avail"] == pytest.approx(p["total"], abs=TOL)
        # The reader is a DIFFERENT quantity from the carryover (E4/W10 costs) — assert it
        # survived the restart, but never that it equals the carryover.
        assert p["daily_realized_pnl"] == pytest.approx(
            ctx.store.get_daily_realized_net_pnl(now_ist().date().isoformat()), abs=TOL)
        _assert_identity(fm, "quantity-by-quantity")


# ═════════════════════════════════════════════════════════════════════════════
# 3. PARITY, IN BOTH DIRECTIONS — the claim Q9 rested on structurally
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestParityBothDirections:
    """Each mode's seed produces the CORRECT (and different) result, through the SAME restore
    logic. Batches 1-5 argued this structurally; here it is measured."""

    def test_paper_and_live_seeds_each_land_correctly(self, wired_system):
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)
        _, carry = _live_seed(ctx, _StubBroker())
        assert carry != 0.0, "anti-vacuity: both modes must have P&L to carry"

        # LIVE: seed excludes today's P&L (subtracted), Phase 2 re-adds it -> broker.net.
        fm_live, _ = _boot(ctx, BROKER_NET - carry)
        # PAPER: static seed ALREADY excludes today's P&L, Phase 2 adds it -> paper + P&L.
        fm_paper, _ = _boot(ctx, PAPER_CAPITAL)

        assert fm_live.get_snapshot().total == pytest.approx(BROKER_NET, abs=TOL), (
            "live: total must land on broker.net"
        )
        assert fm_paper.get_snapshot().total == pytest.approx(PAPER_CAPITAL + carry, abs=TOL), (
            f"paper: total must be paper_capital + today's P&L "
            f"({PAPER_CAPITAL} + {carry}), got {fm_paper.get_snapshot().total}"
        )
        # ANTI-VACUITY: the two modes genuinely differ, so this proves two behaviours not one.
        assert abs(fm_live.get_snapshot().total - fm_paper.get_snapshot().total) > TOL, (
            "the two seeds produced the same total — the scenario cannot distinguish them"
        )
        _assert_identity(fm_live, "parity-live")
        _assert_identity(fm_paper, "parity-paper")


class TestParityStructural:
    """The structural half of the parity claim — no fixture needed, so it lives outside the
    parametrized class above."""

    def test_both_modes_use_the_same_restore_function(self):
        """There is ONE rehydrate, with no mode branch, and ONE initialize call site in
        main.py. Only the seed differs."""
        root = Path(__file__).resolve().parents[2]
        fm_src = (root / "capital" / "fund_manager.py").read_text(encoding="utf-8")
        start = fm_src.index("def rehydrate_from_open_trades")
        end = fm_src.index("def today_realized_pnl_carryover")
        body = "\n".join(l for l in fm_src[start:end].splitlines()
                         if not l.lstrip().startswith("#"))
        for tok in ("paper_mode", "is_paper", "live_mode"):
            assert tok not in body, f"rehydrate now branches on {tok!r} — parity is forked"

        main_src = (root / "main.py").read_text(encoding="utf-8")
        assert main_src.count("fund_manager.initialize(_startup_capital)") == 1, (
            "there must be exactly ONE initialize call site fed by both seeds"
        )


# ═════════════════════════════════════════════════════════════════════════════
# 4. §A2 — THE SHARED HELPER IS WHAT MAKES THE CANCELLATION CONTRACT-INDEPENDENT
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("wired_system", [{"paper_auto_fill_delay_sec": 60.0}], indirect=True)
class TestSharedHelper:
    """If the two sides ever compute their rows independently the cancellation breaks SILENTLY —
    neither number looks wrong on its own. That is the E4/W10 shape, and it is the reason this
    is pinned rather than assumed.

    ⭐ WHY THESE PIN A STRUCTURE AND NOT A NUMBER (re-derivation, 20-Jul-2026).
    An earlier test here asserted `reader != carryover` — a NUMERICAL proxy for the structural
    claim "the reader is not involved". The two were never equivalent: the gap was merely a
    CONSEQUENCE of the old contract (it equalled Σcosts). E4/W10 makes the two equal during the
    session, so the proxy fired on a merge that changed nothing about the mechanism — and it
    would have stayed silent had the mechanism broken while the numbers happened to differ.
    **A test that pins a symptom rather than the property fails on changes that do not threaten
    it and stays silent on changes that do. Both halves are bad.**

    (A naive inversion to `reader == carryover` would have been wrong too: post-E4/W10 the two
    are equal DURING the session but differ AFTER the EOD reset, because the reader has no
    entry_type filter and therefore sees RESET_PNL. It was a transient coincidence either way.)

    The three tests below pin the three STRUCTURAL reasons the cancellation is contract-
    independent, and all three pass identically before and after E4/W10:
      1. both sides draw the same rows/field from ONE shared helper ⇒ (net-Σ)+Σ = net for any
         meaning of `pnl_delta`            → test_both_sides_still_call_the_shared_helper
                                             + test_the_carryover_equals_exactly_what_phase_2…
      2. the ONLY reader-derived ledger row (RESET_PNL) is excluded by the helper's
         entry_type filter                 → test_the_reset_pnl_row_cannot_reach_the_cancellation
      3. the one reader call on the path is a log field
                                           → test_the_rehydrate_reader_call_is_observational_only

    FALSIFICATION CONDITIONS (the original argument had none — which is why it survived until a
    merge broke it):
      #1 the two sides cease to share `_today_release_used_pnl_rows`  (pinned: tests 1)
      #2 ⭐ that helper's `entry_type='RELEASE_USED'` filter is dropped or widened to admit
         RESET_PNL — THE LOAD-BEARING ONE                              (pinned: test 2)
      #3 rehydrate_from_open_trades:1736 ceases to be log-only         (pinned: test 3)
      #4 the seed and rehydrate derive their own day-floors and can straddle midnight —
         pre-existing, contract-independent, NOT pinned here. See
         docs/audit/mc1_live_seed_rederivation_20jul2026.md §C2(e).

    Full argument: docs/audit/mc1_live_seed_rederivation_20jul2026.md."""

    def test_the_carryover_equals_exactly_what_phase_2_re_applies(self, wired_system):
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)

        fm = _fresh_fm(ctx)
        start = now_ist().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        rows = fm._today_release_used_pnl_rows(start)
        row_sum = sum(float(r["pnl_delta"]) for r in rows)
        carry = fm.today_realized_pnl_carryover()

        assert len(rows) >= 2, "scenario must produce at least two RELEASE_USED rows"
        assert carry == pytest.approx(row_sum, abs=1e-9), (
            f"the live-seed subtrahend ({carry}) is not the sum of the rows Phase 2 replays "
            f"({row_sum}) — the cancellation is no longer exact"
        )

    def test_the_reset_pnl_row_cannot_reach_the_cancellation(self, wired_system):
        """⭐ THE LOAD-BEARING STRUCTURAL FACT (re-derivation 20-Jul-2026, reason 2).

        `reset_daily_pnl` (fund_manager.py:1603/:1610) writes
        `RESET_PNL.pnl_delta = -get_daily_realized_net_pnl(today)` — the SOLE causal edge from
        the daily-loss reader into the ledger. `_today_release_used_pnl_rows` (:1757) filters
        `entry_type='RELEASE_USED'`, so that row is invisible to BOTH sides of the cancellation.
        The reader's value therefore cannot reach the seed or Phase 2 under ANY contract.

        CONTRACT-AGNOSTIC: the RESET_PNL row's VALUE differs between contracts
        (-(Σpnl_delta - Σcosts) pre-E4/W10 vs -Σpnl_delta post-); its EXCLUSION does not. This
        test passes identically on both sides of that migration.

        ⚠️ FALSIFIER #2 — if that `entry_type='RELEASE_USED'` filter is ever dropped or widened
        to admit RESET_PNL, the reader's value enters the cancellation and the independence
        genuinely breaks. This test is the one that fails.
        """
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)

        carry_before = _fresh_fm(ctx).today_realized_pnl_carryover()
        # ANTI-VACUITY (1): with a zero carryover the exclusion would be untestable.
        assert carry_before != 0.0, (
            "the carryover is ZERO — the scenario realized no P&L, so excluding RESET_PNL "
            "would be trivially true and this test would prove nothing"
        )

        ctx.fund_manager.reset_daily_pnl()          # writes the ONE reader-derived ledger row

        reset_rows = ctx.store.fetch_all(
            "SELECT pnl_delta FROM fm_ledger WHERE entry_type = 'RESET_PNL'"
        )
        # ANTI-VACUITY (2): if the reset wrote nothing, or wrote a zero, there is no row whose
        # exclusion could matter — the assertion below would pass for the wrong reason.
        assert len(reset_rows) == 1, (
            f"expected exactly one RESET_PNL row, got {len(reset_rows)} — the reset did not run "
            f"as expected and the exclusion is untested"
        )
        reset_delta = float(reset_rows[0]["pnl_delta"])
        assert abs(reset_delta) > TOL, (
            f"the RESET_PNL row carries pnl_delta={reset_delta} — a zero row would be excluded "
            f"or included with identical effect, so this test would prove nothing"
        )

        carry_after = _fresh_fm(ctx).today_realized_pnl_carryover()
        assert carry_after == pytest.approx(carry_before, abs=TOL), (
            f"THE RESET_PNL ROW REACHED THE CANCELLATION: carryover moved {carry_before:.4f} -> "
            f"{carry_after:.4f} after an EOD reset wrote pnl_delta={reset_delta:.4f}. The "
            f"_today_release_used_pnl_rows entry_type='RELEASE_USED' filter no longer excludes "
            f"it, so the daily-loss reader's value now feeds the live seed — falsifier #2 of the "
            f"20-Jul re-derivation. The seed is NOT contract-independent any more."
        )

        # …and the cancellation still lands, with a RESET_PNL row on the books.
        broker = _StubBroker()
        fm, _ = _boot(ctx, broker.get_margins().net - carry_after)
        assert fm.get_snapshot().total == pytest.approx(BROKER_NET, abs=TOL), (
            f"live seed failed to cancel with a RESET_PNL row present: _total="
            f"{fm.get_snapshot().total:.4f} vs broker.net={BROKER_NET:.4f}"
        )
        _assert_identity(fm, "live-seeded-after-reset")

    def test_the_rehydrate_reader_call_is_observational_only(self, wired_system, monkeypatch):
        """⭐ CORRECTS §A2 (re-derivation 20-Jul-2026, reason 3).

        §A2 claimed *"the seed and Phase 2 never consult it"*. That is FALSE:
        `rehydrate_from_open_trades` calls `get_daily_realized_net_pnl` at fund_manager.py:1736.
        The defensible claim is narrower — the value is read AFTER Phase 2 (:1703-1711) and
        AFTER the invariant check, and feeds ONLY a log field, never `_total`, a bucket, or the
        returned dict. Shared code path; no causal dependency.

        Proven by making the reader return an absurd value across the whole seed+rehydrate
        sequence and asserting `_total` still lands exactly on broker.net.

        ⭐ ANTI-VACUITY, and it is the whole point: the patched reader must actually be CALLED.
        Were it never called, this test would pass trivially and prove nothing — which is
        precisely how the assertion it replaces went wrong (that one pinned a numerical
        coincidence, `reader != carryover`, as a proxy for a structural property). The
        call-count assertion documents BOTH that §A2's claim was false AND that the value is
        irrelevant.

        CONTRACT-AGNOSTIC: the reader returns garbage either side of E4/W10.
        ⚠️ FALSIFIER #3 — fails the moment :1736's value feeds `_total`, a bucket, or the return
        dict.
        """
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)
        _drive_losing_close(ctx, "TCS", 2)

        ABSURD = -1.0e9
        calls = {"n": 0}
        real_reader = ctx.store.get_daily_realized_net_pnl

        def _absurd_reader(date_iso):
            calls["n"] += 1
            real_reader(date_iso)          # keep the real (side-effect-free) SELECT happening
            return ABSURD

        # Patch only around the seed + rehydrate sequence — i.e. exactly main.py:2255-2286.
        monkeypatch.setattr(ctx.store, "get_daily_realized_net_pnl", _absurd_reader)
        seed, carry = _live_seed(ctx, _StubBroker())
        fm, counts = _boot(ctx, seed)
        total_under_absurd_reader = fm.get_snapshot().total
        monkeypatch.undo()

        assert carry != 0.0, (
            "the carryover is ZERO — nothing was cancelled, so a corrupted reader could not "
            "have shown up either way"
        )
        # ANTI-VACUITY: patching a function nobody calls proves nothing.
        assert calls["n"] >= 1, (
            "the patched reader was NEVER CALLED during seed+rehydrate, so this test proves "
            "nothing about its influence. NOTE: §A2 asserted exactly this ('the seed and Phase "
            "2 never consult it') and it was false — rehydrate_from_open_trades:1736 calls it. "
            "If that call has since been removed, delete this test rather than trusting it."
        )
        assert counts["summary"]["replayed_pnl_rows"] >= 2, (
            "Phase 2 replayed nothing — there was no cancellation to corrupt"
        )
        assert total_under_absurd_reader == pytest.approx(BROKER_NET, abs=TOL), (
            f"THE READER'S VALUE REACHED THE CANCELLATION: with the daily-loss reader forced to "
            f"{ABSURD:.1f}, _total came out {total_under_absurd_reader:.4f} instead of "
            f"broker.net={BROKER_NET:.4f}. rehydrate_from_open_trades:1736 is no longer "
            f"log-only — falsifier #3 of the 20-Jul re-derivation. E4/W10's change to the "
            f"reader's contract can now disturb live seeding."
        )
        _assert_identity(fm, "live-seeded-under-absurd-reader")

    def test_both_sides_still_call_the_shared_helper(self, wired_system):
        """The structural guarantee, measured: one call from the carryover, one from Phase 2."""
        ctx = wired_system
        _drive_losing_close(ctx, "RELIANCE", 1)

        fm = _fresh_fm(ctx)
        calls = {"n": 0}
        real = fm._today_release_used_pnl_rows

        def _spy(s):
            calls["n"] += 1
            return real(s)

        fm._today_release_used_pnl_rows = _spy
        fm.today_realized_pnl_carryover()
        assert calls["n"] == 1, "the carryover must source its rows from the shared helper"

        fm.initialize(BROKER_NET)
        fm.rehydrate_from_open_trades()
        assert calls["n"] == 2, (
            f"Phase 2 must source the SAME rows from the SAME helper; total calls={calls['n']} "
            f"(expected 2). If Phase 2 now selects rows independently, the seed subtraction and "
            f"the re-addition can silently diverge."
        )
