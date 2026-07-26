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
  6. active SSH session count           -> over configured limit (WARNING, alert not block;
                                            per-IP breakdown enriched with best-effort GeoIP)
  7. root login probes                  -> anomalous spike (INFO)
  8. copy_protection ON->OFF transition -> someone disabled the gate (CRITICAL)  [Phase 2]
  9. auditd copy_attempt bypass         -> outbound scp/sftp/rsync w/o a token (CRITICAL)  [Phase 2]
 10. nse_holidays_<year>.yaml presence  -> a required config file that will not exist
                                           in time (CRITICAL)  [NOT a security check —
                                           see check_nse_holiday_calendar for why the
                                           always-on watcher is the only host that can
                                           reach the operator without anyone acting]

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
import ipaddress
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
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
# Operator-approved SSH baseline override — written by scripts/approve_ssh_keys.py.
# Lives OUTSIDE git so a deploy's `checkout -f` cannot revert a legitimate
# re-baseline (the root cause of repeated false-positive CRITICALs).
_DEFAULT_OPERATOR_SSH_BASELINE = _ROOT / "data_store" / "security" / "ssh_key_baseline.json"
_DEFAULT_AUTHLOG = Path("/var/log/auth.log")
# F1 (Control Tower Phase 1a): a small queryable "last run" status the Control
# Tower reads — security findings are otherwise only Telegram + sentinels.
_DEFAULT_LAST_RUN = _ROOT / "data_store" / "security" / "last_run.json"
# Count of checks executed by the most recent run_pass(); set as a side effect so
# the F1 status writer reports checks_run without re-deriving the list.
_LAST_PASS_CHECK_COUNT = 0

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
    # SS-B: how the cooldown GROWS for a condition that is merely still present
    # (x realert_cooldown_sec, per repeat, last value capped) and how long an
    # absence must last before a return counts as a RECURRENCE rather than a
    # flicker. Config, not code, so the ladder can be retuned without a deploy.
    realert_backoff_multipliers: list = field(default_factory=lambda: [1, 4, 28])
    realert_presence_gap_sec: int = 300
    # Calendar expiry (26-Jul-2026). See check_nse_holiday_calendar for WHY this
    # non-security check is hosted here. lead_days counts back from 31-Dec, so 16
    # opens the reminder on 15-Dec of a 31-day December -- and on 15-Dec of EVERY
    # December, since nothing here is written in terms of a particular year.
    holiday_calendar_alert: bool = True
    holiday_calendar_lead_days: int = 16
    config_dir: str = str(_ROOT / "config")
    expected_ssh_keys: int = 1
    expected_key_fingerprint: str = ""          # committed single-key baseline (legacy)
    expected_key_fingerprints: list = field(default_factory=list)  # operator-override list (set by apply_operator_ssh_baseline)
    sudo_whitelist_prefixes: list = field(default_factory=lambda: [
        "/usr/bin/systemctl", "/bin/systemctl", "/usr/bin/grep", "/usr/bin/tail",
        "/usr/bin/cat", "/usr/bin/fail2ban-client", "/usr/sbin/augenrules",
        "/usr/sbin/auditctl", "/usr/sbin/ausearch", "/usr/bin/journalctl",
        "/usr/bin/ss", "/usr/bin/ps",
    ])
    watched_files: list = field(default_factory=list)
    authlog_path: str = str(_DEFAULT_AUTHLOG)
    authorized_keys_path: str = "/home/ubuntu/.ssh/authorized_keys"
    sentinel_dir: str = "data_store"
    # Phase 2 (copy protection): the on/off switch and the audit log we
    # correlate auditd copy_attempt events against. Read from the top-level
    # `copy_protection:` block (sibling of `security:`).
    copy_protection_enabled: bool = True
    copy_audit_log_path: str = str(_ROOT / "data_store" / "security" / "copy_audit.log")

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
                "realert_cooldown_sec", "realert_backoff_multipliers",
                "realert_presence_gap_sec",
                "holiday_calendar_alert", "holiday_calendar_lead_days",
                "config_dir",
                "expected_ssh_keys", "expected_key_fingerprint",
                "sudo_whitelist_prefixes", "watched_files", "authlog_path",
                "authorized_keys_path", "sentinel_dir",
            ):
                if f in sec and sec[f] is not None:
                    setattr(cfg, f, sec[f])
            cp = raw.get("copy_protection", {}) if isinstance(raw, dict) else {}
            if isinstance(cp, dict):
                if cp.get("enabled") is not None:
                    cfg.copy_protection_enabled = bool(cp["enabled"])
                if cp.get("audit_log_path"):
                    cfg.copy_audit_log_path = str(cp["audit_log_path"])
        except FileNotFoundError:
            _log.warning("security.yaml not found at %s; using defaults", path)
        except Exception as exc:  # never fail to run on a bad config
            _log.error("security.yaml load failed (%s); using defaults", exc)
        if not cfg.watched_files:
            cfg.watched_files = _default_watched_files()
        return cfg


