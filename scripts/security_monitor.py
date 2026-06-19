#!/usr/bin/env python3
"""
scripts/security_monitor.py -- VM Security Manager, Phase 1 (monitoring + alerts).

Standalone, alert-ONLY VM security watcher. It NEVER blocks access (per Rama's
choice — key-only SSH is the gate; this just observes and alerts) and NEVER
crashes its caller (every check is isolated in try/except).

What it watches (read-only):
  1. ~/.ssh/authorized_keys             -> new/changed SSH key (CRITICAL)
  2. sudo COMMAND events                -> non-whitelisted privilege use (INFO)
  3. failed/invalid login rate          -> spike vs baseline (WARNING)
  4. successful logins from a NEW IP    -> possible stolen key (WARNING)
  5. sensitive-file content hashes      -> .env/sshd_config/sudoers/units (CRITICAL/WARNING)
  6. active SSH session count           -> over configured limit (WARNING, alert not block)
  7. root login probes                  -> anomalous spike (INFO)

Auth source: /var/log/auth.log (the `ubuntu` user is in group `adm`, so this
reads it directly — no sudo needed).

Config: config/security.yaml (DELIBERATELY decoupled from the trading app's
strict pydantic config_loader, which uses extra="forbid" — a security key in
system_config.yaml would break main.py startup. This watcher is not part of the
trading app, so it reads its own file).

State: data_store/security_state.json (known key hash, seen login IPs, file
hashes, and an alert-dedup ledger so a persistent condition is not re-alerted
every cycle).

Alerts: [LFL836] Telegram via TelegramNotifier.from_env + CRITICAL email via
alerts.critical.write_critical_sentinel (consumed by alert-watcher.service).

Modes:
  --watch     one monitoring pass (used by security-watcher.service ~60s). Alerts + state.
  --report    human-readable status to stdout; NO alerts, NO state writes.
  --baseline  capture current state as known-good (no alerts). Run once at setup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import logging

_log = logging.getLogger("security_monitor")

_IST = timezone(timedelta(hours=5, minutes=30))
_DEFAULT_CONFIG = _ROOT / "config" / "security.yaml"
_DEFAULT_STATE = _ROOT / "data_store" / "security_state.json"
_DEFAULT_AUTHLOG = Path("/var/log/auth.log")

# auth.log timestamp (rsyslog ISO): 2026-06-19T22:43:11.803963+05:30 <host> sshd[..]: ...
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[.\d]*[+\-]\d{2}:\d{2})")
_ACCEPTED_RE = re.compile(r"Accepted publickey for (\S+) from ([\d.]+)")
_FAILED_RE = re.compile(r"(Failed password|Invalid user)")
_ROOT_PROBE_RE = re.compile(r"authenticating user root")
_SUDO_RE = re.compile(r"sudo:\s+(\S+)\s*:.*COMMAND=(\S+)")


# ─────────────────────────────────────────────────────────────────────────────
# Findings
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    severity: str   # CRITICAL | WARNING | INFO
    key: str        # dedup identity (stable for the same condition+identity)
    title: str
    body: str


# ─────────────────────────────────────────────────────────────────────────────
# Config (standalone YAML; defensive defaults if file/keys absent)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SecConfig:
    enabled: bool = True
    max_active_sessions: int = 2
    failed_login_spike_threshold: int = 500     # per hour
    root_probe_spike_threshold: int = 400       # per hour (constant noise; alert only on anomaly)
    new_ip_alert: bool = True
    sudo_alert: bool = True
    realert_cooldown_sec: int = 21600           # 6h: don't re-alert the same identity sooner
    expected_ssh_keys: int = 1
    expected_key_fingerprint: str = ""
    sudo_whitelist_prefixes: list = field(default_factory=lambda: [
        "/usr/bin/systemctl", "/bin/systemctl", "/usr/bin/grep", "/usr/bin/tail",
        "/usr/bin/cat", "/usr/sbin/fail2ban-client", "/usr/sbin/augenrules",
        "/usr/bin/auditctl", "/usr/bin/journalctl",
    ])
    watched_files: list = field(default_factory=list)
    authlog_path: str = str(_DEFAULT_AUTHLOG)
    authorized_keys_path: str = "/home/ubuntu/.ssh/authorized_keys"
    sentinel_dir: str = "data_store"

    @staticmethod
    def load(path: Path) -> "SecConfig":
        cfg = SecConfig()
        try:
            import yaml  # PyYAML is already a project dep
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            sec = raw.get("security", raw) if isinstance(raw, dict) else {}
            for f in (
                "enabled", "max_active_sessions", "failed_login_spike_threshold",
                "root_probe_spike_threshold", "new_ip_alert", "sudo_alert",
                "realert_cooldown_sec", "expected_ssh_keys", "expected_key_fingerprint",
                "sudo_whitelist_prefixes", "watched_files", "authlog_path",
                "authorized_keys_path", "sentinel_dir",
            ):
                if f in sec and sec[f] is not None:
                    setattr(cfg, f, sec[f])
        except FileNotFoundError:
            _log.warning("security.yaml not found at %s; using defaults", path)
        except Exception as exc:  # never fail to run on a bad config
            _log.error("security.yaml load failed (%s); using defaults", exc)
        if not cfg.watched_files:
            cfg.watched_files = _default_watched_files()
        return cfg


def _default_watched_files() -> list:
    p = str(_ROOT)
    return [
        {"path": "/home/ubuntu/.ssh/authorized_keys", "severity": "CRITICAL", "label": "ssh_authorized_keys"},
        {"path": "/etc/ssh/sshd_config", "severity": "CRITICAL", "label": "sshd_config"},
        {"path": "/etc/sudoers", "severity": "CRITICAL", "label": "sudoers"},
        {"path": "/etc/systemd/system/trading-system.service", "severity": "CRITICAL", "label": "unit_trading"},
        {"path": "/etc/systemd/system/security-watcher.service", "severity": "CRITICAL", "label": "unit_security"},
        {"path": f"{p}/.env", "severity": "CRITICAL", "label": "dotenv"},
        {"path": f"{p}/config/system_config.yaml", "severity": "WARNING", "label": "system_config"},
        {"path": f"{p}/config/accounts.csv", "severity": "WARNING", "label": "accounts_csv"},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested)
# ─────────────────────────────────────────────────────────────────────────────

def sha256_file(path: str) -> Optional[str]:
    """SHA-256 of a file's bytes, or None if unreadable/absent."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def authorized_keys_fingerprints(path: str) -> list[str]:
    """Return SHA256 fingerprints (one per key line) using ssh-keygen -lf.
    Falls back to [] if ssh-keygen unavailable or file missing."""
    try:
        out = subprocess.run(
            ["ssh-keygen", "-lf", path], capture_output=True, text=True, timeout=10,
        )
        fps = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("SHA256:"):
                fps.append(parts[1])
        return fps
    except Exception:
        return []


