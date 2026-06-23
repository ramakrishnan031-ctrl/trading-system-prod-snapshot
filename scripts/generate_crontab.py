#!/usr/bin/env python3
"""
scripts/generate_crontab.py — Self-maintaining cron: the executable generator.

The cron registry (config/cron_registry.yaml) is the SINGLE EXECUTABLE SOURCE OF
TRUTH. This module composes each crontab line DETERMINISTICALLY from a job's
generation fields, and (for the one-time bootstrap) parses the LIVE crontab back
into those fields. compose() and parse() are exact inverses — the `--selftest`
mode proves it byte-for-byte against the live crontab, which is the migration gate
that must be GREEN before auto-install is ever armed.

Determinism: output depends ONLY on the registry fields + the fixed constants
below — no timestamps, randomness, or host lookups — so the equality guard never
rejects a valid push. (`--selftest` asserts generate-twice → byte-identical.)

Generation fields (per CronJob):
  cron_expression : literal 5-field cron time (authoritative; `schedule` is docs)
  env_wrapper     : none | python | python_nopath | claude_cd  (the line prefix)
  command         : the core command AFTER the prefix (verbatim, byte-exact)
  log_target      : verbatim redirect ('>> logs/x.log 2>&1') or None
  marker_name     : writes cron_marks/<name>.done ; None = no marker
  enabled         : only enabled jobs are generated + drift-checked

Modes:
  --selftest [--crontab FILE]  parse→compose each live line; assert byte-equal
  --generate                   emit canonical crontab from the registry (stdout)
  --bootstrap --crontab FILE   parse live crontab → enriched-registry YAML (stdout)
  --check --crontab FILE       diff generate(registry) vs live (bidirectional)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Fixed deployment constants (the ONLY host-specific strings; no runtime lookup) ──
PROJ = "/home/ubuntu/systems/trading-system"
VENV_PY = "/home/ubuntu/systems/venv/bin/python"
CLAUDE_DIR = "/home/ubuntu/tools/claude"
MARKS = f"{PROJ}/data_store/cron_marks"
SHELL_LINE = "SHELL=/bin/bash"

_ENV_PREFIX = {
    "none": "",
    "python": f"cd {PROJ} && set -a && . ./.env && set +a && PYTHONPATH=. {VENV_PY} ",
    "python_nopath": f"cd {PROJ} && set -a && . ./.env && set +a && {VENV_PY} ",
    "claude_cd": f"cd {CLAUDE_DIR} && ",
}
VALID_ENV_WRAPPER = tuple(_ENV_PREFIX.keys())

# 5 whitespace-separated cron fields, then the command body.
_CRON_RE = re.compile(r"^(\S+ \S+ \S+ \S+ \S+) (.*)$")
# Trailing exit-code marker (byte-exact form shared by every marker line).
_MARKER_RE = re.compile(
    r"; rc=\$\?; mkdir -p " + re.escape(MARKS)
    + r'; echo "\$rc \$\(date -Iseconds\)" > ' + re.escape(MARKS) + r"/([A-Za-z0-9_]+)\.done$"
)
# Trailing redirect '>> <path> 2>&1' (path has no spaces).
_LOG_RE = re.compile(r" (>> \S+ 2>&1)$")


def _marker_block(name: str) -> str:
    return (f'; rc=$?; mkdir -p {MARKS}; echo "$rc $(date -Iseconds)" '
            f"> {MARKS}/{name}.done")


def compose(job) -> str:
    """Build one crontab line from a job's generation fields (byte-stable)."""
    env = _value(job, "env_wrapper") or "none"
    if env not in _ENV_PREFIX:
        raise ValueError(f"{_value(job,'name')!r}: bad env_wrapper {env!r}")
    cron_expr = _value(job, "cron_expression")
    command = _value(job, "command")
    if not cron_expr or not command:
        raise ValueError(f"{_value(job,'name')!r}: missing cron_expression/command")
    line = f"{cron_expr} {_ENV_PREFIX[env]}{command}"
    log_target = _value(job, "log_target")
    if log_target:
        line += f" {log_target}"
    marker = _value(job, "marker_name")
    if marker:
        line += _marker_block(marker)
    return line


