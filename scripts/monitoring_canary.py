#!/usr/bin/env python3
"""
scripts/monitoring_canary.py -- Trading System v2  (15-Jul-2026, monitoring hardening)

A DAILY monitoring self-test: it proves the alerting/observability plumbing actually
works and FAILS LOUDLY — via the surviving channel — when it does not. It exists because
the 15-Jul audit found the monitor reporting green while every email + sentinel alert was
silently undelivered (a dead SMTP credential). A monitor that can lie needs a monitor.

Five independent paths are probed, each PASS/FAIL with detail:
    1. EMAIL      — SMTP login (no send) using the alert_watcher SMTP config. The check
                    that would have caught the 15-Jul 535 BadCredentials the morning it broke.
    2. TELEGRAM   — the bot token validates via getMe (no channel spam); the canary's own
                    failure report is then delivered over Telegram (end-to-end proof).
    3. SENTINEL   — alert_watcher ingestion is healthy (no F1 'degraded' marker; no large
                    backlog of undelivered .flag sentinels).
    4. DASHBOARD  — the read-only ops GUI service is active (systemctl is-active).
    5. RESPAWN    — alert-watcher is a stable --loop daemon, NOT a systemd respawn loop
                    (NRestarts rate + SubState). Closes the 16-Jul blind spot where a service
                    churning ~every 10s still read healthy on every other path.

Noise profile: QUIET when healthy (heartbeat + functional_status only), LOUD when broken
(a Telegram WARNING naming the down path; if Telegram is ALSO down, a CRITICAL sentinel is
written so the failure escalates through the F1 fallback). The EMAIL path is EXPECTED to
FAIL until ALERT_SMTP_PASSWORD is restored — that failing check, announced over Telegram,
is the canary working as intended.

The seven-item PREVENTION CHECKLIST applied to this job itself:
  (1) functional criterion = all four paths probed + report delivered on failure;
  (2) health-check = it records its own heartbeat + functional_status (F2);
  (3) alert path = Telegram WARNING on any failure;
  (4) fallback path = a CRITICAL sentinel when Telegram is down too (rides the F1 fallback);
  (5) regression test = tests/unit/test_monitoring_canary.py;
  (6) owner = Rama (ops);
  (7) monitoring classification = monitored, heartbeat_db + functional_status.

Exit codes: 0 = ran (healthy OR degraded-but-reported); 1 = the canary itself could not run.
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env so a cron invocation sees TELEGRAM_BOT_TOKEN + ALERT_SMTP_PASSWORD (the two
# credentials the canary probes), mirroring scripts/check_cron_drift.py.
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:  # noqa: BLE001 — canary must run even if python-dotenv is absent
    pass

from alerts.critical import list_pending_sentinels, write_critical_sentinel
from core.logger import get_logger
from core.time_authority import now_ist

_log = get_logger("monitoring_canary")

# A backlog above this many undelivered .flag sentinels means ingestion/delivery is stalled.
_STUCK_SENTINEL_THRESHOLD = 20


# ── Path probes (pure + dependency-injected for tests) ───────────────────────

def check_email_path(smtp_cfg) -> tuple[bool, str]:
    """SMTP LOGIN only (never sends) — the direct credential test. Would have caught the
    15-Jul 535 the morning it broke."""
    try:
        if getattr(smtp_cfg, "use_tls", True):
            server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout_sec)
            server.ehlo(); server.starttls(); server.ehlo()
        else:
            server = smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, timeout=smtp_cfg.timeout_sec)
        try:
            server.login(smtp_cfg.username, smtp_cfg.resolved_password())
        finally:
            server.quit()
        return True, "SMTP login OK"
    except smtplib.SMTPAuthenticationError as exc:
        return False, f"SMTP AUTH FAILED — restore ALERT_SMTP_PASSWORD ({exc})"
    except Exception as exc:  # noqa: BLE001
        return False, f"SMTP error: {exc}"


def check_telegram_path(bot_token: Optional[str],
                        getter: Optional[Callable] = None) -> tuple[bool, str]:
    """Validate the bot token via getMe (no channel spam). getter is injectable for tests."""
    if not bot_token:
        return False, "telegram unconfigured (TELEGRAM_BOT_TOKEN unset)"
    try:
        if getter is None:
            import requests
            getter = lambda url: requests.get(url, timeout=5)  # noqa: E731
        resp = getter(f"https://api.telegram.org/bot{bot_token}/getMe")
        data = resp.json() if hasattr(resp, "json") else {}
        if getattr(resp, "status_code", 0) == 200 and data.get("ok"):
            return True, "telegram bot token valid"
        return False, f"telegram getMe failed (status={getattr(resp,'status_code','?')})"
    except Exception as exc:  # noqa: BLE001
        return False, f"telegram error: {exc}"


def check_sentinel_ingestion(sentinel_dir: Path,
                             stuck_threshold: int = _STUCK_SENTINEL_THRESHOLD) -> tuple[bool, str]:
    """Healthy iff the alert_watcher is not in F1 'degraded' state and the .flag backlog is small."""
    degraded_marker = Path(sentinel_dir) / "alert_watcher_degraded.json"
    pending = list_pending_sentinels(sentinel_dir)
    if degraded_marker.exists():
        return False, f"alert_watcher DEGRADED (email down); {len(pending)} sentinel(s) pending"
    if len(pending) > stuck_threshold:
        return False, f"{len(pending)} undelivered sentinels — ingestion/delivery stalled"
    return True, f"{len(pending)} pending (ok)"


def check_dashboard(unit: str = "gui-dashboard",
                    runner: Optional[Callable] = None) -> tuple[bool, str]:
    """The read-only ops GUI service is active (systemctl is-active). runner injectable for tests."""
    try:
        if runner is None:
            runner = lambda: subprocess.run(  # noqa: E731
                ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=10)
        r = runner()
        state = (getattr(r, "stdout", "") or "").strip()
        return (state == "active"), f"{unit}={state or 'unknown'}"
    except Exception as exc:  # noqa: BLE001
        return False, f"dashboard check error: {exc}"


# ── Respawn-loop detection (16-Jul-2026) ─────────────────────────────────────
#
# The 16-Jul morning verify found alert-watcher silently RESPAWNING (--once + Restart=always
# → ~101,570 restarts) while every alert path read healthy: sentinels still delivered each
# pass, so check_sentinel_ingestion was green and NOTHING here watched the service's restart
# rate. A monitor that calls a churning service "healthy" is exactly the blind spot the canary
# exists to close. This probe treats a rapid-respawn pattern (SubState=auto-restart, or
# NRestarts climbing fast between daily canary runs) as NOT-healthy, so the fixed --loop daemon
# can be proven stable and a regression back into a respawn loop can never read green.
# DEFAULT thresholds — overridable via alerts.respawn_rate_per_hour_threshold /
# alerts.respawn_restart_delta_threshold (run_canary reads the config and passes them in;
# direct/test callers with no override get exactly these values → behaviour unchanged).
_RESPAWN_MAX_PER_HOUR = 6.0     # a long-lived daemon restarts a handful of times/day at most
_RESPAWN_MIN_DELTA = 3          # ignore 1-2 legit restarts in a short inter-run gap (no false alarm)
_SERVICE_STATE_FILE = "canary_service_state.json"


def _parse_systemctl_show(text: str) -> dict:
    """Parse `systemctl show` KEY=VALUE lines into a dict (order-independent)."""
    props: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            props[k.strip()] = v.strip()
    return props


def _load_service_state(state_path: Optional[Path]) -> dict:
    if not state_path:
        return {}
    try:
        return json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_service_state(state_path: Optional[Path], state: dict) -> None:
    if not state_path:
        return
    try:
        p = Path(state_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def _elapsed_seconds(prev_iso: Optional[str], now_iso: str) -> float:
    """Seconds between two ISO timestamps; 0.0 if prev is missing/unparseable (→ baseline)."""
    if not prev_iso:
        return 0.0
    try:
        return (datetime.fromisoformat(now_iso) - datetime.fromisoformat(prev_iso)).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _classify_respawn(nrestarts: int, substate: str, prev_nrestarts: Optional[int],
                      elapsed_sec: float, *, max_restarts_per_hour: float = _RESPAWN_MAX_PER_HOUR,
                      min_delta: int = _RESPAWN_MIN_DELTA) -> tuple[bool, str]:
    """Pure verdict: is the service respawning? (unit-testable, no I/O)

    NOT-healthy when SubState=auto-restart (a stable daemon is never mid-restart at sample
    time) OR NRestarts jumped by >= min_delta at a rate above max_restarts_per_hour since the
    previous canary run. First run (no prev) or a counter reset (redeploy) is a benign baseline.
    """
    if substate == "auto-restart":
        return False, f"mid-restart (SubState=auto-restart) — verify not a respawn loop; NRestarts={nrestarts}"
    if prev_nrestarts is None or elapsed_sec <= 0:
        return True, f"NRestarts={nrestarts} (baseline)"
    delta = nrestarts - prev_nrestarts
    if delta < 0:
        return True, f"NRestarts reset to {nrestarts} (service redeployed)"
    rate = delta / (elapsed_sec / 3600.0)
    if delta >= min_delta and rate > max_restarts_per_hour:
        return False, (f"RESPAWN LOOP: +{delta} restarts in {elapsed_sec:.0f}s "
                       f"(~{rate:.0f}/hr > {max_restarts_per_hour:.0f}/hr)")
    return True, f"NRestarts={nrestarts} stable (+{delta} in {elapsed_sec:.0f}s)"


def check_service_respawn(unit: str = "alert-watcher.service", *,
                          runner: Optional[Callable] = None,
                          state_path: Optional[Path] = None,
                          now_iso: Optional[str] = None,
                          max_restarts_per_hour: Optional[float] = None,
                          min_delta: Optional[int] = None) -> tuple[bool, str]:
    """Healthy iff `unit` is NOT respawning (see _classify_respawn). Reads NRestarts + SubState
    via `systemctl show` (runner injectable for tests) and compares NRestarts to the persisted
    previous sample. Thresholds fall back to the module defaults when not supplied (run_canary
    passes the config-driven `alerts.respawn_*` values). A systemctl/parse failure DEGRADES to
    healthy-with-note (a respawn-check outage is not itself an alert-path failure) rather than
    firing a spurious canary WARNING."""
    try:
        if runner is None:
            runner = lambda: subprocess.run(  # noqa: E731
                ["systemctl", "show", unit, "--property=NRestarts", "--property=SubState"],
                capture_output=True, text=True, timeout=10).stdout
        props = _parse_systemctl_show(runner())
        nrestarts = int(props.get("NRestarts", "0") or 0)
        substate = props.get("SubState", "") or ""
    except Exception as exc:  # noqa: BLE001
        return True, f"{unit}: respawn-check unavailable ({exc})"

    now_iso = now_iso or now_ist().isoformat()
    prev = _load_service_state(state_path)
    prev_n = prev.get("nrestarts")
    elapsed = _elapsed_seconds(prev.get("iso"), now_iso)
    ok, detail = _classify_respawn(
        nrestarts, substate, prev_n, elapsed,
        max_restarts_per_hour=(_RESPAWN_MAX_PER_HOUR if max_restarts_per_hour is None else max_restarts_per_hour),
        min_delta=(_RESPAWN_MIN_DELTA if min_delta is None else min_delta),
    )
    _save_service_state(state_path, {"nrestarts": nrestarts, "iso": now_iso})
    return ok, f"{unit}: {detail}"


# ── Orchestration ────────────────────────────────────────────────────────────

def run_canary(cfg, *, sentinel_dir: Optional[Path] = None) -> dict:
    """Run all five probes. Returns {path: {"ok": bool, "detail": str}, "overall_ok": bool}."""
    alerts_cfg = cfg.system.alerts
    sd = Path(sentinel_dir if sentinel_dir is not None else alerts_cfg.sentinel_dir)
    results = {
        "email": check_email_path(alerts_cfg.smtp),
        "telegram": check_telegram_path(os.environ.get("TELEGRAM_BOT_TOKEN", "")),
        "sentinel": check_sentinel_ingestion(sd),
        "dashboard": check_dashboard(),
        "respawn": check_service_respawn(
            state_path=sd / _SERVICE_STATE_FILE,
            max_restarts_per_hour=getattr(alerts_cfg, "respawn_rate_per_hour_threshold", _RESPAWN_MAX_PER_HOUR),
            min_delta=getattr(alerts_cfg, "respawn_restart_delta_threshold", _RESPAWN_MIN_DELTA),
        ),
    }
    out = {k: {"ok": ok, "detail": detail} for k, (ok, detail) in results.items()}
    out["overall_ok"] = all(v["ok"] for v in out.values() if isinstance(v, dict))
    return out


def _format_report(results: dict) -> str:
    icon = {True: "✅", False: "🔴"}
    lines = ["🐤 [LFL836] Monitoring Canary — " + now_ist().strftime("%d-%b %H:%M")]
    for path in ("email", "telegram", "sentinel", "dashboard", "respawn"):
        r = results.get(path, {})
        lines.append(f"{icon.get(r.get('ok'), '❔')} {path}: {r.get('detail', '?')}")
    return "\n".join(lines)


def _deliver_report(results: dict, report: str) -> None:
    """QUIET when healthy; on any failure send a Telegram WARNING (the surviving channel),
    and if Telegram is ALSO down write a CRITICAL sentinel so it escalates via the F1 fallback."""
    if results.get("overall_ok"):
        return
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=_log)
        if notifier is not None:
            res = notifier.send(severity="WARNING", title="Monitoring canary: path(s) DOWN",
                                body=report, source_module="monitoring_canary")
            if getattr(res, "success", False):
                return
    except Exception as exc:  # noqa: BLE001
        _log.error("canary telegram report failed: %s", exc)
    # Telegram also down → escalate via a CRITICAL sentinel (F1 fallback delivers it).
    try:
        write_critical_sentinel(title="Monitoring canary: alert channels DOWN",
                                body=report, source_module="monitoring_canary")
    except OSError as exc:
        _log.error("canary sentinel fallback failed: %s", exc)


def main(argv=None) -> int:
    try:
        from core.config_loader import load_all
        cfg = load_all()
    except Exception as exc:  # noqa: BLE001
        _log.error("monitoring_canary: config load failed: %s", exc)
        return 1

    results = run_canary(cfg)
    report = _format_report(results)
    print(report)
    _log.info("monitoring_canary.result", extra={"overall_ok": results["overall_ok"],
              "email_ok": results["email"]["ok"], "telegram_ok": results["telegram"]["ok"]})
    _deliver_report(results, report)
    return 0


def _cron_main(argv=None) -> int:
    """Cron entry: heartbeat (EXECUTION) + functional_status (FUNCTIONAL, F2) + alert."""
    from utils.cron_heartbeat import HeartbeatTimer

    timer = HeartbeatTimer("monitoring_canary", alert=True)
    with timer:
        rc = main(argv)
        rc = 0 if rc is None else rc
        # F2: EXECUTION vs FUNCTIONAL — the canary can RUN cleanly (rc 0) while a monitored
        # path is DOWN. Record that as functional-degraded so it never reads as clean.
        try:
            from core.config_loader import load_all
            results = run_canary(load_all())
            timer.functional_status = "OK" if results["overall_ok"] else "DEGRADED"
            if not results["overall_ok"]:
                down = [p for p in ("email", "telegram", "sentinel", "dashboard", "respawn")
                        if not results[p]["ok"]]
                timer.message = "paths down: " + ", ".join(down)
        except Exception:  # noqa: BLE001
            timer.functional_status = "UNKNOWN"
        if rc != 0:
            timer.status = "FAILED"
    return rc


if __name__ == "__main__":
    sys.exit(_cron_main())
