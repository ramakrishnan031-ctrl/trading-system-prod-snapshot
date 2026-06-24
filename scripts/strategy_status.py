"""
scripts/strategy_status.py  --  Slice 2 strategy status table.

Builds the per-strategy "will it trade today?" table from the SAME resolver the
entry gate uses (``strategies.control.strategy_will_trade``), so the table can
NEVER disagree with live behaviour. Renders three views:

  * render_html       -> Gmail-safe inline-CSS table (full 15 rows, pills)
  * render_telegram   -> compact phone view (counts + names)
  * render_plaintext  -> plain mirror (HTML fallback)

The "Type" column shows each strategy's TRUE declared intent (from the raw YAML,
BEFORE the force_intraday_only load-time rewrite), so a DELIVERY strategy reads
DELIVERY even while the breaker is forcing it to trade intraday. The VERDICT,
however, is computed on the EFFECTIVE (post-rewrite) intent — exactly what the
gate sees — so verdict == gate. A footnote flags any delivery strategy currently
running as intraday under the breaker.

Malformed YAML -> a CONFIG ERROR row (never crash the report).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from strategies.control import strategy_will_trade
from strategies.schema import validate_strategy

_WILL = "WILL TRADE"
_WONT = "WON'T TRADE"
_ERR = "CONFIG ERROR"


@dataclass(frozen=True)
class StatusRow:
    strategy: str
    type: str               # TRUE declared intent: INTRADAY | DELIVERY | "?"
    master: str             # system trade_type
    switch: str             # ENABLED | DISABLED | "?"
    verdict: str            # WILL TRADE | WON'T TRADE | CONFIG ERROR
    reason: str
    start: str
    end: str
    sl_pct: str
    rr: str
    direction: str

    @property
    def will_trade(self) -> bool:
        return self.verdict == _WILL


def _effective_intent(true_intent: str, force_intraday_only: bool) -> str:
    """Mirror the loader's force_intraday_only rewrite: DELIVERY -> INTRADAY when
    the breaker is on. The resolver must see this (post-rewrite) intent so the
    table verdict matches the gate."""
    if force_intraday_only and true_intent != "INTRADAY":
        return "INTRADAY"
    return true_intent


def build_status_rows(
    config_dir: Path, *, trade_type: str, force_intraday_only: bool
) -> List[StatusRow]:
    """Build one StatusRow per strategy YAML, sorted WILL TRADE first then by
    Type then name. Each file is validated independently so a single malformed
    YAML becomes a CONFIG ERROR row instead of crashing the whole table."""
    strat_dir = config_dir / "strategies"
    rows: List[StatusRow] = []
    for path in sorted(strat_dir.glob("*.yaml")):
        name = path.stem
        try:
            cfg = validate_strategy(path)   # RAW (true intent, not force-rewritten)
        except Exception as exc:  # noqa: BLE001 — never crash the report
            rows.append(StatusRow(
                strategy=name, type="?", master=trade_type, switch="?",
                verdict=_ERR, reason=f"config error: {str(exc)[:80]}",
                start="—", end="—", sl_pct="—", rr="—", direction="—",
            ))
            continue
        true_intent = cfg.intent
        eff_cfg = cfg.model_copy(
            update={"intent": _effective_intent(true_intent, force_intraday_only)}
        )
        v = strategy_will_trade(
            eff_cfg, trade_type=trade_type, force_intraday_only=force_intraday_only
        )
        rows.append(StatusRow(
            strategy=cfg.name,
            type=true_intent,
            master=trade_type,
            switch="ENABLED" if cfg.enabled else "DISABLED",
            verdict=_WILL if v.will_trade else _WONT,
            reason=v.reason,
            start=cfg.entry_start_time,
            end=cfg.entry_end_time,
            sl_pct=(f"{cfg.sl_pct * 100:.2f}%" if cfg.sl_pct else "—"),
            rr=f"{cfg.tgt_risk_reward:g}",
            direction=cfg.direction,
        ))

    # WILL TRADE first, then CONFIG ERROR last; within a group by Type then name.
    def _key(r: StatusRow):
        rank = 0 if r.verdict == _WILL else (2 if r.verdict == _ERR else 1)
        return (rank, r.type, r.strategy)
    rows.sort(key=_key)
    return rows


def summary_line(rows: List[StatusRow], trade_type: str) -> str:
    will = sum(1 for r in rows if r.will_trade)
    wont = sum(1 for r in rows if r.verdict == _WONT)
    err = sum(1 for r in rows if r.verdict == _ERR)
    tail = f" · {err} CONFIG ERROR" if err else ""
    return f"Today: {will} WILL TRADE · {wont} WON'T TRADE{tail} (master: {trade_type})"


def footnote(rows: List[StatusRow], force_intraday_only: bool) -> Optional[str]:
    """Flag delivery strategies currently trading as intraday (breaker ON)."""
    if not force_intraday_only:
        return None
    deliv_live = sorted(r.strategy for r in rows
                        if r.type == "DELIVERY" and r.will_trade)
    if not deliv_live:
        return None
    return ("Note: %s — DELIVERY-type, trading as INTRADAY because "
            "force_intraday_only is ON (no CNC placed)." % ", ".join(deliv_live))


# ── rendering ────────────────────────────────────────────────────────────────

def _esc(x) -> str:
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(rows: List[StatusRow], trade_type: str,
                force_intraday_only: bool) -> str:
    """Gmail-safe inline-CSS table — all rows, no truncation (no-skip policy)."""
    head_cells = ["S.No", "Strategy", "Type", "Master", "Switch", "Verdict",
                  "Start", "End", "SL%", "Target(R:R)", "Direction"]
    ths = "".join(
        f'<th style="padding:6px 8px;text-align:left;border-bottom:2px solid '
        f'#bbb;font-size:12px;color:#333;">{_esc(h)}</th>' for h in head_cells
    )
    trs = []
    for i, r in enumerate(rows, 1):
        stripe = "#ffffff" if i % 2 else "#f5f7fa"
        if r.verdict == _WILL:
            pill = ('<span style="background:#1e8e3e;color:#fff;padding:2px 8px;'
                    'border-radius:10px;font-size:11px;">WILL TRADE</span>')
        elif r.verdict == _ERR:
            pill = ('<span style="background:#c5221f;color:#fff;padding:2px 8px;'
                    'border-radius:10px;font-size:11px;">CONFIG ERROR</span>')
        else:
            pill = ('<span style="background:#9aa0a6;color:#fff;padding:2px 8px;'
                    'border-radius:10px;font-size:11px;">WON\'T TRADE</span>')
        switch_col = (r.switch if r.switch == "ENABLED"
                      else f'<span style="color:#9aa0a6;">{_esc(r.switch)}</span>')
        cells = [str(i), r.strategy, r.type, r.master, switch_col, pill,
                 r.start, r.end, r.sl_pct, r.rr, r.direction]
        tds = "".join(
            f'<td style="padding:6px 8px;font-size:12px;color:#202124;">{c}</td>'
            if j in (4, 5) else  # switch/verdict already contain HTML
            f'<td style="padding:6px 8px;font-size:12px;color:#202124;">{_esc(c)}</td>'
            for j, c in enumerate(cells)
        )
        trs.append(f'<tr style="background:{stripe};">{tds}</tr>')
    note = footnote(rows, force_intraday_only)
    note_html = (f'<div style="font-size:11px;color:#5f6368;margin-top:6px;">'
                 f'{_esc(note)}</div>') if note else ""
    return (
        f'<div style="font-weight:600;font-size:13px;margin:4px 0;">'
        f'{_esc(summary_line(rows, trade_type))}</div>'
        f'<table style="border-collapse:collapse;width:100%;font-family:Arial,'
        f'sans-serif;"><thead><tr>{ths}</tr></thead><tbody>'
        f'{"".join(trs)}</tbody></table>{note_html}'
    )


def compact_lists(rows: List[StatusRow]):
    """Raw name lists (will, wont, err) for the Telegram briefing to format with
    its OWN MarkdownV2 escaper — strategy names contain underscores, which are
    MarkdownV2-special, so they must NOT be injected pre-formatted."""
    will = [r.strategy for r in rows if r.will_trade]
    wont = [r.strategy for r in rows if r.verdict == _WONT]
    err = [r.strategy for r in rows if r.verdict == _ERR]
    return will, wont, err


def render_telegram(rows: List[StatusRow], trade_type: str) -> str:
    """Compact phone view: counts + names (no full table). PLAIN text (standalone
    / tests); the Cron Officer briefing builds its own MarkdownV2-escaped version
    from ``compact_lists`` instead of using this."""
    will = [r.strategy for r in rows if r.will_trade]
    wont = [r.strategy for r in rows if r.verdict == _WONT]
    err = [r.strategy for r in rows if r.verdict == _ERR]
    lines = [f"*Strategy Status* (master: {trade_type})"]
    lines.append(f"✅ WILL TRADE ({len(will)}): " + (", ".join(will) if will else "—"))
    lines.append(f"⛔ WON'T TRADE ({len(wont)}): " + (", ".join(wont) if wont else "—"))
    if err:
        lines.append(f"⚠️ CONFIG ERROR ({len(err)}): " + ", ".join(err))
    lines.append("_Full table → email_")
    return "\n".join(lines)


def render_plaintext(rows: List[StatusRow], trade_type: str) -> str:
    out = [summary_line(rows, trade_type)]
    for i, r in enumerate(rows, 1):
        out.append(f"{i:2d}. {r.strategy:30s} {r.type:8s} {r.switch:8s} "
                   f"{r.verdict:11s} ({r.reason})")
    return "\n".join(out)