def apply_operator_ssh_baseline(
    cfg: "SecConfig", path: Path = _DEFAULT_OPERATOR_SSH_BASELINE
) -> Optional[dict]:
    """Overlay an OPERATOR-approved SSH key baseline onto cfg, if present.

    Written by scripts/approve_ssh_keys.py after a LEGITIMATE key rotation. It
    lives outside git (data_store/security/ssh_key_baseline.json) so a deploy's
    `checkout -f` cannot revert it — fixing the root cause of the repeated
    false-positive CRITICALs. When present it REPLACES the committed
    expected_key_fingerprint(s) + count (operator intent is authoritative).
    Returns the loaded record, or None if the override is absent/unreadable.
    """
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    fps = rec.get("fingerprints")
    if isinstance(fps, list) and fps:
        cfg.expected_key_fingerprints = [str(f) for f in fps]
        cfg.expected_key_fingerprint = ""        # override REPLACES the single-key config
        try:
            cfg.expected_ssh_keys = int(rec.get("count", len(fps)))
        except (TypeError, ValueError):
            cfg.expected_ssh_keys = len(fps)
    return rec


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


_SS_PID_RE = re.compile(r"pid=(\d+)")


def established_ssh_connections() -> list[dict]:
    """[{"ip": str, "pid": Optional[int]}] for ESTABLISHED :22 peers (via `sudo -n
    ss`, NOPASSWD-whitelisted). sudo is required for the process/pid column: a
    plain `ss` run as `ubuntu` cannot see the pid of the root-owned sshd [priv]
    parent, only its own. [] on any error."""
    try:
        out = subprocess.run(
            ["sudo", "-n", "ss", "-tnHp", "state", "established", "( sport = :22 )"],
            capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
        )
        conns = []
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                peer = parts[3].rsplit(":", 1)[0].strip("[]")
                if not peer:
                    continue
                pids = _SS_PID_RE.findall(line)
                conns.append({"ip": peer, "pid": int(pids[-1]) if pids else None})
        return conns
    except Exception:
        return []


def established_ssh_peers() -> list[str]:
    """Peer IPs only (one entry per connection, dupes preserved) — thin
    back-compat wrapper over established_ssh_connections()."""
    return [c["ip"] for c in established_ssh_connections()]


_PS_LINE_RE = re.compile(r"^(\S+\s+\S+\s+\d+\s+\d+:\d+:\d+\s+\d+)\s+(.*)$")
_PS_SSHD_USER_RE = re.compile(r"^sshd:\s+(\S+)")


def process_start_and_user(pid: Optional[int]) -> tuple:
    """(start_time, user) for `pid` via `sudo -n ps -o lstart=,args=`
    (NOPASSWD-whitelisted). Best-effort: (None, None) if the pid is already
    gone/unreadable — common for a scanning connection that sshd closes
    within ~1-2s of the `ss` snapshot that caught it mid-preauth."""
    if not pid:
        return None, None
    try:
        out = subprocess.run(
            ["sudo", "-n", "ps", "-o", "lstart=,args=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL,
        )
        line = out.stdout.strip()
        if not line:
            return None, None
        m = _PS_LINE_RE.match(line)
        if not m:
            return None, None
        start = datetime.strptime(
            re.sub(r"\s+", " ", m.group(1)), "%a %b %d %H:%M:%S %Y"
        ).replace(tzinfo=_IST)
        user = None
        um = _PS_SSHD_USER_RE.match(m.group(2))
        if um:
            u = um.group(1).split("@")[0]
            user = None if u.lower() == "unknown" else u
        return start, user
    except Exception:
        return None, None


# GeoIP enrichment: no local MaxMind GeoLite2 DB is installed on the VM (needs
# a MaxMind account/license key — Rama would have to provision that). Falling
# back to the free, no-key ip-api.com lookup used in the earlier diagnostic.
# Best-effort only: never blocks or delays the alert on lookup failure, and
# cached (state["geoip_cache"]) so a recurring IP is not re-queried every pass.
_GEOIP_TIMEOUT_SEC = 2.0
_GEOIP_CACHE_TTL_DAYS = 30


def geoip_lookup(ip: str, cache: dict, now: datetime) -> str:
    """"<City>, <CC> — <ISP/Org>" for `ip`, "local" for private/loopback/link-local,
    or "" if the lookup fails/is inconclusive. Reads/writes `cache` in place."""
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            return "local"
    except ValueError:
        return ""
    entry = cache.get(ip)
    if entry:
        try:
            cached_ts = datetime.fromisoformat(entry.get("ts", ""))
            if (now - cached_ts).total_seconds() < _GEOIP_CACHE_TTL_DAYS * 86400:
                return entry.get("label", "")
        except (ValueError, TypeError):
            pass
    label = ""
    try:
        import requests
        resp = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,countryCode,city,isp,org"},
            timeout=_GEOIP_TIMEOUT_SEC,
        )
        data = resp.json()
        if data.get("status") == "success":
            place = ", ".join(p for p in (data.get("city"), data.get("countryCode")) if p)
            org = data.get("org") or data.get("isp") or ""
            label = f"{place} — {org}" if org else place
    except Exception:
        label = ""
    cache[ip] = {"label": label, "ts": now.isoformat()}
    return label