def parse_line_ts(line: str) -> Optional[datetime]:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1))
    except ValueError:
        return None


def scan_authlog(lines: list[str], since: datetime) -> dict:
    """Single pass over auth.log lines, collecting events at/after `since`.
    Returns counts + the accepted-login IP set + sudo (user, cmd) list."""
    accepted_ips: set[str] = set()
    failed = 0
    root_probes = 0
    sudo_events: list[tuple[str, str]] = []
    for line in lines:
        ts = parse_line_ts(line)
        if ts is not None and ts < since:
            continue
        m = _ACCEPTED_RE.search(line)
        if m:
            accepted_ips.add(m.group(2))
        if _FAILED_RE.search(line):
            failed += 1
        if _ROOT_PROBE_RE.search(line):
            root_probes += 1
        sm = _SUDO_RE.search(line)
        if sm:
            sudo_events.append((sm.group(1), sm.group(2)))
    return {
        "accepted_ips": accepted_ips,
        "failed": failed,
        "root_probes": root_probes,
        "sudo_events": sudo_events,
    }


def established_ssh_peers() -> list[str]:
    """Peer IPs of ESTABLISHED connections to local :22 (via ss). [] on error."""
    try:
        out = subprocess.run(
            ["ss", "-tnH", "state", "established", "( sport = :22 )"],
            capture_output=True, text=True, timeout=10,
        )
        peers = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                peer = parts[3].rsplit(":", 1)[0].strip("[]")
                if peer:
                    peers.append(peer)
        return peers
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# ─────────────────────────────────────────────────────────────────────────────
# Checks (each returns list[Finding]; each mutates `state` for tracking)
# ─────────────────────────────────────────────────────────────────────────────

