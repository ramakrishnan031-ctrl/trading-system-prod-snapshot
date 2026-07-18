"""
tests/unit/test_signal_status_classification.py

THE TEST THAT WOULD HAVE CAUGHT THE ORIGINAL DEFECT.

`reports/daily_report.py:464` classified a rejection as CAPITAL by substring-matching the
free-text `rejection_reason`:

    rejected_capital = sum(1 for s in data.signals
                           if "CAPITAL" in (s.get("rejection_reason") or "").upper())

The CONCENTRATION reason embeds `capital_qty=`, so EVERY concentration rejection matched.
Measured on production data for 2026-07-10: **1,189 rejections reported as CAPITAL when the
true count was 0** -- the report told the operator the system was capital-starved while the
constraint that binds on 100% of trades was never named anywhere he would see it.

The strings below are the REAL ones, copied verbatim from the live database, not invented
approximations. A test built on a tidied-up string would not have caught this.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from reports import signal_status as sig_status

# Copied verbatim from signals.rejection_reason in the live DB.
REAL_CONCENTRATION_REASON = (
    "qty=0: CONCENTRATION exhausted for GRAVITA (risk_qty=6 capital_qty=17 conc_qty=0)"
)
REAL_CONCENTRATION_STATUS = "REJECTED_SIZING_CONCENTRATION"


class TestTheOriginalDefect:
    """The exact misclassification, both directions (rule B)."""

    def test_the_real_concentration_reason_contains_the_word_that_caused_the_bug(self):
        """Anti-vacuity: if this string ever stops containing 'capital', the rest of this
        class proves nothing. Assert the trap is still present before asserting we avoid it."""
        assert "CAPITAL" in REAL_CONCENTRATION_REASON.upper(), (
            "the real concentration reason no longer embeds 'capital_qty=' -- these tests "
            "would pass vacuously; re-derive them from current production data"
        )

    def test_a_concentration_rejection_is_not_counted_as_capital(self):
        """POSITIVE half: the defect itself."""
        assert sig_status.sizing_constraint(REAL_CONCENTRATION_STATUS) == "CONCENTRATION"
        assert sig_status.sizing_constraint(REAL_CONCENTRATION_STATUS) != "CAPITAL"

    def test_a_genuine_capital_rejection_is_counted_as_capital(self):
        """NEGATIVE half (rule B). Without this, a classifier that simply never says CAPITAL
        would pass the test above trivially."""
        assert sig_status.sizing_constraint("REJECTED_SIZING_CAPITAL") == "CAPITAL"

    def test_the_old_substring_logic_would_have_failed_this_test(self):
        """Pins WHY the fix is a fix: the retired predicate misclassifies the real string,
        the new one does not. If someone reintroduces substring matching, the contrast this
        documents disappears."""
        old_says_capital = "CAPITAL" in REAL_CONCENTRATION_REASON.upper()
        new_says_capital = sig_status.sizing_constraint(REAL_CONCENTRATION_STATUS) == "CAPITAL"
        assert old_says_capital is True, "the old logic no longer reproduces the defect"
        assert new_says_capital is False, "the new logic reproduces the defect"


class TestFamilyGrouping:
    """The second half of the same defect: grouping by free text fragmented one rejection
    class into 190 lines because the reason embeds the symbol and three arm values."""

    def test_per_score_statuses_collapse_to_one_family(self):
        for s in ("REJECTED_SCORE_29", "REJECTED_SCORE_53", "REJECTED_SCORE_59"):
            assert sig_status.family(s) == "REJECTED_SCORE", s

    def test_unrelated_statuses_are_not_collapsed(self):
        """The collapse must be surgical -- it must not merge distinct constraints."""
        assert sig_status.family("REJECTED_SIZING_CONCENTRATION") == "REJECTED_SIZING_CONCENTRATION"
        assert sig_status.family("REJECTED_SIZING_CAPITAL") == "REJECTED_SIZING_CAPITAL"
        assert sig_status.family("REJECTED_DAILY_TRADES") != sig_status.family("REJECTED_OPEN_POSITIONS")

    def test_grouping_the_real_reason_strings_fragments_but_status_does_not(self):
        """Reproduces the fragmentation in miniature: three signals, same cause."""
        rows = [
            {"status": REAL_CONCENTRATION_STATUS,
             "rejection_reason": "qty=0: CONCENTRATION exhausted for GRAVITA (risk_qty=6 capital_qty=17 conc_qty=0)"},
            {"status": REAL_CONCENTRATION_STATUS,
             "rejection_reason": "qty=0: CONCENTRATION exhausted for MAPMYINDIA (risk_qty=12 capital_qty=32 conc_qty=0)"},
            {"status": REAL_CONCENTRATION_STATUS,
             "rejection_reason": "qty=0: CONCENTRATION exhausted for GRAVITA (risk_qty=2 capital_qty=18 conc_qty=0)"},
        ]
        by_reason = {r["rejection_reason"] for r in rows}
        by_family = {sig_status.family(r["status"]) for r in rows}
        assert len(by_reason) == 3, "the sample no longer fragments; pick distinct real strings"
        assert len(by_family) == 1, "grouping by status family must collapse one cause to one line"


class TestSilentDeadAndDedup:
    """Two more sites of the same class, both measurably wrong before the fix."""

    @pytest.mark.parametrize("status", [
        "DROPPED_DEDUP", "SKIPPED_QUOTE_UNAVAILABLE", "QUEUE_FULL",
        "PLACEMENT_FAILED", "TIMEOUT",
    ])
    def test_terminal_non_rejected_statuses_are_not_silently_dead(self, status):
        """`silent_dead` counted only "REJECTED" in status, so 2,688 signals carrying an
        explicit DROPPED_/SKIPPED_/etc. disposition were reported as having vanished."""
        assert sig_status.has_explicit_disposition(status), (
            f"{status} carries an explicit outcome and must not count as silently dead"
        )

    def test_a_genuinely_unmapped_status_is_silent(self):
        """NEGATIVE half: the concept must still be able to fire, or the fix has just
        redefined 'silent dead' to mean nothing."""
        assert not sig_status.has_explicit_disposition("SOME_NEW_UNMAPPED_STATUS")
        assert sig_status.bucket("SOME_NEW_UNMAPPED_STATUS") == "unmapped"

    def test_dedup_duplicate_is_distinguished_from_duplicate_symbol_rejection(self):
        """`"DUPLICATE" not in status` conflated two different dispositions: the dedup drop,
        and a rejection because an active position already exists on that symbol."""
        assert sig_status.is_dedup_duplicate("DUPLICATE")
        assert not sig_status.is_dedup_duplicate("REJECTED_DUPLICATE_SYMBOL")
        assert sig_status.is_rejected("REJECTED_DUPLICATE_SYMBOL")


class TestOneImplementation:
    """Anti-duplication (Rule #4): daily_trade_review had the right pattern already, so the
    fix promoted it rather than adding a second classifier."""

    def test_daily_trade_review_delegates_to_the_shared_module(self):
        from reports import daily_trade_review as dtr
        assert dtr._signal_bucket is sig_status.bucket, (
            "daily_trade_review has its own signal-status classifier again -- there must be "
            "exactly ONE implementation (reports/signal_status.py)"
        )


class TestNoReintroduction:
    """Source guard, batch-4 pattern: fail if free-text classification returns to the
    reporting layer.

    Scoped precisely: RENDERING rejection_reason is correct and expected (Sheet 1 shows it per
    signal). What must not come back is BRANCHING on its contents -- a membership test against
    the reason string.
    """

    def test_no_substring_classification_on_rejection_reason_in_reports(self):
        root = Path(__file__).resolve().parents[2] / "reports"
        # e.g.  "CAPITAL" in (s.get("rejection_reason") or "").upper()
        pattern = re.compile(
            r'["\'][A-Za-z_]+["\']\s+(?:not\s+)?in\s+[^\n]{0,80}rejection_reason'
        )
        offenders = []
        for py in sorted(root.rglob("*.py")):
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                # Comments are documentation, not classification -- the fix deliberately
                # QUOTES the retired predicate to explain what it cost.
                if line.lstrip().startswith("#"):
                    continue
                if pattern.search(line):
                    offenders.append(f"{py.name}:{i}: {line.strip()}")
        assert offenders == [], (
            "free-text classification on rejection_reason has been reintroduced in the "
            "reporting layer. Classify by the structured `status` "
            "(reports/signal_status.py); render the reason text as detail only.\n"
            + "\n".join(offenders)
        )

    def test_the_guard_would_catch_the_original_line(self):
        """Anti-vacuity: prove the regex actually matches the historical defect, or the guard
        above is decorative."""
        original = ('    rejected_capital = sum(1 for s in data.signals '
                    'if "CAPITAL" in (s.get("rejection_reason") or "").upper())')
        pattern = re.compile(
            r'["\'][A-Za-z_]+["\']\s+(?:not\s+)?in\s+[^\n]{0,80}rejection_reason'
        )
        assert pattern.search(original), "the guard would NOT have caught the original defect"