def session_ip_breakdown(conns: list[dict], lines: list[str], now: datetime,
                         lookback_hours: int = 24) -> dict:
    """Per-IP breakdown of the current established SSH connections: count,
    user(s), and an earliest-seen time — never "?".

    Primary source: "Accepted publickey" events for that IP in auth.log within
    `lookback_hours` (best-effort proxy for "session since" — auth.log has no
    session-close-aware mapping back to a specific `ss` connection, so this is
    an approximation good enough for an alert, not a security boundary).

    Fallback (no Accepted event — e.g. a scan/bot connection `ss` catches
    mid-preauth, never completes auth): the connection's sshd PID's own
    process-start time + attempted user via `process_start_and_user`. If even
    that PID has already exited (common — these connections live ~1-2s), the
    final fallback is `now` (we DID just observe the connection via `ss`, so
    that is still a true lower bound) with user "unauthenticated"."""
    since = now - timedelta(hours=lookback_hours)
    logins: dict[str, list[tuple[Optional[datetime], str]]] = {}
    for line in lines:
        ts = parse_line_ts(line)
        if ts is not None and ts < since:
            continue
        m = _ACCEPTED_RE.search(line)
        if m:
            user, ip = m.group(1), m.group(2)
            logins.setdefault(ip, []).append((ts, user))
    by_ip: dict[str, list[dict]] = {}
    for c in conns:
        by_ip.setdefault(c["ip"], []).append(c)
    out: dict = {}
    for ip, ip_conns in by_ip.items():
        events = logins.get(ip, [])
        users = sorted({u for _, u in events})
        times = [t for t, _ in events if t is not None]
        earliest = min(times) if times else None
        if earliest is None:
            for c in ip_conns:
                start, pid_user = process_start_and_user(c.get("pid"))
                if start is not None and (earliest is None or start < earliest):
                    earliest = start
                if pid_user and pid_user not in users:
                    users.append(pid_user)
            if earliest is None:
                earliest = now
        out[ip] = {"count": len(ip_conns), "users": sorted(users) or ["unauthenticated"],
                   "earliest": earliest}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2: copy-bypass auditd parsing (pure helpers; unit-tested)
# ─────────────────────────────────────────────────────────────────────────────

# ausearch -i stamp (interpreted). The year width varies by auditd build/locale
# — real aarch64 output is 2-digit "audit(06/20/26 10:34:10.979:14038)", some
# builds emit 4-digit — so accept either and try both strptime formats below.
_AUSEARCH_TS_RE = re.compile(
    r"audit\((\d{2}/\d{2}/\d{2,4} \d{2}:\d{2}:\d{2})[.\d]*:(\d+)\)")
_EXE_RE = re.compile(r"\bexe=(?:\"([^\"]+)\"|(\S+))")
_PROCTITLE_RE = re.compile(r"\bproctitle=(.+)$")
_EXECVE_ARG_RE = re.compile(r"\ba\d+=(?:\"([^\"]*)\"|(\S+))")