def parse(line: str) -> dict:
    """Decompose a live crontab command line into generation fields.

    Inverse of compose(): parse(compose(j)) == j's fields, and
    compose(parse(line)) == line (asserted by --selftest)."""
    m = _CRON_RE.match(line)
    if not m:
        raise ValueError(f"unparseable cron line: {line!r}")
    cron_expr, body = m.group(1), m.group(2)

    # env_wrapper: longest-prefix match (python before python_nopath).
    env = "none"
    for name in ("python", "python_nopath", "claude_cd"):
        if body.startswith(_ENV_PREFIX[name]):
            env, body = name, body[len(_ENV_PREFIX[name]):]
            break

    marker_name = None
    mk = _MARKER_RE.search(body)
    if mk:
        marker_name = mk.group(1)
        body = body[: mk.start()]

    log_target = None
    lg = _LOG_RE.search(body)
    if lg:
        log_target = lg.group(1)
        body = body[: lg.start()]

    return {
        "cron_expression": cron_expr,
        "env_wrapper": env,
        "command": body,
        "log_target": log_target,
        "marker_name": marker_name,
    }


def _value(job, attr):
    """Field access that works for both a dict and a pydantic CronJob."""
    if isinstance(job, dict):
        return job.get(attr)
    return getattr(job, attr, None)


# ── crontab text helpers ────────────────────────────────────────────────────
def _command_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        s = raw.rstrip("\n")
        if not s or s.startswith("#") or s.startswith("SHELL="):
            continue
        out.append(s)
    return out


def selftest(crontab_text: str) -> int:
    """Parse→compose each live command line; assert byte-for-byte equality.
    Determinism check: compose() of the parsed fields twice is identical."""
    lines = _command_lines(crontab_text)
    failures = []
    for i, line in enumerate(lines, 1):
        try:
            fields = parse(line)
            rebuilt = compose(fields)
            rebuilt2 = compose(parse(line))
        except Exception as exc:  # noqa: BLE001
            failures.append((i, line, f"ERROR {type(exc).__name__}: {exc}"))
            continue
        if rebuilt != line:
            failures.append((i, line, f"ROUND-TRIP MISMATCH -> {rebuilt!r}"))
        elif rebuilt != rebuilt2:
            failures.append((i, line, "NON-DETERMINISTIC compose()"))
    total = len(lines)
    if failures:
        print(f"SELFTEST FAILED: {len(failures)}/{total} lines did NOT round-trip "
              f"byte-for-byte (each = a STOP-AND-REPORT oddball):")
        for i, line, why in failures:
            print(f"  line {i}: {why}\n     LIVE: {line!r}")
        return 1
    print(f"SELFTEST PASSED: all {total} live crontab lines round-trip "
          f"byte-for-byte (parse<->compose are exact inverses; compose deterministic).")
    return 0


# ── Header for the generated canonical (preserves the FIX-189 / env-export docs) ──
_CANON_HEADER = """\
# Cron schedule for Trading System v2 — GENERATED ARTIFACT. DO NOT HAND-EDIT.
# Source of truth: config/cron_registry.yaml. Regenerate with:
#   python scripts/generate_crontab.py --generate > deploy/cron/trading-system.cron
# A pre-receive guard rejects a hand-edited canonical; the daily drift-check
# compares the live `crontab -l` against this (bidirectional).
#
# FIX-189: SHELL=/bin/bash forces bash (dash's `.` can't source a bare relative
# path). Python jobs source .env via `set -a && . ./.env && set +a` so secrets
# are EXPORTED into the child process (bare VAR=value otherwise stays shell-local).
# Markers `; rc=$?; ... > cron_marks/<name>.done` let the Cron Officer read shell/
# unmonitored jobs at EOD. All times IST (Asia/Kolkata).
"""

# ── Bootstrap (ONE-TIME migration: live crontab -> enriched registry) ────────
_META_FIELDS = ("script", "schedule", "type", "critical", "market_day_only",
                "cadence", "monitored", "weekday", "day_of_month", "category",
                "heartbeat_required", "detection_method", "excluded_reason")
_GEN_FIELDS = ("cron_expression", "command", "env_wrapper", "log_target", "marker_name")

