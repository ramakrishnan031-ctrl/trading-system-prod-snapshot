"""
Capital vocabulary in the INTRADAY SIGNAL alert (04-Aug-2026).

WHAT WENT WRONG, so a future reader does not "simplify" it back:

    capital_at_risk = entry_price * qty          # <- position NOTIONAL
    risk_pct = risk_amt / capital_at_risk * 100  # <- (entry-SL)/entry
    "... | Risk: Rs8.86 (1.5%) | ..."

The rupee figure was correct -- it really is the money at risk. The PERCENTAGE
was not what it appeared to be: `qty` cancels in that ratio, so it is identically
the SL distance as a fraction of entry, AT EVERY QUANTITY. It was rendered beside
the word "Risk", where every reader parses a percentage as "% of my capital". At
risk_per_trade_pct = 1% of TOTAL capital, the true account figure is ~1% and has
no relationship to the number displayed.

⛔ These tests assert the CLAIM, not the string. Pinning the exact body would go
RED on a harmless rewording and GREEN on a reworded-but-still-wrong one -- the
ledger-#9 lesson. The load-bearing test is
`test_percentage_is_invariant_to_quantity`: that invariance is the algebraic
PROOF the number cannot be a fraction of capital, because a fraction of capital
must scale with position size.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from signals.signal_processor import SignalProcessor


class _CapNotifier:
    def __init__(self) -> None:
        self.kw: dict = {}

    def send(self, **kw) -> None:
        self.kw = kw


def _emit(*, entry: float, sl: float, tgt: float, qty: int,
          direction: str = "LONG") -> str:
    cap = _CapNotifier()

    class _Stub:
        _mode = "LIVE"
        _notifier = cap
        _log = logging.getLogger("test")

    SignalProcessor._emit_signal_alert(
        _Stub(), symbol="TESTSYM", strategy_name="unit_test_strategy",
        score=61, entry_price=entry, sl_price=sl, tgt_price=tgt,
        qty=qty, direction=direction,
    )
    return cap.kw["body"]


def _pct_in(body: str) -> float:
    """The single percentage rendered on the Risk line."""
    m = re.search(r"SL is ([0-9.]+)% from entry", body)
    assert m, f"no SL-distance percentage found in body:\n{body}"
    return float(m.group(1))


def _rs_after(body: str, label: str) -> float:
    m = re.search(rf"{label}: ₹([0-9,]+\.[0-9]{{2}})", body)
    assert m, f"no '{label}' rupee figure in body:\n{body}"
    return float(m.group(1).replace(",", ""))


# ── the property the old code got wrong ───────────────────────────────────────

def test_percentage_is_the_sl_distance_not_a_fraction_of_capital() -> None:
    # entry 400, SL 380 -> SL sits 5.0% from entry.
    body = _emit(entry=400.0, sl=380.0, tgt=440.0, qty=3)
    assert abs(_pct_in(body) - 5.0) < 0.05
    print("  OK the rendered % is (entry-SL)/entry, i.e. the SL distance")


def test_percentage_is_invariant_to_quantity() -> None:
    """⭐ THE LOAD-BEARING TEST.

    A percentage OF CAPITAL must grow as the position grows. This one does not
    move at all across a 100x change in qty -- which is the proof it is a
    property of the LEVELS, never of the account. If someone reintroduces a
    capital-relative figure under this label, this test goes RED.
    """
    pcts = {_pct_in(_emit(entry=400.0, sl=380.0, tgt=440.0, qty=q))
            for q in (1, 7, 100)}
    assert len(pcts) == 1, f"percentage moved with qty: {pcts}"
    print("  OK the % is invariant to qty => it cannot be a fraction of capital")


def test_the_percentage_is_never_a_bare_number_beside_the_word_risk() -> None:
    """The defect was a percentage sitting unqualified next to "Risk"."""
    body = _emit(entry=400.0, sl=380.0, tgt=440.0, qty=3)
    m = re.search(r"Risk: ₹[0-9,.]+ \(([^)]*)\)", body)
    assert m, f"no parenthetical beside Risk:\n{body}"
    qualifier = m.group(1)
    assert "SL" in qualifier, (
        f"the parenthetical beside Risk must name what the % measures; got {qualifier!r}"
    )
    print("  OK the % beside Risk is explicitly qualified as the SL distance")


# ── the two figures, each labelled for what it holds ──────────────────────────

def test_risk_rupees_is_the_account_money_at_risk() -> None:
    body = _emit(entry=400.0, sl=380.0, tgt=440.0, qty=3)
    assert abs(_rs_after(body, "Risk") - 60.0) < 0.01   # 3 x (400-380)
    print("  OK Risk Rs = qty x (entry - SL)")


def test_exposure_is_rendered_and_is_the_position_notional() -> None:
    body = _emit(entry=400.0, sl=380.0, tgt=440.0, qty=3)
    assert abs(_rs_after(body, "Exposure") - 1200.0) < 0.01   # 3 x 400
    print("  OK Exposure Rs = qty x entry (notional), and it is shown")


def test_exposure_and_risk_are_different_numbers_and_both_appear() -> None:
    """They were conflated by ONE name; the fix is that both exist, separately."""
    body = _emit(entry=400.0, sl=380.0, tgt=440.0, qty=3)
    assert _rs_after(body, "Exposure") != _rs_after(body, "Risk")
    print("  OK notional and account-risk are shown as two distinct figures")


# ── direction: the SL is ABOVE entry on a SHORT ───────────────────────────────

def test_short_trade_percentage_is_positive_and_wording_is_direction_neutral() -> None:
    """SL above entry must not render a negative %, nor claim SL is 'below' entry."""
    body = _emit(entry=400.0, sl=420.0, tgt=360.0, qty=3, direction="SHORT")
    assert abs(_pct_in(body) - 5.0) < 0.05
    assert "below entry" not in body, "wording must hold for SHORT (SL sits above entry)"
    print("  OK SHORT: % positive, wording direction-neutral")


# ── the vocabulary rule itself ────────────────────────────────────────────────

def test_the_misleading_identifier_is_gone_from_the_source() -> None:
    """`capital_at_risk` held the NOTIONAL. The name is the defect; pin it out.

    Width stated: this checks the one module that defined it. The repo-wide sweep
    on 04-Aug found the identifier in exactly two code lines, both here.
    """
    src = Path(__file__).resolve().parents[2] / "signals" / "signal_processor.py"
    text = src.read_text(encoding="utf-8")
    code = "\n".join(
        ln for ln in text.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "capital_at_risk" not in code, (
        "`capital_at_risk` names the position notional, not capital at risk -- "
        "do not reintroduce it"
    )
    print("  OK the misleading identifier does not survive in code")
