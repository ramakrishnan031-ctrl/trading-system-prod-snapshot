"""core/evidence_contract.py — THE FORWARD EVIDENCE CONTRACT (Batch 1, 10-Sep-2026).

Instrumentation ONLY. ⛔ Nothing here may change a single trading decision. The
recorder is an OBSERVER: it is wrapped so it can never raise into the pipeline,
exactly like `signal_processor._sr_observe` and `v3_chain.runner.observe`.

WHY IT EXISTS. Every trading day without it is a day of forward data that cannot
be recovered. `signals.status` keeps a terminal verdict and `screener_results`
keeps the screening detail, but neither preserves the *decision context* at the
moment of the decision — and 68.99 % of the corpus is a screener reject whose
sizing, re-anchoring and provenance are never written down at all.

═══════════════════════════════════════════════════════════════════════════════
THREE THINGS THIS MODULE REFUSES TO DO
═══════════════════════════════════════════════════════════════════════════════
1. ⛔ NO LOOKAHEAD, NO BACKFILL. A record carries only what was known at the
   capture point. Nothing is reconstructed from a later state, and a gap stays a
   gap: a missing day is a loss, a manufactured day is a corruption.
2. ⛔ NO GIT REF IN THE PROVENANCE. `scripts/forward_shadow_record.py:71-72`
   asks git for HEAD and, because a deployed tree has no `.git`, falls through to
   the BARE repo — stamping `20061b6` onto rows produced by 27 hand-patched
   files. Measured. `code_fingerprint` here hashes the DEPLOYED BYTES instead.
3. ⛔ NO INVENTED IDENTITY. If the arm cannot be resolved, or the fingerprint
   cannot be computed, the record is `FAILED` and says so. It is never quietly
   defaulted and never hidden behind `PARTIAL`.

═══════════════════════════════════════════════════════════════════════════════
record_status — AND WHY PARTIAL IS NARROW
═══════════════════════════════════════════════════════════════════════════════
COMPLETE  every required field present, and every optional field supplied.
PARTIAL   every required field present; some OPTIONAL CONTEXT absent.
FAILED    a REQUIRED identity/provenance field is missing or unresolvable.

⚠️ PARTIAL is for missing optional context ONLY. A missing identity or
provenance field is a FAILURE and must be visible. Widening PARTIAL to cover it
would make the corpus look healthier than it is, which is the one thing an
evidence contract must never do.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Contract identity
# ─────────────────────────────────────────────────────────────────────────────

#: Bumped when the FIELD SET or a field's MEANING changes. A reader may assume
#: rows sharing a contract_version are directly comparable.
CONTRACT_VERSION: str = "1"

#: ⚠️ A GENERATION MARKER, ⛔ NOT a timestamp alias and ⛔ NOT "today".
#: It partitions the corpus into runs that are comparable *as measurements*. It
#: is bumped by a DELIBERATE HUMAN DECISION when the thing being measured
#: changes underneath the contract -- e.g. when the score ceiling is repaired,
#: rows from before and after must not be pooled. Two rows with the same
#: evidence_epoch were produced by the same measurement regime; two rows with
#: different epochs were not, however close their timestamps.
EVIDENCE_EPOCH: str = "E1"

# capture points
P1_ACCEPT: str = "P1_ACCEPT"
P2_REJECT: str = "P2_REJECT"
P3_SCREEN: str = "P3_SCREEN"
CAPTURE_POINTS: frozenset = frozenset({P1_ACCEPT, P2_REJECT, P3_SCREEN})

# record_status
COMPLETE: str = "COMPLETE"
PARTIAL: str = "PARTIAL"
FAILED: str = "FAILED"

#: Identity + provenance. Absent or unresolvable => FAILED, never PARTIAL.
REQUIRED_FIELDS: Tuple[str, ...] = (
    "contract_version", "evidence_epoch", "capture_point", "arm",
    "signal_id", "ts", "captured_at", "code_fingerprint", "config_hashes",
)

#: ⚠️ PER-CAPTURE-POINT IDENTITY. These are NOT optional context: at these
#: points the value is bound at every call site, so an absent one means the
#: wiring is broken, not that the day was quiet. Absent => FAILED + sentinel,
#: ⛔ never PARTIAL, because PARTIAL is where nobody looks.
#: ⛔ P2_REJECT is deliberately NOT here yet: two of its five sites read
#: `getattr(candidate, "symbol", None)`, whose shape is not guaranteed, and a
#: flood of FAILED rows would bury the sentinel it is supposed to raise.
#: Tightening P2 is an OPEN item for the next round.
REQUIRED_BY_CAPTURE_POINT: Dict[str, Tuple[str, ...]] = {
    P1_ACCEPT: ("symbol", "strategy"),
    P3_SCREEN: ("symbol", "strategy"),
}

#: Decision context. Absent => PARTIAL. These are the fields whose absence is a
#: gap in what we know, not a defect in who we are.
OPTIONAL_FIELDS: Tuple[str, ...] = (
    "symbol", "strategy", "triggered_at", "status", "reject_reason",
    "rejected_step", "score_total", "tier", "step_results", "step_statuses",
    "trigger_price", "entry_price_final", "reanchored", "sl_price", "tgt_price",
    "qty", "sizing_breakdown", "binding_constraint", "market_data_snapshot",
    "trade_id",
)

# ─────────────────────────────────────────────────────────────────────────────
# §6.4 — the code fingerprint, from the DEPLOYED BYTES
# ─────────────────────────────────────────────────────────────────────────────
#
# THE ARTEFACT SET IS NAMED HERE SO IT CAN BE AUDITED. It is not a guess: it is
# the MEASURED first-party import closure of the decision path -- the packages
# imported by signal_processor, secondary_screener, order_placer, entry_gate,
# retest_monitor and position_sizer -- plus main.py, which wires them.
#
# ⛔ DELIBERATELY EXCLUDED, because none of them can change a signal's outcome:
#    tests/ (403 .py) · scripts/ (84) · ops_dashboard/ (93) · v3_chain/ (shadow
#    only) · regime/ · data/ · utils/ · tasks/. Excluding them keeps the
#    fingerprint from churning on work that cannot have produced the row.
#
# ⛔ NO .pyc: `__pycache__` is skipped. A .pyc is a build artefact whose bytes
#    depend on the interpreter, not on what we deployed.
# ⛔ NO volatile files: only `.py`, never logs, DBs, configs (config has its own
#    hashes -- see §6.5) or anything written at runtime.
#
# ORDERING IS PART OF THE CONTRACT: files are sorted by POSIX relative path, and
# each contributes `relpath \0 length \0 bytes`. The path and length are hashed
# alongside the content so a rename cannot collide with an edit, and so two
# files cannot be made to look like one by shifting a boundary between them.
_FINGERPRINT_PACKAGES: Tuple[str, ...] = (
    "alerts", "allocation", "broker", "capital", "core",
    "orders", "screening", "signals", "sr_detector", "strategies",
)
_FINGERPRINT_EXTRA_FILES: Tuple[str, ...] = ("main.py",)


def compute_code_fingerprint(root: Path) -> Tuple[Optional[str], int]:
    """Hash the deployed artefact set. Returns (hexdigest, file_count).

    Returns (None, 0) on ANY failure -- a fingerprint that cannot be computed is
    absent, ⛔ never approximated. The caller turns that into a FAILED record.

    Measured cost on the deployed tree: 109 files, ~2.85 MB, under 20 ms. It is
    computed ONCE per process and cached by EvidenceRecorder; ⛔ never per row.
    """
    try:
        paths = []
        for pkg in _FINGERPRINT_PACKAGES:
            d = root / pkg
            if not d.is_dir():
                continue
            for p in d.rglob("*.py"):
                if "__pycache__" in p.parts:
                    continue
                paths.append(p)
        for extra in _FINGERPRINT_EXTRA_FILES:
            p = root / extra
            if p.is_file():
                paths.append(p)
        if not paths:
            return (None, 0)
        rel = sorted(p.relative_to(root).as_posix() for p in paths)
        h = hashlib.sha256()
        for r in rel:
            data = (root / r).read_bytes()
            h.update(r.encode("utf-8"))
            h.update(b"\x00")
            h.update(str(len(data)).encode("ascii"))
            h.update(b"\x00")
            h.update(data)
        return (h.hexdigest(), len(rel))
    except Exception:  # noqa: BLE001
        return (None, 0)


# ─────────────────────────────────────────────────────────────────────────────
# §6.8 / §6.9 — storage
# ─────────────────────────────────────────────────────────────────────────────
#
# data_store/evidence/signal_evidence_<ARM>_<YYYY-MM-DD>.jsonl
#
# ⭐ THE ARM IS IN THE FILENAME, and that is not decoration. Both machines write
# to the same relative path. This project has already copied one machine's whole
# disk onto another, and the testing VM still carries production's inherited
# data. If two arms' files are ever merged or copied, an `arm` FIELD lets you
# separate the rows only if you still trust the file they came from -- the
# filename is what stops them being poured into one bucket in the first place.
# The field protects the ANALYSIS; the filename protects the FILE.
#
# ⭐ DATED, unlike the `data_store/v3/*.jsonl` precedent, whose undated
# forward_shadow file has reached 100 MB. A date makes the artefact rankable by
# retention and selectable by backup without reading it.
EVIDENCE_DIRNAME: str = "evidence"
EVIDENCE_FILE_PREFIX: str = "signal_evidence"


def evidence_path(root: Path, arm: str, on: date) -> Path:
    return (root / "data_store" / EVIDENCE_DIRNAME
            / f"{EVIDENCE_FILE_PREFIX}_{arm}_{on.isoformat()}.jsonl")


# ─────────────────────────────────────────────────────────────────────────────
# §6.7 — the CRITICAL sink, bound to the system's own alert officer
# ─────────────────────────────────────────────────────────────────────────────

def notifier_critical_sink(notifier: Any) -> Callable[[str, str], None]:
    """Bind the recorder's once-a-day CRITICAL to `TelegramNotifier.send`.

    ⚠️ `source_module` is a REQUIRED positional parameter of
    `TelegramNotifier.send(severity, title, body, source_module, ...)`. The first
    build wired a hand-rolled lambda in main.py that omitted it, so every call
    raised TypeError -- which the recorder swallows by design. The §6.7 sentinel
    could therefore never fire in production, while every test (all of them used
    a fake sink) stayed green. This helper exists so the REAL signature is
    exercised by a test against a REAL notifier.

    CRITICAL writes the sentinel FIRST (TG5), so alert_watcher still emails it
    when Telegram is down.
    """
    def _sink(state: str, detail: str) -> None:
        notifier.send(
            severity="CRITICAL",
            title=f"[EVIDENCE] {state}",
            body=detail,
            source_module="evidence_contract",
        )
    return _sink


# ─────────────────────────────────────────────────────────────────────────────
# §6.7 — observer failure accounting
# ─────────────────────────────────────────────────────────────────────────────

class _FailureLedger:
    """First failure of the day is LOUD; the rest are COUNTED.

    The shape is borrowed from `order_reconciler._should_alert_for_discrepancy`
    -- alert once, then suppress -- because 3,850 signals a day means a broken
    recorder would otherwise produce an alert storm that buries the one alert
    that mattered. ⛔ Only the PATTERN is borrowed; no trading logic comes with it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: Optional[str] = None
        self.total: int = 0
        self.first_ts: Optional[str] = None
        self.last_ts: Optional[str] = None
        self.capture_points: Dict[str, int] = {}
        self.classes: Dict[str, int] = {}

    def record(self, day: str, ts: str, capture_point: str, cls: str) -> bool:
        """Register a failure. Returns True iff this is the day's FIRST (=> alert)."""
        with self._lock:
            if self._day != day:          # day/epoch boundary: reset
                self._day = day
                self.total = 0
                self.first_ts = None
                self.last_ts = None
                self.capture_points = {}
                self.classes = {}
            self.total += 1
            self.last_ts = ts
            self.capture_points[capture_point] = self.capture_points.get(capture_point, 0) + 1
            self.classes[cls] = self.classes.get(cls, 0) + 1
            if self.total == 1:
                self.first_ts = ts
                return True
            return False

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "day": self._day,
                "total_failures": self.total,
                "first_ts": self.first_ts,
                "last_ts": self.last_ts,
                "capture_points": dict(self.capture_points),
                "failure_classes": dict(self.classes),
            }


