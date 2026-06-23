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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Self-maintaining cron generator.")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--crontab", type=Path, help="live crontab file (for selftest/bootstrap/check)")
    args = p.parse_args(argv)

    if args.selftest:
        if not args.crontab or not args.crontab.exists():
            print("--selftest needs --crontab FILE")
            return 2
        return selftest(args.crontab.read_text(encoding="utf-8"))

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
