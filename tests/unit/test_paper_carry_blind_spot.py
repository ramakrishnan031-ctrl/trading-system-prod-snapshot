"""
tests/unit/test_paper_carry_blind_spot.py — the paper run announcing what it cannot prove.

WHY THIS EXISTS. Paper's positions, holdings and GTTs are in-memory dicts on the
adapter and die with the nightly restart; `gtt_state` is a real table and survives,
by design. So the morning after a paper CNC carry, `_gather()` returns
empty/empty/empty against a live ACTIVE row, the K6 ladder falls to rung 4's
"GTT gone + flat -> the GTT did its job" branch, and `_finalize_gtt_exit` books a
P&L at today's LTP, releases the delivery reservation, publishes PositionClosed and
marks the row CLEANED.

Every one of those artefacts is identical to a real carried exit. That is not weak
evidence — it is ACTIVE MISINFORMATION about the one path that holds capital
overnight, and "paper-proven" is the literal gate on delivery going live. Silence is
the dangerous version, so the run says it.

Clock-robust on purpose (the 26-Jul clock-dependency class): "carried" is asserted
with a date far in the past and "same-day" is computed from the same clock the code
reads, so neither assertion can rot into a time-bomb.
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

from core.time_authority import now_ist
from orders.cnc_gtt_monitor import CncGttMonitor
from tests.unit.test_cnc_gtt_monitor import (
    _FM, _KS, _Bus, _Notifier, _place, _seed_trade, _setup,
)

_LOG = logging.getLogger("test_paper_carry_blind_spot")
_LONG_AGO = "2020-01-02T10:00:00+05:30"


def _backdate(store, gtt_id, stamp: str) -> None:
    """Make an ACTIVE gtt_state row look like it was written on another day."""
    with store.transaction() as cur:
        cur.execute("UPDATE gtt_state SET created_at = ? WHERE gtt_id = ?",
                    (stamp, int(gtt_id)))


def _today_stamp() -> str:
    return now_ist().date().isoformat() + "T10:00:00+05:30"


def _live_monitor(env):
    """The SAME store, adapter, placer and rows — only the mode differs. That is
    what makes the mode gate the single variable."""
    return CncGttMonitor(
        store=env.store, adapter=env.adapter, placer=env.placer,
        fund_manager=_FM(), kill_switch=_KS(), notifier=_Notifier(), bus=_Bus(),
        logger=_LOG, mode="LIVE", market_hours_fn=lambda: True)


def _announcements(notifier):
    return [s for s in notifier.sent if "OVERNIGHT CARRY" in s[1]]


# ── it fires on a carry ──────────────────────────────────────────────────────

def test_it_announces_the_blind_spot_on_a_carried_row(tmp_path: Path):
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _LONG_AGO)
    # the restart: the paper broker's in-memory stores are gone, the DB row is not
    env.adapter._paper_gtts.clear()

    out = env.mon.reconcile()

    assert "paper_carry_blind_spot:1" in out
    announced = _announcements(env.notifier)
    assert len(announced) == 1
    severity, title, _src = announced[0]
    assert severity == "CRITICAL"
    assert "[PAPER]" in title


def test_the_announcement_says_what_to_expect_and_where_the_real_proof_lives(tmp_path: Path):
    """The body has to name the misleading artefact BY NAME, or a reader who sees a
    tidy GTT_EXIT ten seconds later will not connect the two."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _LONG_AGO)
    env.adapter._paper_gtts.clear()

    # _Notifier deliberately keeps only (severity, title, source); wrap it so the
    # BODY is inspectable without changing a fixture other tests share.
    sent = []
    real_send = env.notifier.send

    def _capture(**kw):
        sent.append(kw)
        return real_send(**kw)

    env.notifier.send = _capture
    env.mon.reconcile()
    text = "\n".join(k["body"] for k in sent if "OVERNIGHT CARRY" in k["title"])

    assert text, "premise: the announcement was sent"
    assert "NOT EVIDENCE" in text
    assert "GTT_EXIT" in text
    assert "RAMCOIND" in text
    assert "healthy:<symbol>" in text, "it must name the live pair's PASS condition"
    assert "paper_overnight_carry_26jul2026.md" in text


