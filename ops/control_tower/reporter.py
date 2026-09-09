"""ops/control_tower/reporter.py -- Control Tower Phase 1c reporter.

A) TELEGRAM (noise-controlled DELTA): push ONLY a CRITICAL (unless ACKNOWLEDGED)
   or a NEW/CHANGED HIGH (newly-OPEN or re-opened this run, unacked). MEDIUM /
   LOW / INFO never push. Nothing qualifies -> NO push (clean days are silent;
   the heartbeat + last_successful_run prove the run happened).
B) PULL REPORT (HTML + CSV -> report_<date>.*): full picture; never pushed.
"""
from __future__ import annotations

import csv
import html
import os
from pathlib import Path
from core.account_registry import primary_account_tag

# tower severity -> TelegramNotifier tier
_NOTIFY_TIER = {"CRITICAL": "CRITICAL", "HIGH": "WARNING"}


def _identity(f):
    return (f.category, f.resource_name, f.reason)


def select_push(detected, prior: dict):
    """Findings that qualify for a Telegram push, given the pre-run status map.
    CRITICAL always (unless it was ACKNOWLEDGED); HIGH only if new/re-opened."""
    out = []
    for f in detected:
        ident = _identity(f)
        if prior.get(ident) == "ACKNOWLEDGED":
            continue
        is_new = ident not in prior
        is_reopened = prior.get(ident) == "RESOLVED"
        if f.severity == "CRITICAL" or (f.severity == "HIGH" and (is_new or is_reopened)):
            out.append(f)
    return out


def format_telegram(overall: str, qualifying) -> tuple[str, str, str]:
    tier = "CRITICAL" if any(f.severity == "CRITICAL" for f in qualifying) else "WARNING"
    lines = [f"OVERALL STATUS: {overall}", ""]
    for f in qualifying:
        lines.append(f"[{f.severity}] {f.category}: {f.resource_name}")
        lines.append(f"  {f.reason}")
        if f.recommended_action:
            lines.append(f"  -> {f.recommended_action}")
    return tier, f"Control Tower — {len(qualifying)} issue(s)", "\n".join(lines)


def maybe_push(notifier, overall: str, qualifying) -> bool:
    if not qualifying or notifier is None:
        return False
    tier, title, body = format_telegram(overall, qualifying)
    notifier.send(severity=tier, title=f"[{primary_account_tag()}] {title}", body=body,
                  source_module="control_tower")
    return True


# ── pull report ───────────────────────────────────────────────────────────────
def largest_paths(root, n=20):
    """(folders, files): top-n by size. Folders = top-level dirs + data_store/*."""
    root = Path(root)
    folders = []
    for base in [root, root / "data_store"]:
        if not base.exists():
            continue
        for entry in os.scandir(base):
            if entry.is_dir():
                folders.append((_dir_size(Path(entry.path)), entry.path))
    files = []
    for dirpath, _dn, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                files.append((p.stat().st_size, str(p)))
            except OSError:
                pass
    folders.sort(reverse=True)
    files.sort(reverse=True)
    return folders[:n], files[:n]


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def _mb(b):
    return f"{b / 1024 / 1024:,.1f}MB"


def _backup_growth(trends_rows):
    """trends_rows: list of (date, disk_used_pct, backup_mb, log_mb, db_mb) desc.
    <7 days history -> 'Trend unavailable (insufficient history)'."""
    if len(trends_rows) < 7:
        return "Trend unavailable (insufficient history)"
    today, week = trends_rows[0], trends_rows[-1]
    if today[2] is None or week[2] is None:
        return "Trend unavailable (insufficient history)"
    return f"{today[2] - week[2]:+.0f}MB over {len(trends_rows)} days ({week[0]} -> {today[0]})"


def write_pull_report(report_dir, date_str, *, overall, score, source_status,
                      fresh_status, findings_rows, freshness_rows, trends_rows,
                      root) -> dict:
    rdir = Path(report_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    html_path = rdir / f"report_{date_str}.html"
    csv_path = rdir / f"report_{date_str}.csv"

    by_status = {"OPEN": [], "ACKNOWLEDGED": [], "RESOLVED": []}
    for r in findings_rows:
        by_status.setdefault(r[7], []).append(r)
    folders, files = largest_paths(root)

    def esc(x):
        return html.escape(str(x if x is not None else ""))

    out = [f"<h1>Control Tower — {date_str}</h1>",
           f"<h2>OVERALL STATUS: {esc(overall)} (health score {score})</h2>",
           "<h3>Sections</h3><ul>",
           f"<li>security: {esc(source_status.get('security'))}</li>",
           f"<li>cron: {esc(source_status.get('cron'))}</li>",
           f"<li>config: {esc(source_status.get('config'))}</li>",
           f"<li>excursion: {esc(source_status.get('excursion'))}</li>",
           f"<li>freshness: {esc(fresh_status)}</li>",
           f"<li>disk: {esc(trends_rows[0][1]) if trends_rows else '?'}% used</li></ul>"]

    for st in ("OPEN", "ACKNOWLEDGED", "RESOLVED"):
        out.append(f"<h3>Findings — {st} ({len(by_status.get(st, []))})</h3><table border=1>"
                   "<tr><th>id</th><th>sev</th><th>category</th><th>resource</th>"
                   "<th>reason</th><th>first_seen</th></tr>")
        for r in by_status.get(st, []):
            out.append(f"<tr><td>{r[0]}</td><td>{esc(r[1])}</td><td>{esc(r[2])}</td>"
                       f"<td>{esc(r[3])}</td><td>{esc(r[4])}</td><td>{esc(r[5])}</td></tr>")
        out.append("</table>")

    out.append("<h3>Freshness</h3><table border=1><tr><th>stage</th><th>status</th>"
               "<th>expected_by</th><th>actual_at</th><th>delay_min</th></tr>")
    for fr in freshness_rows:
        out.append(f"<tr><td>{esc(fr['stage'])}</td><td>{esc(fr['status'])}</td>"
                   f"<td>{esc(fr['expected_by'])}</td><td>{esc(fr['actual_at'])}</td>"
                   f"<td>{esc(fr['delay_minutes'])}</td></tr>")
    out.append("</table>")

    out.append("<h3>Trends (disk% / backup / log / db)</h3><table border=1>"
               "<tr><th>date</th><th>disk%</th><th>backup_mb</th><th>log_mb</th><th>db_mb</th></tr>")
    for t in trends_rows:
        out.append(f"<tr><td>{esc(t[0])}</td><td>{esc(t[1])}</td><td>{esc(t[2])}</td>"
                   f"<td>{esc(t[3])}</td><td>{esc(t[4])}</td></tr>")
    out.append(f"</table><p>Backup growth: {esc(_backup_growth(trends_rows))}</p>")

    out.append("<h3>Largest folders (reference)</h3><ul>")
    out += [f"<li>{_mb(sz)} — {esc(p)}</li>" for sz, p in folders]
    out.append("</ul><h3>Largest files (reference)</h3><ul>")
    out += [f"<li>{_mb(sz)} — {esc(p)}</li>" for sz, p in files]
    out.append("</ul>")

    html_path.write_text("\n".join(out), encoding="utf-8")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "severity", "category", "resource_name", "reason",
                    "first_seen", "last_seen", "status", "acked_at", "resolved_at"])
        w.writerows(findings_rows)
    return {"html": str(html_path), "csv": str(csv_path)}