def parse_ausearch_execve(text: str, now: datetime) -> list[dict]:
    """Parse `ausearch -k copy_attempt -i` output into copy events.

    Each returned dict: {id, ts(datetime|None), exe, cmd}. Tolerant: events
    are grouped by the audit sequence id; the command line is taken from
    `proctitle=` (falling back to the EXECVE a0..aN args). Best-effort — an
    unparseable block is skipped, never raised."""
    events: dict[str, dict] = {}
    for line in text.splitlines():
        m = _AUSEARCH_TS_RE.search(line)
        if not m:
            continue
        ev_id = m.group(2)
        ev = events.setdefault(ev_id, {"id": ev_id, "ts": None, "exe": "", "cmd": ""})
        if ev["ts"] is None:
            for fmt in ("%m/%d/%y %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
                try:
                    naive = datetime.strptime(m.group(1), fmt)
                    ev["ts"] = naive.replace(tzinfo=now.tzinfo or _IST)
                    break
                except ValueError:
                    continue
        em = _EXE_RE.search(line)
        if em and not ev["exe"]:
            ev["exe"] = em.group(1) or em.group(2)
        pm = _PROCTITLE_RE.search(line)
        if pm and not ev["cmd"]:
            ev["cmd"] = pm.group(1).strip()
        if "type=EXECVE" in line and not ev["cmd"]:
            args = [a or b for a, b in _EXECVE_ARG_RE.findall(line)]
            if args:
                ev["cmd"] = " ".join(args)
    return [e for e in events.values() if e["exe"] or e["cmd"]]


def is_outbound_copy(exe: str, cmd: str) -> bool:
    """Heuristic: does this scp/sftp/rsync invocation move data OFF the VM?

    Conservative — only returns True on a positive outbound indicator so a
    PC->VM push (sshd-spawned `scp -t <dir>`, sink mode) does NOT false-fire."""
    base = (exe or "").rsplit("/", 1)[-1] or (cmd.split() or [""])[0]
    base = base.rsplit("/", 1)[-1]
    t = f" {cmd} "
    if base == "scp":
        if re.search(r"\s-[A-Za-z]*t", t):      # sink mode -> INBOUND (PC->VM)
            return False
        if re.search(r"\s-[A-Za-z]*f", t):      # source mode -> OUTBOUND (PC<-VM pull)
            return True
        return bool(re.search(r"\s\S+@\S+:|\s[\w.\-]+:", t))  # host:path destination
    if base == "rsync":
        return bool(re.search(r"\s\S+@\S+:|::", t))           # any remote endpoint
    if base == "sftp":
        return True                                            # VM-initiated sftp session
    return False


def ausearch_copy_attempts(since: Optional[datetime], now: datetime) -> list[dict]:
    """Query auditd for copy_attempt execve events since `since` (best-effort).

    Needs `sudo ausearch` (ausearch is on the security.yaml sudo whitelist, so
    this does not self-alert). Returns [] if auditd/ausearch is unavailable."""
    start = since or (now - timedelta(minutes=15))
    # `-ts recent` (~last 10 min) is a locale-proof keyword — a computed
    # MM/DD/YYYY can be rejected on 2-digit-year builds, silently disabling the
    # check. copy_attempt events are rare, so re-scanning 10 min is cheap and the
    # event-id dedup ledger (6h) prevents re-alerting. stdin=DEVNULL: ausearch
    # blocks reading stdin when it inherits a pipe/tty (it hung over ssh).
    cmd = ["sudo", "-n", "ausearch", "-k", "copy_attempt", "-i", "-ts", "recent"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                             stdin=subprocess.DEVNULL)
    except Exception:
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    events = parse_ausearch_execve(out.stdout, now)
    # keep only events at/after `since` (parsed ts; unparseable ts -> keep)
    return [e for e in events if e["ts"] is None or e["ts"] >= start]


def recent_allowed_copy_times(audit_log_path: str, now: datetime,
                              window_sec: int = 120) -> list[datetime]:
    """Timestamps of COPY_ALLOWED entries in the copy audit log within the
    window — used to recognise wrapper-authorised copies (so they are NOT
    flagged as bypass). Best-effort; [] on any error."""
    out: list[datetime] = []
    cutoff = now - timedelta(seconds=window_sec)
    try:
        lines = Path(audit_log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines[-500:]:  # bounded tail
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "COPY_ALLOWED":
            continue
        try:
            ts = datetime.fromisoformat(rec.get("ts", ""))
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            out.append(ts)
    return out


def active_token_windows(audit_log_path: str, now: datetime,
                         lookback_sec: int = 3600) -> list[tuple[datetime, datetime]]:
    """[(issued, expires)] windows from COPY_TOKEN_ISSUED entries within lookback.

    An outbound copy whose timestamp falls inside a window had a valid token, so
    it is NOT a bypass — this makes `request-copy` suppress the alert on a
    deliberate copy even in detection-only mode (before the wrappers exist)."""
    out: list[tuple[datetime, datetime]] = []
    cutoff = now - timedelta(seconds=lookback_sec)
    try:
        lines = Path(audit_log_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines[-500:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") != "COPY_TOKEN_ISSUED":
            continue
        try:
            issued = datetime.fromisoformat(rec.get("ts", ""))
            expires = datetime.fromisoformat(rec.get("expires_at", ""))
        except (ValueError, TypeError):
            continue
        if expires >= cutoff:
            out.append((issued, expires))
    return out


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
    # Allowed = the operator-override list (apply_operator_ssh_baseline) if set,
    # else the committed single expected_key_fingerprint. List-aware so a
    # legitimate multi-key rotation can be baselined without false positives.
    allowed = set(cfg.expected_key_fingerprints)
    if cfg.expected_key_fingerprint:
        allowed.add(cfg.expected_key_fingerprint)
    if allowed:
        unexpected = [f for f in fps if f not in allowed]
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


def check_active_sessions(cfg: SecConfig, state: dict, authlog_lines: list[str],
                          now: datetime) -> list[Finding]:
    conns = established_ssh_connections()
    peer_ips = [c["ip"] for c in conns]
    n = len(conns)
    state["last_session_count"] = n
    state["last_session_peers"] = sorted(set(peer_ips))
    if n > cfg.max_active_sessions:
        breakdown = session_ip_breakdown(conns, authlog_lines, now)
        geo_cache = state.setdefault("geoip_cache", {})
        per_ip = []
        for ip in sorted(breakdown):
            b = breakdown[ip]
            since = b["earliest"].strftime("%H:%M:%S")
            geo = geoip_lookup(ip, geo_cache, now)
            geo_part = f", {geo}" if geo else ""
            per_ip.append(f"{ip} x{b['count']} ({'/'.join(b['users'])}, since {since}{geo_part})")
        return [Finding("WARNING", f"sessions:{n}:{','.join(sorted(set(peer_ips)))[:60]}",
                        "Active SSH sessions over limit",
                        f"{n} active SSH sessions (limit {cfg.max_active_sessions}). "
                        + " | ".join(per_ip) +
                        ". (Alert only — not blocked.)")]
    return []


def check_copy_protection_switch(cfg: SecConfig, state: dict,
                                 now: datetime) -> list[Finding]:
    """Phase 2: alert on a copy_protection ON->OFF transition (the 'no bypassing
    the protector' guard). Transition-based, so a persistent OFF does not spam.
    First observation only seeds state (no alert)."""
    prev = state.get("copy_protection_enabled")
    cur = bool(cfg.copy_protection_enabled)
    state["copy_protection_enabled"] = cur
    if prev is None:
        return []  # baseline / first run — seed silently
    if prev and not cur:
        peers = sorted(set(established_ssh_peers()))
        who = ", ".join(peers) if peers else "(no active SSH peers seen)"
        return [Finding("CRITICAL", "copyprot:disabled",
                        "COPY PROTECTION DISABLED",
                        f"copy_protection.enabled was turned OFF at "
                        f"{now:%Y-%m-%d %H:%M:%S %Z}. Active SSH peer(s): {who}. "
                        f"VM->PC copies are now unrestricted. If this was not you, "
                        f"investigate immediately.")]
    if (not prev) and cur:
        return [Finding("INFO", "copyprot:enabled",
                        "Copy protection re-enabled",
                        f"copy_protection.enabled was turned back ON at "
                        f"{now:%Y-%m-%d %H:%M:%S %Z}.")]
    return []


def check_copy_bypass(cfg: SecConfig, state: dict, now: datetime) -> list[Finding]:
    """Phase 2: flag an OUTBOUND scp/sftp/rsync execve that ran WITHOUT a valid
    copy token (i.e. bypassed the copy-guard wrapper — e.g. a raw /usr/bin/scp).

    Correlates auditd `copy_attempt` events with the wrapper's COPY_ALLOWED
    audit entries; an outbound copy with no matching authorisation is a bypass.
    Best-effort: silently no-ops if auditd/ausearch is unavailable."""
    last = state.get("copy_bypass_last_check")
    since = None
    if last:
        try:
            since = datetime.fromisoformat(last)
        except (ValueError, TypeError):
            since = None
    state["copy_bypass_last_check"] = now.isoformat()

    events = ausearch_copy_attempts(since, now)
    if not events:
        return []
    allowed = recent_allowed_copy_times(cfg.copy_audit_log_path, now,
                                        window_sec=180)
    windows = active_token_windows(cfg.copy_audit_log_path, now)
    out: list[Finding] = []
    for ev in events:
        if not is_outbound_copy(ev["exe"], ev["cmd"]):
            continue
        ev_ts = ev["ts"]
        authorised = False
        if ev_ts is not None:
            authorised = (
                any(abs((ev_ts - a).total_seconds()) <= 180 for a in allowed)
                or any(start <= ev_ts <= end for (start, end) in windows)
            )
        if authorised:
            continue
        when = ev_ts.strftime("%Y-%m-%d %H:%M:%S") if ev_ts else "recently"
        out.append(Finding("CRITICAL", f"copybypass:{ev['id']}",
                           "COPY BYPASS DETECTED",
                           f"Outbound copy ran WITHOUT a valid token at {when}: "
                           f"{ev['cmd'] or ev['exe']}. Possible VM->PC exfiltration "
                           f"bypassing the copy gate — investigate."))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# NSE holiday calendar expiry (26-Jul-2026) — the one check here that is NOT
# about security
# ─────────────────────────────────────────────────────────────────────────────
#
# WHAT IT PREVENTS. `core/config_loader._CONFIG_FILES` resolves the holiday file
# as f"nse_holidays_{date.today().year}.yaml" at module import, and `load_all()`
# requires every registered file to exist. MEASURED (not reasoned) by patching
# date.today before the import: with only nse_holidays_2026.yaml on disk, the
# first 08:15 boot of 2027 raises
#     ConfigMissingError: Required config file not found: nse_holidays_2027.yaml
# and main returns 5. The service does not start. The fix is one committed file,
# but only Rama can produce it — NSE publishes the list ~Nov-Dec and a guessed
# calendar is far worse than a missing one, so this cannot be automated.
#
# ⭐ WHY IT LIVES IN THE SECURITY WATCHER, WHICH IS OTHERWISE NOT PART OF THE
# TRADING APP. Three properties are needed and only this process has all three:
#
#   1. IT REACHES RAMA WITH NOBODY DOING ANYTHING. The suite already carries a
#      dated tripwire for the same condition (test_config_loader.py, from 1-Dec)
#      but a test only fires if somebody runs it. In December that is a hope with
#      a date on it, not a reminder.
#   2. IT IS ALWAYS ON. security-watcher.service is a oneshot with RestartSec=60,
#      independent of cron and of the trading service — which matters most in the
#      exact failure it warns about, because on 1-Jan the trading service is the
#      thing that is dead. A boot-path check cannot warn you that the boot died.
#   3. IT ALREADY BACKS OFF. A missing file is by nature persistent, and this
#      module's presence ledger (_dedup, 26-Jul) reports a persistent condition
#      ONCE at full severity and then on a widening 6h/24h/7d ladder, never
#      CRITICAL twice. Hosting the reminder anywhere else would mean building a
#      second suppression mechanism — and 37 CRITICAL emails from ONE stale SSH
#      baseline is what that costs when it is missing.
#
# It resolves ITSELF: presence of the file is the whole clear condition. There is
# no acknowledgement, no flag, nothing for Rama to remember to switch off.


def _holiday_files_due(today: date, lead_days: int) -> list[int]:
    """The years whose nse_holidays_<year>.yaml must ALREADY be on disk, given today.

    PURE in `today` and free of any hard-coded year, so (a) the reminder can be
    driven to any date without patching a clock, and (b) December 2027 behaves
    exactly like December 2026 — otherwise the fix would be a one-character-
    different version of the bug, one year later.

      * the CURRENT year, always: if that file is absent the boot is already dead.
      * the NEXT year, once `lead_days` or fewer remain before 31-Dec.
    """
    due = [today.year]
    if (date(today.year, 12, 31) - today).days <= lead_days:
        due.append(today.year + 1)
    return due


def check_nse_holiday_calendar(cfg: SecConfig, now: datetime) -> list[Finding]:
    """CRITICAL while a REQUIRED nse_holidays_<year>.yaml is absent; silent the
    moment it exists. Two horizons, one shape (see the block comment above)."""
    if not cfg.holiday_calendar_alert:
        return []
    today = now.date()
    config_dir = Path(cfg.config_dir)
    out: list[Finding] = []
    for year in _holiday_files_due(today, cfg.holiday_calendar_lead_days):
        fname = f"nse_holidays_{year}.yaml"
        if (config_dir / fname).exists():
            continue
        shape = (
            f"in the same shape as the {today.year} file (a `holidays:` list of "
            f"date/name entries)" if year != today.year else
            "as a `holidays:` list of date/name entries"
        )
        if year == today.year:
            headline = (
                f"The 08:15 boot CANNOT START while this file is missing: load_all() "
                f"raises ConfigMissingError and main returns 5. This is not a warning "
                f"about the future — it is the current state of the machine."
            )
        else:
            days_left = (date(today.year, 12, 31) - today).days
            headline = (
                f"{days_left} day(s) of the {today.year} calendar remain. On "
                f"1-Jan-{year} the 08:15 boot will raise ConfigMissingError and the "
                f"service will NOT start. Acting now costs one commit; acting late "
                f"costs a trading day that begins with a dead service."
            )
        out.append(Finding(
            "CRITICAL", f"holidaycal:missing:{fname}",
            f"CONFIG: NSE holiday calendar {year} is not committed",
            f"config/{fname} does not exist on this machine.\n\n"
            f"{headline}\n\n"
            f"ACTION — commit NSE's PUBLISHED holiday list for {year} as "
            f"config/{fname}, {shape}, and deploy.\n\n"
            f"⛔ DO NOT invent, infer or extrapolate the dates, and do not copy the "
            f"previous year's file forward. A guessed calendar is far worse than a "
            f"missing one: the system would trade on a market holiday, or skip a real "
            f"trading day, and believe it was right. NSE publishes the following "
            f"year's list around Nov-Dec — if it is not published yet, wait for it.\n\n"
            f"This alert stops by itself as soon as the file exists. Nothing to "
            f"acknowledge, nothing to switch off. Until then it repeats on the "
            f"standard backoff (6h, then 24h, then weekly) and only this first one "
            f"is CRITICAL."))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch
# ─────────────────────────────────────────────────────────────────────────────

# ── Re-alert policy (SS-B, 26-Jul-2026) ──────────────────────────────────────
# The old ledger stored ONE float per finding key -- the last time it alerted --
# and compared it to a fixed cooldown. One number cannot express the difference
# between a condition that NEVER WENT AWAY and one that WENT AWAY AND CAME BACK,
# so it got both wrong, in opposite directions:
#
#   "still true"  -> a persistent BENIGN condition re-fired at full CRITICAL every
#                    cooldown, forever, with no decay/escalation/expiry. One stale
#                    SSH baseline produced 37 CRITICAL emails over 9 days and two
#                    key generations -- 45% of the entire delivered stream.
#   "true again"  -> a condition that CLEARED and RECURRED inside the cooldown was
#                    SILENTLY DROPPED. The louder half hid the dangerous half.
#
# The ledger now tracks PRESENCE: {first_seen, last_seen, last_alerted, repeats}.
#   * absent for > presence gap, then seen again  -> RECURRENCE: new episode,
#     alerted IMMEDIATELY at FULL severity (closes the silent half);
#   * seen continuously                           -> PERSISTENCE: the interval
#     widens along the ladder (1x, 4x, 28x = 6h/24h/7d, capped) and every repeat
#     after the first is DOWNGRADED out of CRITICAL and labelled as a repeat.
#
# WHY THE DOWNGRADE IS SAFE: every Finding.key encodes the IDENTITY of the
# condition, not just its type -- authkeys:unexpected:<fingerprints>,
# file:<label>:<sha12>, copybypass:<audit_event_id>, authkeys:hash:<sha12>. ANY
# change to what is wrong produces a DIFFERENT key => a new condition => full
# severity, immediately. Only a byte-identical condition is ever downgraded.
# (test_every_live_finding_key_carries_its_identity pins that premise.)
#
# IT DOES NOT GO QUIET (the trap in the obvious fix): the repeat still fires on
# the decaying ladder; data_store/security/last_run.json is written from the
# PRE-dedup findings on EVERY ~60s pass and now NAMES the persistent keys; and
# ops/control_tower/aggregator.read_security raises a finding whenever that file
# says clean=false. `--report` also prints live findings with no dedup at all.
_REALERT_BACKOFF_LADDER: tuple[float, ...] = (1.0, 4.0, 28.0)   # x realert_cooldown_sec
_PRESENCE_GAP_SEC = 300.0          # absence shorter than this is a flicker, not a clear
_LEDGER_RETENTION_SEC = 8 * 86400.0
_LEDGER_MAX_ENTRIES = 500          # rootspike:/failspike: keys rotate hourly
# A repeat is never CRITICAL. Kept inside the module's declared vocabulary
# (CRITICAL | WARNING | INFO) so _F1_SEV_RANK and the tower severity map still
# understand it.
_REPEAT_SEVERITY = {"CRITICAL": "WARNING", "WARNING": "WARNING", "INFO": "INFO"}


def _fmt_duration(seconds: float) -> str:
    s = max(0, int(seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _episode(entry) -> dict:
    """Normalise a ledger entry to the episode shape.

    Back-compat: the pre-SS-B ledger stored a bare float (the last alert time).
    Read it as an episode with an UNKNOWN presence history, so the first pass
    after this ships treats a still-present condition as a recurrence and alerts
    once at full severity before settling onto the ladder. One extra alert is the
    safe direction; one fewer is not.
    """
    if isinstance(entry, dict):
        return dict(entry)
    if isinstance(entry, (int, float)):
        return {"first_seen": float(entry), "last_seen": None,
                "last_alerted": float(entry), "repeats": 1}
    return {}


def _episode_age_ref(ep: dict) -> Optional[float]:
    """The timestamp a ledger entry is aged against (last_seen; last_alerted for a
    legacy entry that has never been seen under the new scheme)."""
    for field_name in ("last_seen", "last_alerted", "first_seen"):
        v = ep.get(field_name)
        if v is not None:
            return float(v)
    return None


def _as_repeat(f: Finding, ep: dict, now_ts: float, repeats: int) -> Finding:
    """A repeat of an UNCHANGED condition: never CRITICAL, and says so."""
    held = now_ts - float(ep.get("first_seen") or now_ts)
    return Finding(
        severity=_REPEAT_SEVERITY.get(f.severity, f.severity),
        key=f.key,
        title=f"{f.title} (STILL PRESENT)",
        body=(
            f"{f.body}\n\n"
            f"[REPEAT #{repeats + 1}] UNCHANGED for {_fmt_duration(held)}. The first "
            f"report went out at {f.severity}; repeats are downgraded so a persistent "
            f"condition cannot flood the CRITICAL channel, and the interval widens each "
            f"time. This is NOT a new event. If anything about the condition changes it "
            f"gets a new alert identity and fires at full severity again. Continuous "
            f"status, every pass, no dedup: data_store/security/last_run.json -> "
            f"persistent[]."
        ),
    )


def _prune_ledger(ledger: dict, now_ts: float) -> dict:
    """Age out cleared conditions and bound the file. Legacy float entries are
    CONVERTED here rather than dropped, so retention is uniform whether or not a
    key happened to fire during the upgrade pass."""
    keep: dict = {}
    for k, v in ledger.items():
        ep = _episode(v)
        if not ep:
            continue
        ref = _episode_age_ref(ep)
        if ref is None or (now_ts - ref) <= _LEDGER_RETENTION_SEC:
            keep[k] = ep
    if len(keep) > _LEDGER_MAX_ENTRIES:
        newest = sorted(keep.items(),
                        key=lambda kv: _episode_age_ref(kv[1]) or 0.0,
                        reverse=True)[:_LEDGER_MAX_ENTRIES]
        keep = dict(newest)
    return keep


def _dedup(findings: list[Finding], state: dict, cooldown: int, now_ts: float,
           *, backoff_ladder=None, presence_gap_sec=None) -> list[Finding]:
    """Emit a finding once per EPISODE, then on a widening ladder and never again
    as CRITICAL -- while treating a genuine RECURRENCE as the new event it is.

    Also publishes state["persistent_conditions"]: the keys present on this pass
    that are NOT first reports, so "what is still wrong" is written down even when
    nothing is sent.
    """
    ladder = tuple(float(m) for m in (backoff_ladder or _REALERT_BACKOFF_LADDER))
    if not ladder:
        ladder = _REALERT_BACKOFF_LADDER
    gap = float(_PRESENCE_GAP_SEC if presence_gap_sec is None else presence_gap_sec)
    ledger = dict(state.get("alerted") or {})
    fresh: list[Finding] = []
    persistent: list[str] = []

    for f in findings:
        ep = _episode(ledger.get(f.key))
        seen_before = ep.get("last_seen") if ep else None
        # Absent for at least one presence gap and now back = "true again", a NEW
        # episode -- not the same condition still running.
        if not ep or seen_before is None or (now_ts - float(seen_before)) > gap:
            ep = {"first_seen": now_ts, "last_alerted": None, "repeats": 0}
        ep["last_seen"] = now_ts
        repeats = int(ep.get("repeats") or 0)
        last_alerted = ep.get("last_alerted")

        if last_alerted is None:
            due = True
        else:
            mult = ladder[min(max(repeats - 1, 0), len(ladder) - 1)]
            due = (now_ts - float(last_alerted)) >= cooldown * mult

        if not due:
            persistent.append(f.key)
            ledger[f.key] = ep
            continue

        if repeats == 0:
            fresh.append(f)                      # first report of this episode: verbatim
        else:
            persistent.append(f.key)
            fresh.append(_as_repeat(f, ep, now_ts, repeats))
        ep["last_alerted"] = now_ts
        ep["repeats"] = repeats + 1
        ledger[f.key] = ep

    state["alerted"] = _prune_ledger(ledger, now_ts)
    state["persistent_conditions"] = sorted(set(persistent))
    return fresh


def _send(f: Finding, cfg: SecConfig) -> None:
    result = None
    try:
        from alerts.telegram_notifier import TelegramNotifier
        n = TelegramNotifier.from_env(logger=_log)
        if n is not None:
            result = n.send(severity=f.severity, title=f"🔒 {f.title}", body=f.body,
                            source_module="security_monitor")
    except Exception as exc:
        _log.error("security_monitor: telegram send failed: %s", exc)
    # TelegramNotifier.send() writes the CRITICAL sentinel itself (TG5) and returns
    # its path. Only write our OWN when that did not happen — notifier missing,
    # Telegram disabled via the master switch, or send() raised — so every CRITICAL
    # produces EXACTLY ONE sentinel -> one email (email is the sole channel while
    # Telegram is unavailable; no duplicate emails, no missed alerts).
    if f.severity == "CRITICAL" and not getattr(result, "sentinel_path", None):
        try:
            from alerts.critical import write_critical_sentinel
            write_critical_sentinel(title=f.title, body=f.body,
                                    source_module="security_monitor",
                                    sentinel_dir=cfg.sentinel_dir)
        except Exception as exc:
            _log.error("security_monitor: sentinel write failed: %s", exc)


def _append_copy_audit(cfg: SecConfig, event: str, now: datetime, **fields) -> None:
    """Append a JSON line to the copy audit log so the System Manager EOD summary
    (Phase 3) has a SINGLE source for copy-protection events. Best-effort."""
    rec = {"ts": now.isoformat(), "event": event, "source": "security_monitor"}
    rec.update(fields)
    try:
        p = Path(cfg.copy_audit_log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        _log.error("security_monitor: copy-audit append failed: %s", exc)


def _maybe_copy_audit(cfg: SecConfig, f: Finding, now: datetime) -> None:
    """Persist a freshly-alerted (post-dedup) copy-protection finding to the copy
    audit log — only the bypass / switch findings, keyed off the Finding.key."""
    if f.key.startswith("copybypass:"):
        event = "COPY_BYPASS_DETECTED"
    elif f.key == "copyprot:disabled":
        event = "COPY_PROTECTION_DISABLED"
    elif f.key == "copyprot:enabled":
        event = "COPY_PROTECTION_ENABLED"
    else:
        return
    _append_copy_audit(cfg, event, now, detail=f.body[:300])


# F1 records the source's NATIVE max severity (CRITICAL | WARNING | INFO). The
# native->tower mapping (WARNING->MEDIUM) lives in the Control Tower aggregator
# (ops/control_tower/severity.py) — ONE reviewable place that drives 1c paging —
# so the source stays in its own vocabulary. (Refined per the 1b §1A decision.)
_F1_SEV_RANK = {"CRITICAL": 3, "WARNING": 2, "INFO": 1}


def _write_last_run_status(path: Path, findings: list, checks_run: int,
                           now: datetime, persistent: Optional[list] = None) -> None:
    """F1 (Control Tower Phase 1a) — ADDITIVE side-artefact: write a small,
    queryable last-run status atomically (.tmp -> os.replace). It NEVER changes
    a finding, an alert, or the security state; best-effort (logs + swallows
    OSError) so it can never break a monitoring pass.

    SS-B (26-Jul-2026): it is written from the PRE-dedup findings on EVERY pass, so
    it reflects the CONDITION rather than the alert — which is what makes it safe
    for _dedup to stop re-alerting a persistent condition. `persistent` names the
    keys that are still present but no longer alerting each pass, so "what is
    still wrong" is answerable without an email. The Control Tower's security
    adapter (ops/control_tower/aggregator.read_security) raises a finding whenever
    clean is false, so this is a channel that is read daily, not just written."""
    max_sev = "INFO"
    for f in findings:
        s = str(getattr(f, "severity", "")).upper()
        if _F1_SEV_RANK.get(s, 0) > _F1_SEV_RANK.get(max_sev, 0):
            max_sev = s
    persistent_keys = sorted(str(k) for k in (persistent or []))
    payload = {
        "version": 1,
        "timestamp": now.isoformat(),
        "checks_run": int(checks_run),
        "findings_count": len(findings),
        "max_severity": max_sev,
        "clean": len(findings) == 0,
        "persistent": persistent_keys,
        "persistent_count": len(persistent_keys),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _log.error("security_monitor: last_run.json write failed: %s", exc)


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
    # On baseline, seed known IPs from the WHOLE current auth.log (not just the
    # 15-min window) so the first --watch doesn't alert on every past IP.
    ip_scan = scan_authlog(lines, datetime(1970, 1, 1, tzinfo=_IST)) if baseline else scan_window

    findings: list[Finding] = []
    checks = [
        lambda: check_authorized_keys(cfg, state),
        lambda: check_new_login_ips(cfg, ip_scan, state, baseline),
        lambda: check_failed_spike(cfg, scan_hour, now),
        lambda: check_root_probe_spike(cfg, scan_hour, now),
        lambda: check_sudo_events(cfg, scan_window),
        lambda: check_watched_files(cfg, state),
        lambda: check_active_sessions(cfg, state, lines, now),
        lambda: check_copy_protection_switch(cfg, state, now),   # Phase 2
        lambda: check_copy_bypass(cfg, state, now),              # Phase 2
        lambda: check_nse_holiday_calendar(cfg, now),            # calendar expiry
    ]
    global _LAST_PASS_CHECK_COUNT
    _LAST_PASS_CHECK_COUNT = len(checks)
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
    apply_operator_ssh_baseline(cfg)   # overlay the durable operator override, if any
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

    fresh = _dedup(findings, state, cfg.realert_cooldown_sec, now.timestamp(),
                   backoff_ladder=cfg.realert_backoff_multipliers,
                   presence_gap_sec=cfg.realert_presence_gap_sec)
    for f in fresh:
        _send(f, cfg)
        _maybe_copy_audit(cfg, f, now)   # Phase 3: persist copy events for the EOD summary
        _log.warning("security_monitor ALERT [%s] %s — %s", f.severity, f.title, f.body)
    save_state(args.state, state)
    # F1 (ADDITIVE): queryable last-run status for the Control Tower. Written
    # AFTER alerts/state so it can never influence a security decision.
    _write_last_run_status(_DEFAULT_LAST_RUN, findings, _LAST_PASS_CHECK_COUNT, now,
                           persistent=state.get("persistent_conditions"))
    _log.info("security_monitor: pass complete (%d finding(s), %d new alert(s))",
              len(findings), len(fresh))
    # exit 0 always (watcher must keep running); severity is in the alerts.
    return 0


if __name__ == "__main__":
    sys.exit(main())