def test_it_fires_BEFORE_the_misleading_exit_not_after(tmp_path: Path):
    """Ordering is the whole point: the announcement has to reach the reader before
    the tidy-looking GTT exit does, not as a footnote to it."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _LONG_AGO)
    env.adapter._paper_gtts.clear()

    env.mon.reconcile()
    titles = [t for _s, t, _m in env.notifier.sent]
    announce_at = next(i for i, t in enumerate(titles) if "OVERNIGHT CARRY" in t)
    exit_at = next(i for i, t in enumerate(titles) if "GTT exit" in t)
    assert announce_at < exit_at, titles


def test_it_does_NOT_change_what_the_reconcile_then_does(tmp_path: Path):
    """⛔ Announce, do not diverge. Making paper behave differently from live here
    would be its own parity lie — the fix for the gap is a live pair, not a
    settlement model. So the fabricated GTT_EXIT still happens, loudly labelled."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _LONG_AGO)
    env.adapter._paper_gtts.clear()

    out = env.mon.reconcile()

    assert "gtt_exit:RAMCOIND" in out
    assert env.store.fetch_one(
        "SELECT status FROM trades WHERE trade_id = 't1'")["status"] == "CLOSED"
    assert len(env.fm.releases) == 1


# ── it stays silent everywhere else ──────────────────────────────────────────

def test_it_is_SILENT_on_a_same_day_row(tmp_path: Path):
    """The other direction. A row written today is not a carry, so an ordinary
    intraday paper session must never see this."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _today_stamp())
    env.adapter._paper_gtts.clear()

    out = env.mon.reconcile()

    assert not [a for a in out if a.startswith("paper_carry_blind_spot")]
    assert _announcements(env.notifier) == []
    assert "gtt_exit:RAMCOIND" in out, "premise: the same code path still ran"


def test_it_is_SILENT_in_LIVE_with_the_IDENTICAL_carried_row(tmp_path: Path):
    """C2: paper-only BY CONSTRUCTION. Same store, same adapter, same backdated row
    — only `mode` differs, so the mode gate is the single variable. It is a mode
    check and not a config flag precisely so nobody can switch this on in live."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _LONG_AGO)
    env.adapter._paper_gtts.clear()

    live = _live_monitor(env)
    out = live.reconcile()

    assert not [a for a in out if a.startswith("paper_carry_blind_spot")]
    assert _announcements(live._notifier) == []


def test_it_is_SILENT_when_there_are_no_rows_at_all(tmp_path: Path):
    """Today's actual state: delivery is off, so the reconcile loops over an empty
    collection. This must add nothing to a normal paper day."""
    env = _setup(tmp_path)
    assert env.mon.reconcile() == []
    assert _announcements(env.notifier) == []


def test_a_blank_created_at_is_NOT_read_as_carried(tmp_path: Path):
    """Fail CLOSED on an unreadable stamp. A missing timestamp is not evidence of a
    carry, and inventing one would put a CRITICAL on a normal day."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, "")
    env.adapter._paper_gtts.clear()

    out = env.mon.reconcile()
    assert not [a for a in out if a.startswith("paper_carry_blind_spot")]


def test_it_announces_ONCE_a_day_even_if_the_row_survives_the_pass(tmp_path: Path):
    """A needs_review latch keeps a row ACTIVE across cycles, and the 15-minute
    cadence would otherwise re-announce every pass. Same once-per-thing discipline
    as the FIX-183 adoption warning next to it."""
    env = _setup(tmp_path)
    _seed_trade(env.store, "t1")
    res = _place(env)
    _backdate(env.store, res.gtt_id, _LONG_AGO)
    with env.store.transaction() as cur:
        cur.execute("UPDATE gtt_state SET needs_review = 1 WHERE gtt_id = ?",
                    (int(res.gtt_id),))
    env.adapter._paper_gtts.clear()

    first = env.mon.reconcile()
    second = env.mon.reconcile()

    assert "paper_carry_blind_spot:1" in first
    assert "paper_carry_blind_spot_seen:1" in second, (
        "the second pass must still REPORT the condition, just not re-alert it")
    assert len(_announcements(env.notifier)) == 1