# non-marker commands whose job name != script basename
_SPECIAL_NAME = {
    "cron_officer.py --briefing": "cron_officer_briefing",
    "cron_officer.py --eod-summary": "cron_officer_eod",
    "system_manager.py": "system_manager_eod",
    "generate_screened_stocks_csv.py": "generate_screened_csv",
    "-m reports.daily_report": "daily_report",
    "reports/daily_review.py": "daily_review",
}


def _script_basename(command: str) -> str:
    toks = command.split()
    if toks[0] == "-m":
        return toks[1]
    base = toks[0].rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".py") else base


def resolve_job_name(f: dict) -> str:
    """Map a parsed live line to its registry job name (markers are authoritative)."""
    cx, cmd, mk = f["cron_expression"], f["command"], f["marker_name"]
    if f["env_wrapper"] == "claude_cd":
        mn, hr = cx.split()[0], cx.split()[1]
        return f"claude_heartbeat_{int(hr):02d}{int(mn):02d}"
    if mk == "db_retention":
        return "db_retention_vacuum" if "--vacuum" in cmd else "db_retention"
    if mk:
        return mk
    for needle, name in _SPECIAL_NAME.items():
        if needle in cmd:
            return name
    return _script_basename(cmd)


def _human_sched(cron_expr: str) -> str:
    mn, hr = cron_expr.split()[0], cron_expr.split()[1]
    if mn.isdigit() and hr.isdigit():
        return f"{int(hr):02d}:{int(mn):02d} daily"
    return cron_expr


def _index_by_name(text: str) -> dict:
    out = {}
    for line in _command_lines(text):
        f = parse(line)
        nm = resolve_job_name(f)
        if nm in out:
            raise SystemExit(f"STOP-AND-REPORT: duplicate resolved name {nm!r}: {line!r}")
        out[nm] = f
    return out


def bootstrap(live_text: str, registry_path: Path, canonical_text: str):
    """Build the enriched jobs dict from LIVE (+ existing metadata). STOP on any
    registry job that has no live line (would be a drop) or unexpected live job."""
    import yaml
    existing = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    existing_jobs = existing.get("jobs", {})
    officer = existing.get("officer", {})
    live = _index_by_name(live_text)
    canon = _index_by_name(canonical_text)

    def merge(meta: dict, gen: dict, *, personal=False) -> dict:
        job = {k: meta[k] for k in _META_FIELDS if meta.get(k) is not None}
        job["enabled"] = True                      # reconciliation: declarative, no behaviour change
        job["personal_tooling"] = bool(meta.get("personal_tooling", personal))
        for k in _GEN_FIELDS:
            if gen.get(k) is not None:
                job[k] = gen[k]
        return job

    enriched, matched = {}, set()
    for name, meta in existing_jobs.items():
        if name == "db_retention":
            for sub in ("db_retention", "db_retention_vacuum"):
                if sub not in live:
                    raise SystemExit(f"STOP-AND-REPORT: db_retention split — {sub} not in live")
                enriched[sub] = merge(meta, live[sub]); matched.add(sub)
        elif name == "sentinel_retention":          # registry-only (+1); enrich from canonical
            if name not in canon:
                raise SystemExit("STOP-AND-REPORT: sentinel_retention not in canonical")
            enriched[name] = merge(meta, canon[name])
        elif name in live:
            enriched[name] = merge(meta, live[name]); matched.add(name)
        else:
            raise SystemExit(f"STOP-AND-REPORT: registry job {name!r} has no live line (drop risk)")

    for name, f in live.items():                    # live-not-registry => must be claude only
        if name in matched:
            continue
        if not name.startswith("claude_heartbeat_"):
            raise SystemExit(f"STOP-AND-REPORT: unexpected live-not-registry job {name!r}")
        enriched[name] = {
            "script": f["command"], "schedule": _human_sched(f["cron_expression"]),
            "type": "shell", "critical": False, "market_day_only": False,
            "cadence": "daily", "monitored": False, "detection_method": "none",
            "excluded_reason": "personal tooling heartbeat — no trading deliverable",
            "enabled": True, "personal_tooling": True,
            "cron_expression": f["cron_expression"], "command": f["command"],
            "env_wrapper": f["env_wrapper"], "log_target": f["log_target"],
        }
    return enriched, officer