# ─────────────────────────────────────────────────────────────────────────────
# The recorder
# ─────────────────────────────────────────────────────────────────────────────

class EvidenceRecorder:
    """Append-only forward evidence. NEVER raises into the pipeline.

    Parity with the two existing observers (`_sr_observe`, `v3_chain.observe`):
    every public entry point is wrapped, and a failure is logged, never
    propagated. What it adds, which neither of those has, is §6.7's accounting:
    the first failure of the day emits ONE critical sentinel, the rest are
    counted, and the state resets at the day boundary.
    """

    def __init__(
        self,
        root: Path,
        *,
        config_hashes: Optional[Dict[str, str]] = None,
        logger: Any = None,
        critical_sink: Optional[Callable[[str, str], None]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
        arm_fn: Optional[Callable[[], str]] = None,
        enabled: bool = True,
    ) -> None:
        self._root = Path(root)
        self._log = logger
        self._critical_sink = critical_sink
        self._enabled = bool(enabled)
        self._write_lock = threading.Lock()
        self._failures = _FailureLedger()
        self.records_written = 0

        if now_fn is not None:
            self._now = now_fn
        else:
            from core.time_authority import now_ist  # local import (file convention)
            self._now = now_ist

        # §6.6: ONE implementation of the arm, and no default. If it cannot
        # resolve, every record is FAILED and says so -- an invented arm would
        # silently merge two machines' corpora.
        if arm_fn is not None:
            self._arm_fn = arm_fn
        else:
            def _default_arm() -> str:
                from core.account_registry import primary_account_tag
                return primary_account_tag()
            self._arm_fn = _default_arm

        # §6.5: reuse AppConfig.file_hashes. ⛔ No second hashing scheme.
        self._config_hashes = dict(config_hashes) if config_hashes else None

        # computed ONCE, cached
        self._fingerprint, self._fingerprint_files = compute_code_fingerprint(self._root)

    # ── introspection (used by tests and the daily summary) ──────────────────

    @property
    def code_fingerprint(self) -> Optional[str]:
        return self._fingerprint

    @property
    def fingerprint_file_count(self) -> int:
        return self._fingerprint_files

    def failure_summary(self) -> Dict[str, Any]:
        return self._failures.summary()

    # ── the capture entry point ──────────────────────────────────────────────

    def capture(self, capture_point: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Emit ONE evidence record. ⛔ NEVER raises. ⛔ Never blocks trading.

        A failure anywhere below is swallowed, counted and (once a day) alerted.
        The pipeline neither knows nor cares.
        """
        if not self._enabled:
            return
        try:
            self._capture_inner(capture_point, payload or {})
        except Exception as exc:  # noqa: BLE001
            self._on_failure(capture_point, exc)

    # ── internals ────────────────────────────────────────────────────────────

    def _resolve_arm(self) -> Optional[str]:
        try:
            arm = self._arm_fn()
        except Exception:  # noqa: BLE001
            return None
        arm = str(arm).strip() if arm is not None else ""
        return arm or None

    def _build_record(
        self, capture_point: str, payload: Dict[str, Any], now: datetime,
    ) -> Dict[str, Any]:
        arm = self._resolve_arm()
        captured_at = now.isoformat()

        rec: Dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "evidence_epoch": EVIDENCE_EPOCH,
            "capture_point": capture_point if capture_point in CAPTURE_POINTS else None,
            "arm": arm,
            "signal_id": payload.get("signal_id") or None,
            # `ts` is the DECISION's own time when the caller supplies one, and
            # the capture time otherwise -- they differ, and conflating them is
            # how a corpus starts lying about latency.
            "ts": payload.get("ts") or captured_at,
            "captured_at": captured_at,
            "code_fingerprint": self._fingerprint,
            "code_fingerprint_files": self._fingerprint_files,
            "config_hashes": self._config_hashes,
        }
        for f in OPTIONAL_FIELDS:
            rec[f] = payload.get(f)

        required = REQUIRED_FIELDS + REQUIRED_BY_CAPTURE_POINT.get(capture_point, ())
        missing_required = [
            f for f in required
            if rec.get(f) in (None, "", {}) and f != "config_hashes"
        ]
        if not self._config_hashes:
            missing_required.append("config_hashes")

        if missing_required:
            rec["record_status"] = FAILED
            rec["missing_required"] = sorted(set(missing_required))
        else:
            _pt_required = REQUIRED_BY_CAPTURE_POINT.get(capture_point, ())
            missing_optional = [f for f in OPTIONAL_FIELDS
                                if rec.get(f) is None and f not in _pt_required]
            rec["record_status"] = PARTIAL if missing_optional else COMPLETE
            if missing_optional:
                rec["missing_optional"] = missing_optional
        return rec

    def _capture_inner(self, capture_point: str, payload: Dict[str, Any]) -> None:
        now = self._now()
        rec = self._build_record(capture_point, payload, now)

        # A FAILED record is still WRITTEN -- the corpus must show its own holes.
        # It is also counted and (once a day) alerted, because a run of FAILED
        # rows means the contract is broken, not that the day was quiet.
        if rec["record_status"] == FAILED:
            self._on_failure(
                capture_point,
                RuntimeError("missing required: " + ",".join(rec.get("missing_required", []))),
                already_built=True,
            )

        arm = rec.get("arm") or "UNKNOWN_ARM"
        path = evidence_path(self._root, arm, now.date())
        line = json.dumps(rec, default=str, ensure_ascii=False, sort_keys=True)
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self.records_written += 1

    def _on_failure(self, capture_point: str, exc: BaseException,
                    already_built: bool = False) -> None:
        try:
            now = self._now()
            ts = now.isoformat()
            day = now.date().isoformat()
        except Exception:  # noqa: BLE001
            ts, day = "", ""
        cls = type(exc).__name__
        first = self._failures.record(day, ts, capture_point, cls)

        if self._log is not None:
            try:
                if first:
                    self._log.error(
                        "evidence_contract: capture FAILED at %s (%s: %s) "
                        "-- first of the day; subsequent failures counted, not alerted",
                        capture_point, cls, exc,
                    )
                else:
                    self._log.warning(
                        "evidence_contract: capture failed at %s (%s) -- suppressed, "
                        "total today=%d", capture_point, cls, self._failures.total,
                    )
            except Exception:  # noqa: BLE001
                pass

        if first and self._critical_sink is not None:
            try:
                s = self._failures.summary()
                self._critical_sink(
                    "EVIDENCE_CAPTURE_FAILED",
                    f"evidence_contract: first capture failure of {day} at "
                    f"{capture_point} ({cls}: {exc}). Subsequent failures are "
                    f"counted, not alerted. epoch={EVIDENCE_EPOCH} "
                    f"contract={CONTRACT_VERSION} summary={s}",
                )
            except Exception:  # noqa: BLE001
                pass
        # ⛔ and nothing propagates. Trading is untouched. (already_built is
        # accepted so a FAILED record can be counted without a second write.)
