# Trading System v2

An automated **NSE intraday** trading system. It receives scanner alerts by webhook,
screens and scores them, sizes the position against real capital, places the order with
the broker, and manages the exit — unattended, on a VM, every trading day.

> ## ⚠️ THIS SYSTEM SPENDS REAL MONEY
>
> It has been **live since 11-May-2026**. It places real orders against a real broker
> account with real capital. There is no "just testing" mode on the VM.
>
> **Before you change anything, read these three things — in this order:**
> 1. **[PATHS.md](PATHS.md)** — what changed recently, and where everything lives.
> 2. **[docs/SYSTEM_MAP.md](docs/SYSTEM_MAP.md)** — the operational map. **Reading it
>    before any VM/system work is non-negotiable**, and it opens with a HOW-TO-READ index.
> 3. **[DEPLOYMENT.md](DEPLOYMENT.md)** — how a change actually reaches the VM.
>
> **The rules that exist because they were learned the hard way:**
> - **Deploy ≠ restart.** A push does not activate a systemd unit change.
> - **Off-market only.** Never deploy inside **09:00–15:30** (market) or **15:30–17:05**
>   (the EOD cron window). After 17:05, before 08:15.
> - **NEVER flatten a position from DB state.** Read **broker** positions first — the DB
>   lags broker truth. The mandatory 8-step runbook is in SYSTEM_MAP.md.
> - **Runtime beats commit-existence.** A fix commit is not a fixed system. A control
>   that reports PASS may be summing nothing.
> - **Every reject must map to a known reason.** An unmapped reject is a STOP.

---

## How a trade happens

```
  Chartink scanner ──webhook──▶  signals/      receive, authenticate, queue
                                    │
                                    ▼
                                 screening/    secondary screener + hard gates + score
                                    │
                                    ▼
                                 capital/      risk gates ─ position sizing ─ RESERVE
                                    │          (fund_manager holds the 3-balance
                                    │           invariant: available+reserved+used==total)
                                    ▼
                                 orders/       place entry, then SL + target legs
                                    │
                                    ▼
                                 broker/       Zerodha adapter, rate limits, fill monitor
                                    │
                                    ▼
                                 capital/      COMMIT reserved ─▶ used, then release on exit

  Cross-cutting:
    capital/kill_switch.py   SOFT_KILL blocks new entries; HARD_KILL blocks everything
                             and flattens the book. The emergency path.
    core/state_store.py      SQLite (WAL) — the single source of DB truth
    core/config_loader.py    one atomic load of every YAML, pydantic extra="forbid"
    alerts/                  Telegram + email; a CRITICAL always has a fallback path
```

**Entry point:** `main.py` (`--mode paper|live`). It boots, reconciles against the
broker, starts the workers, and self-exits cleanly after the service window.

## Layout

| Package | What lives there |
|---|---|
| `signals/` | webhook receiver, signal processor/queue |
| `screening/` | secondary screener, hard gates |
| `capital/` | **fund_manager, kill_switch, position_sizer, risk_engine, allocator** |
| `orders/` | order placer/manager/reconciler, exit protocols |
| `broker/` | Zerodha adapter, order monitor, position helpers |
| `data/` | live feed, candles |
| `core/` | state_store, config_loader, schema, events, time authority |
| `strategies/` | strategy definitions + control gates |
| `regime/`, `sr_detector/`, `v3_chain/`, `allocation/` | V3 engines — **shadow/default-OFF** |
| `ops/`, `ops_dashboard/` | Control Tower monitoring; the read-only ops GUI |
| `scripts/`, `tasks/`, `deploy/`, `config/` | cron jobs, deployment, YAML config |
| `tests/` | ~4,800 tests — the safety net |

## Running the tests

```bash
python -m pytest tests/ -q          # the full suite (~13 min) — run this before any deploy
python -m pytest tests/unit -q      # faster inner loop
```

**Run the FULL suite before a deploy, not just the suites you touched.** A scoped run
stays green while a real regression sits in a suite you didn't think to run — that has
happened more than once.

Some tests fail **on Windows only** (environment, not defects) and the count is
*time-of-day dependent*. Compare against a baseline taken **at the same hour**, and
attribute anything new against the true pre-change tree (`git checkout <base> -- <files>`)
— **never `git stash`**, which only stashes *uncommitted* work and silently leaves your
change on both sides of the comparison.

## Configuration

All YAML in `config/`, loaded atomically by `core/config_loader.py` with pydantic
`extra="forbid"` — an unknown key fails the load rather than being ignored. Secrets live
in `.env` (never committed; a pre-commit scanner blocks them). Flags are default-OFF: a
scaffold ships inert, soaks in shadow, and only then gets a Rama-gated flip.

## Agents

`AGENTS.md` / `GEMINI.md` are charters for the AI agents that work on this repo. They are
not human onboarding — this file is.
</content>