def check_authorized_keys(cfg: SecConfig, state: dict) -> list[Finding]:
    path = cfg.authorized_keys_path
    cur = sha256_file(path)
    prev = state.get("authorized_keys_sha256")
    fps = authorized_keys_fingerprints(path)
    state["authorized_keys_sha256"] = cur
    state["authorized_keys_fingerprints"] = fps
    out: list[Finding] = []
    if cur is None:
        return [Finding("CRITICAL", "authkeys:missing",
                        "authorized_keys missing/unreadable",
                        f"{path} could not be read — SSH access may be broken or tampered.")]
    if prev is not None and cur != prev:
        out.append(Finding("CRITICAL", f"authkeys:hash:{cur[:12]}",
                            "NEW SSH KEY DETECTED",
                            f"authorized_keys changed. Now {len(fps)} key(s): "
                            f"{', '.join(fps) or '?'}. If this was not you, the VM may be compromised."))
    if cfg.expected_key_fingerprint:
        unexpected = [f for f in fps if f != cfg.expected_key_fingerprint]
        if unexpected:
            out.append(Finding("CRITICAL", f"authkeys:unexpected:{','.join(sorted(unexpected))[:40]}",
                               "UNEXPECTED SSH KEY present",
                               f"authorized_keys has key(s) not matching the expected baseline: "
                               f"{', '.join(unexpected)}."))
    if len(fps) > cfg.expected_ssh_keys:
        out.append(Finding("CRITICAL", f"authkeys:count:{len(fps)}",
                           "SSH key COUNT exceeds baseline",
                           f"{len(fps)} keys present (baseline {cfg.expected_ssh_keys})."))
    return out


def check_new_login_ips(cfg: SecConfig, scan: dict, state: dict, baseline: bool) -> list[Finding]:
    known = set(state.get("known_login_ips", []))
    out: list[Finding] = []
    for ip in sorted(scan["accepted_ips"]):
        if ip not in known:
            known.add(ip)
            if not baseline and cfg.new_ip_alert:
                out.append(Finding("WARNING", f"newip:{ip}",
                                   "New successful SSH login IP",
                                   f"First-seen successful key login from {ip}. "
                                   f"Was this you? (Rama's IP changes — expected if so.)"))
    state["known_login_ips"] = sorted(known)
    return out


def check_failed_spike(cfg: SecConfig, scan: dict, now: datetime) -> list[Finding]:
    if scan["failed"] > cfg.failed_login_spike_threshold:
        return [Finding("WARNING", f"failspike:{now:%Y%m%d%H}",
                        "Failed-login spike",
                        f"{scan['failed']} failed/invalid attempts in the last hour "
                        f"(threshold {cfg.failed_login_spike_threshold}). Possible targeted attack.")]
    return []


def check_root_probe_spike(cfg: SecConfig, scan: dict, now: datetime) -> list[Finding]:
    if scan["root_probes"] > cfg.root_probe_spike_threshold:
        return [Finding("INFO", f"rootspike:{now:%Y%m%d%H}",
                        "Root login-probe spike",
                        f"{scan['root_probes']} root login probes in the last hour "
                        f"(threshold {cfg.root_probe_spike_threshold}). Blocked by key-only auth.")]
    return []


def check_sudo_events(cfg: SecConfig, scan: dict) -> list[Finding]:
    if not cfg.sudo_alert:
        return []
    out: list[Finding] = []
    seen: set[str] = set()
    for user, cmd in scan["sudo_events"]:
        if any(cmd.startswith(p) for p in cfg.sudo_whitelist_prefixes):
            continue
        if cmd in seen:
            continue
        seen.add(cmd)
        out.append(Finding("INFO", f"sudo:{cmd}",
                           "Non-whitelisted sudo command",
                           f"sudo by {user}: {cmd}"))
    return out


def check_watched_files(cfg: SecConfig, state: dict) -> list[Finding]:
    hashes = state.get("file_hashes", {})
    out: list[Finding] = []
    for spec in cfg.watched_files:
        path = spec.get("path")
        if not path or spec.get("label") == "ssh_authorized_keys":
            continue  # authorized_keys handled by check_authorized_keys
        sev = spec.get("severity", "WARNING")
        label = spec.get("label", path)
        cur = sha256_file(path)
        prev = hashes.get(path)
        hashes[path] = cur
        if cur is None:
            continue
        if prev is not None and cur != prev:
            out.append(Finding(sev, f"file:{label}:{cur[:12]}",
                               f"Sensitive file changed: {label}",
                               f"{path} content changed (sha256 {prev[:12]}→{cur[:12]}). "
                               f"If this was not a git deploy / known change, investigate."))
    state["file_hashes"] = hashes
    return out


def check_active_sessions(cfg: SecConfig, state: dict) -> list[Finding]:
    peers = established_ssh_peers()
    n = len(peers)
    state["last_session_count"] = n
    state["last_session_peers"] = sorted(set(peers))
    if n > cfg.max_active_sessions:
        return [Finding("WARNING", f"sessions:{n}:{','.join(sorted(set(peers)))[:60]}",
                        "Active SSH sessions over limit",
                        f"{n} active SSH sessions (limit {cfg.max_active_sessions}). "
                        f"IPs: {', '.join(sorted(set(peers)))}. (Alert only — not blocked.)")]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _dedup(findings: list[Finding], state: dict, cooldown: int, now_ts: float) -> list[Finding]:
    """Drop findings whose key alerted within the cooldown; record the rest."""
    ledger = state.get("alerted", {})
    fresh: list[Finding] = []
    for f in findings:
        last = ledger.get(f.key)
        if last is not None and (now_ts - last) < cooldown:
            continue
        ledger[f.key] = now_ts
        fresh.append(f)
    # prune ledger entries older than 2x cooldown to bound growth
    cutoff = now_ts - 2 * cooldown
    state["alerted"] = {k: v for k, v in ledger.items() if v >= cutoff}
    return fresh