def emit_registry_yaml(enriched: dict, officer: dict) -> str:
    import yaml
    body = yaml.safe_dump({"jobs": enriched, "officer": officer},
                          sort_keys=False, default_flow_style=False, width=4096)
    header = ("# Cron Job Registry — SINGLE EXECUTABLE SOURCE OF TRUTH (Phase 3).\n"
              "# Read by core/cron_registry.py; generates deploy/cron/trading-system.cron\n"
              "# via scripts/generate_crontab.py --generate. Edit jobs HERE, regenerate.\n"
              "# Generation fields (cron_expression/command/env_wrapper/log_target/marker_name)\n"
              "# are authoritative; `schedule` is documentation. enabled:false = not generated.\n\n")
    return header + body


def load_jobs(registry_path: Path) -> list[dict]:
    import yaml
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    out = []
    for name, job in (data.get("jobs") or {}).items():
        j = dict(job); j["name"] = name
        out.append(j)
    return out


def generate_canonical(jobs: list[dict]) -> str:
    """Emit the canonical crontab text (enabled jobs only), deterministically."""
    lines = [_CANON_HEADER, SHELL_LINE, ""]
    for j in jobs:
        if not j.get("enabled", True):
            continue
        lines.append(f"# {j['name']}  [{j.get('schedule','')}]")
        lines.append(compose(j))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def gate_check(jobs: list[dict], live_text: str) -> int:
    """PROOF 1 (set-based): zero drops; the only addition is sentinel_retention."""
    gen = {compose(j): j["name"] for j in jobs if j.get("enabled", True)}
    live = set(_command_lines(live_text))
    drops = live - set(gen)                          # live job NOT generated => DROP (forbidden)
    adds = {ln: gen[ln] for ln in (set(gen) - live)} # generated, not yet live
    ok = True
    if drops:
        ok = False
        print(f"GATE FAIL — {len(drops)} DROP(S) (live job not generated):")
        for d in sorted(drops):
            print(f"  DROP: {d!r}")
    unexpected = {ln: nm for ln, nm in adds.items() if nm != "sentinel_retention"}
    if unexpected:
        ok = False
        print(f"GATE FAIL — {len(unexpected)} unexpected addition(s) (expected only sentinel_retention):")
        for ln, nm in unexpected.items():
            print(f"  ADD[{nm}]: {ln!r}")
    if ok:
        print(f"GATE PASS — zero drops; {len(adds)} reviewed addition(s): "
              f"{sorted(adds.values())} (expected: ['sentinel_retention']).")
        return 0
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Self-maintaining cron generator.")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--bootstrap", action="store_true", help="live + registry -> enriched registry YAML (stdout)")
    p.add_argument("--generate", action="store_true", help="enriched registry -> canonical crontab (stdout)")
    p.add_argument("--gate", action="store_true", help="PROOF 1: generate(registry) vs live (zero drops, +sentinel)")
    p.add_argument("--crontab", type=Path, help="live crontab file")
    p.add_argument("--registry", type=Path, default=_ROOT / "config" / "cron_registry.yaml")
    p.add_argument("--canonical", type=Path, default=_ROOT / "deploy" / "cron" / "trading-system.cron")
    args = p.parse_args(argv)

    if args.selftest:
        if not args.crontab or not args.crontab.exists():
            print("--selftest needs --crontab FILE"); return 2
        return selftest(args.crontab.read_text(encoding="utf-8"))

    if args.bootstrap:
        if not args.crontab or not args.crontab.exists():
            print("--bootstrap needs --crontab FILE (the live crontab)"); return 2
        enriched, officer = bootstrap(
            args.crontab.read_text(encoding="utf-8"), args.registry,
            args.canonical.read_text(encoding="utf-8"))
        sys.stdout.write(emit_registry_yaml(enriched, officer))
        return 0

    if args.generate:
        sys.stdout.write(generate_canonical(load_jobs(args.registry)))
        return 0

    if args.gate:
        if not args.crontab or not args.crontab.exists():
            print("--gate needs --crontab FILE (the live crontab)"); return 2
        return gate_check(load_jobs(args.registry), args.crontab.read_text(encoding="utf-8"))

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
