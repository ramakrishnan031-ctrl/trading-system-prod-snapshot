"""tests/unit/test_sentinel_retention_scope.py -- SK-D1 (26-Jul-2026).

`sentinel_retention` must delete ONLY `critical_alert_*.delivered`, never `*.flag`.

WHY THIS IS LOAD-BEARING. Two standing checks count the `.flag` population as their
measure of undelivered CRITICAL alerts:

    scripts/system_manager.py:482        pending = len(list(ds.glob("critical_alert_*.flag")))
                                         -> "Pending CRITICAL sentinels: N"  (ok iff 0)
    scripts/preflight/checks/recovery.py:103
                                         flags = list(d.glob("critical_alert_*.flag"))

The 02:05 cron deletes by a DIFFERENT extension, so those counters are correct today.
⚠️ But they are correct only because the glob is extension-scoped -- broaden it to the
obvious-looking `critical_alert_*` while tidying, and both checks silently start
reading LOW: an undelivered CRITICAL older than 7 days would be garbage-collected and
"Pending CRITICAL sentinels: 0" would then mean "nothing pending OR it aged out".
That is a silent success, and it would be invisible precisely because the check is
green.

`config/cron_registry.yaml` already records the constraint in prose --
`script: ... (never *.flag)` -- but prose is not a guard. The authoritative field is
`command:`, which is what `scripts/generate_crontab.compose()` installs, so that is
what this test pins.

CONTEXT (26-Jul-2026): this cron is why the SSH-key sentinel count read 33 rather
than the 37 a handoff note predicted -- it had garbage-collected 17-Jul's four
sentinels at 02:05:01. The count was the wrong observable; the retention job was
behaving exactly as designed. Nothing here changes the job. It pins its SCOPE.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REGISTRY = Path("config/cron_registry.yaml")


def _sentinel_retention_job() -> dict:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    for _section, jobs in data.items():
        if isinstance(jobs, dict) and "sentinel_retention" in jobs:
            return jobs["sentinel_retention"]
    raise AssertionError("sentinel_retention job not found in config/cron_registry.yaml")


def test_sentinel_retention_deletes_only_delivered_sentinels() -> None:
    """The installed command must be scoped to `.delivered`."""
    cmd = _sentinel_retention_job()["command"]
    assert "critical_alert_*.delivered" in cmd, (
        "sentinel_retention must target `critical_alert_*.delivered` explicitly; "
        f"got: {cmd}"
    )


def test_sentinel_retention_never_deletes_flag_sentinels() -> None:
    """⭐ The guard proper: an unscoped or `.flag`-inclusive pattern would silently
    erode the two 'pending CRITICAL sentinels' counters."""
    cmd = _sentinel_retention_job()["command"]
    assert ".flag" not in cmd, (
        "sentinel_retention must NEVER delete `.flag` sentinels -- system_manager.py "
        "and preflight/checks/recovery.py count them as UNDELIVERED CRITICAL alerts"
    )
    assert '-name "critical_alert_*"' not in cmd, (
        "an unscoped `critical_alert_*` pattern would sweep `.flag` files too; keep "
        "the `.delivered` suffix in the -name pattern"
    )


def test_registry_prose_still_records_the_constraint() -> None:
    """The human-facing `script:` line carries the rule for the next reader. Prose is
    not a guard -- the two tests above are -- but losing it costs the next person the
    reason, so it is pinned too."""
    script = _sentinel_retention_job()["script"]
    assert "never *.flag" in script, (
        "the registry's `script:` description should keep the `(never *.flag)` note "
        "that explains WHY the pattern is extension-scoped"
    )