def _send(f: Finding, cfg: SecConfig) -> None:
    try:
        from alerts.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.from_env(logger=_log)
        if n is not None:
            n.send(severity=f.severity, title=f"🔒 {f.title}", body=f.body,
                   source_module="security_monitor")
    except Exception as exc:
        _log.error("security_monitor: telegram send failed: %s", exc)
    if f.severity == "CRITICAL":
        try:
            from alerts.critical import write_critical_sentinel
            write_critical_sentinel(title=f.title, body=f.body,
                                    source_module="security_monitor",
                                    sentinel_dir=cfg.sentinel_dir)
        except Exception as exc:
            _log.error("security_monitor: sentinel write failed: %s", exc)


def run_pass(cfg: SecConfig, state: dict, authlog: Path, now: datetime,
             baseline: bool) -> list[Finding]:
    """Run all checks (each isolated). Returns findings (pre-dedup)."""
    try:
        lines = authlog.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        lines = []
        _log.error("security_monitor: cannot read %s: %s", authlog, exc)
    since_hour = now - timedelta(hours=1)
    since_window = now - timedelta(minutes=15)
    scan_hour = scan_authlog(lines, since_hour)
    scan_window = scan_authlog(lines, since_window)

    findings: list[Finding] = []
    checks = [
        lambda: check_authorized_keys(cfg, state),
        lambda: check_new_login_ips(cfg, scan_window, state, baseline),
        lambda: check_failed_spike(cfg, scan_hour, now),
        lambda: check_root_probe_spike(cfg, scan_hour, now),
        lambda: check_sudo_events(cfg, scan_window),
        lambda: check_watched_files(cfg, state),
        lambda: check_active_sessions(cfg, state),
    ]
    for chk in checks:
        try:
            findings.extend(chk())
        except Exception as exc:
            _log.error("security_monitor: check error: %s", exc, exc_info=True)
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VM Security Monitor (Phase 1)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--watch", action="store_true", help="one monitoring pass (alerts + state)")
    g.add_argument("--report", action="store_true", help="human-readable status; no alerts/state")
    g.add_argument("--baseline", action="store_true", help="capture current state as known-good")
    ap.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    ap.add_argument("--state", type=Path, default=_DEFAULT_STATE)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = SecConfig.load(args.config)
    authlog = Path(cfg.authlog_path)
    now = datetime.now(_IST)
    baseline = args.baseline

    if not cfg.enabled and not args.report:
        _log.info("security_monitor: disabled in config; nothing to do")
        return 0

    state = load_state(args.state)
    findings = run_pass(cfg, state, authlog, now, baseline)

    if args.report:
        print(f"=== Security Monitor report @ {now:%Y-%m-%d %H:%M:%S %Z} ===")
        print(f"authorized_keys: {len(state.get('authorized_keys_fingerprints', []))} key(s) "
              f"{state.get('authorized_keys_fingerprints', [])}")
        print(f"active SSH sessions: {state.get('last_session_count')} "
              f"peers={state.get('last_session_peers')}")
        print(f"known login IPs: {len(state.get('known_login_ips', []))}")
        if findings:
            print(f"\n{len(findings)} finding(s):")
            for f in findings:
                print(f"  [{f.severity}] {f.title} — {f.body}")
        else:
            print("\nNo findings.")
        return 0

    if baseline:
        save_state(args.state, state)
        _log.info("security_monitor: baseline captured (%d keys, %d known IPs, %d files)",
                  len(state.get("authorized_keys_fingerprints", [])),
                  len(state.get("known_login_ips", [])),
                  len(state.get("file_hashes", {})))
        return 0

    fresh = _dedup(findings, state, cfg.realert_cooldown_sec, now.timestamp())
    for f in fresh:
        _send(f, cfg)
        _log.warning("security_monitor ALERT [%s] %s — %s", f.severity, f.title, f.body)
    save_state(args.state, state)
    _log.info("security_monitor: pass complete (%d finding(s), %d new alert(s))",
              len(findings), len(fresh))
    # exit 0 always (watcher must keep running); severity is in the alerts.
    return 0


if __name__ == "__main__":
    sys.exit(main())
