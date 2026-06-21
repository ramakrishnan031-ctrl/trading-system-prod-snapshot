"""
scripts/preflight/checks/vm_health.py -- Group 1 (VM Health).

First-cut checks: RAM, root disk, data disk, NTP drift. The low-level readers are
module-level functions so unit tests can monkeypatch them without a real Linux box
(pre-flight only RUNS on the VM; on the dev PC it is exercised via tests).

vm_disk_data + vm_ntp_sync are ported from scripts/premarket_healthcheck.py
(check_disk_space / check_clock_skew) -- see tests/unit/test_preflight_parity.py.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from scripts.preflight.base import Check, CheckContext, CheckResult, Criticality, Status

# ── thresholds ──────────────────────────────────────────────────────────────────
RAM_MIN_BYTES = 1 * 1024 ** 3          # 1 GB free RAM
ROOT_DISK_MIN_BYTES = 5 * 1024 ** 3    # 5 GB free on /
ROOT_DISK_MAX_PCT = 90.0               # / usage must stay < 90%
DATA_DISK_MIN_BYTES = 2 * 1024 ** 3    # 2 GB free on the data partition (premarket parity)
NTP_FAIL_SEC = 30.0                    # > 30s skew = FAIL (premarket parity)
NTP_WARN_SEC = 0.5                     # > 0.5s skew = WARN (spec's tighter band)


# ── low-level readers (monkeypatched in tests) ──────────────────────────────────
def _mem_available_bytes() -> int:
    """Linux MemAvailable in bytes (parsed from /proc/meminfo)."""
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable not found in /proc/meminfo")


def _disk_usage(path: str) -> tuple[int, float]:
    """(free_bytes, pct_used) for the filesystem holding ``path``."""
    total, used, free = shutil.disk_usage(path)
    pct_used = (used / total * 100.0) if total else 0.0
    return free, pct_used


def _ntp_skew_seconds() -> float | None:
    """Absolute NTP skew in seconds, or None when ntplib/network is unavailable."""
    try:
        import ntplib
        from datetime import datetime, timezone

        resp = ntplib.NTPClient().request("pool.ntp.org", version=3, timeout=5)
        ntp_t = datetime.fromtimestamp(resp.tx_time, tz=timezone.utc)
        return abs((datetime.now(tz=timezone.utc) - ntp_t).total_seconds())
    except Exception:
        return None


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f}GB"


def _project_root(ctx: CheckContext) -> Path:
    """Project root inferred from the DB path (data_store/trading_system.db)."""
    return ctx.db_path.resolve().parent.parent


# ── checks ──────────────────────────────────────────────────────────────────────
class VmRamCheck(Check):
    name = "vm_ram"
    group = "VM Health"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        try:
            free = _mem_available_bytes()
        except Exception as exc:
            return self._warn(f"cannot read memory ({exc})")
        if free > RAM_MIN_BYTES:
            return self._passed(f"{_gb(free)} free", free_bytes=free)
        return self._failed(f"low RAM: {_gb(free)} free (need {_gb(RAM_MIN_BYTES)})",
                            free_bytes=free)


class VmDiskRootCheck(Check):
    name = "vm_disk_root"
    group = "VM Health"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        free, pct = _disk_usage("/")
        if free < ROOT_DISK_MIN_BYTES or pct >= ROOT_DISK_MAX_PCT:
            return self._failed(
                f"/ low: {_gb(free)} free, {pct:.0f}% used "
                f"(need >{_gb(ROOT_DISK_MIN_BYTES)} and <{ROOT_DISK_MAX_PCT:.0f}%)",
                free_bytes=free, pct_used=round(pct, 1))
        return self._passed(f"/ {_gb(free)} free, {pct:.0f}% used",
                            free_bytes=free, pct_used=round(pct, 1))


class VmDiskDataCheck(Check):
    """Ported from premarket_healthcheck.check_disk_space (min 2GB)."""

    name = "vm_disk_data"
    group = "VM Health"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 20

    def run(self, ctx: CheckContext) -> CheckResult:
        root = _project_root(ctx)
        free, pct = _disk_usage(str(root))
        if free < DATA_DISK_MIN_BYTES:
            return self._failed(f"data disk low: {_gb(free)} free (need {_gb(DATA_DISK_MIN_BYTES)})",
                                free_bytes=free)
        return self._passed(f"data disk {_gb(free)} free", free_bytes=free)


class VmNtpSyncCheck(Check):
    """
    Ported from premarket_healthcheck.check_clock_skew, with the spec's tighter
    visibility band added. FAIL > 30s (premarket parity -- the only case it
    blocked); WARN > 0.5s; SKIPPED when ntplib/network is unavailable (premarket
    treated that as non-fatal).
    """

    name = "vm_ntp_sync"
    group = "VM Health"
    criticality = Criticality.CRITICAL
    expected_duration_ms = 5000

    def run(self, ctx: CheckContext) -> CheckResult:
        skew = _ntp_skew_seconds()
        if skew is None:
            return self._skipped("clock check skipped (ntplib/network unavailable)")
        if skew > NTP_FAIL_SEC:
            return self._failed(f"clock skew too large: {skew:.1f}s", skew_sec=round(skew, 3))
        if skew > NTP_WARN_SEC:
            return self._warn(f"clock skew {skew:.2f}s (>{NTP_WARN_SEC}s)", skew_sec=round(skew, 3))
        return self._passed(f"clock OK: skew {skew:.2f}s", skew_sec=round(skew, 3))


CHECKS = [
    VmRamCheck(),
    VmDiskRootCheck(),
    VmDiskDataCheck(),
    VmNtpSyncCheck(),
]
