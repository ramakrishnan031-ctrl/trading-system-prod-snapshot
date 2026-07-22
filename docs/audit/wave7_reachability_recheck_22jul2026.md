# Wave-7 — reachability re-check of the 10 remaining items (22-Jul-2026)

**Read-only. Reachability only — the defects' existence is not re-litigated (done 21/22-Jul).** Every
reachability sentence below M-O5 was inherited from the 04-Jul audit and never independently checked;
three checked this week (M-U1, M-O8, M-O5) were each wrong. This is the fourth check. Evidence from the
record (`mode=ro`) and 30 days of logs where possible, not config intent. No fixes, no designs, no tier
changes applied to the register.

## ⭐ HIGHER THAN ASSUMED — read this first

**M-O4 — the "one gate covers all three" Tier-C claim is WRONG for M-O4.** M-C2 and M-O7 are gated by
the system's own flags; **M-O4 is gated by the broker's position book**, which a **human same-day CNC
buy can populate independent of `delivery_enabled`**. `_promote_limits_to_market` (`eod_squareoff.py:1560-1567`)
builds `current_qty` keyed by symbol **with no product filter** — unlike phase-1's E.5 filter which
restricts to `product in ("MIS","CO")` (`:1044`). So if a system MIS symbol's phase-1 LIMIT fills during
the grace window (MIS→0) **and** the same symbol carries a non-zero CNC/NRML position from *any* source,
phase-2 reads `remaining = current_qty[symbol] > 0` and fires a **spurious MIS MARKET → naked reverse**.
- *Consequence:* HIGH (naked reverse — same class as M-O5).
- *Reachability:* **LOW but LIVE NOW, not behind the delivery flip** — it needs a human same-day CNC
  position in a stock the system is intraday-trading, present at 15:17 with that trade's LIMIT pending.
  A multi-way coincidence, but not zero, and **not** removed by `force_intraday_only`/`delivery_enabled`.
- **⚠️ Rama can close it in one answer:** does this Zerodha account ever see manual/human CNC (delivery)
  trades on the same day? If never, M-O4 is inert in practice. If it can, M-O4 has a live path today.
- **Do NOT attach M-O4 to Slice 2.5 as "inert until delivery."** M-C2 and M-O7 belong there; M-O4 does
  not share their gate.

---

## The ten, item by item

### Tier B (assumed live, ordinary)

| Item | 04-Jul/triage assumption | Evidence (record/log/code) | Verdict |
|---|---|---|---|
| **M-O9** trailed-SL slippage vs `sl_initial` | MODERATE-HIGH, "every trailed-SL exit" | **`sl_trail_count = 0` for all 395 trades** (mode=ro, 15-Jun→22-Jul). The SL has **never trailed** → the trailed-SL slippage path never executes → nothing to corrupt. | **LOWER — effectively inert.** Arms only if the trail engine ever fires (0 in the whole book). |
| **M-A2** synchronous alert sends block reconnection | LOW (needs a reconnect + Telegram slow) | **0 reconnect events in 30 days of logs.** The trigger (a KiteTicker reconnect) has never occurred. | **LOWER (confirmed).** |
| **M-K3** `connect()` lacks `foreign_keys=ON` | LOW-MODERATE | The FK-relevant writer is **`eod_cleanup`, which uses `StateStore`** (FK on — proven by the 15-Jul FK crash + its own `:229` comment "foreign_keys=ON, so a bare DELETE rolls back"), **not** `db_connect.connect()`. The `connect()` callers (`capture_metrics_baseline`, `gemini_*`) write **analytics tables** (no FK relationships) or read. `check_vm_state` writes trades/orders but via **raw `sqlite3.connect`** (a different concern) and is a manual script. **No cron uses `connect()` for FK-relevant main-table writes.** | **LOWER — latent.** No reachable FK-off write path today. |
| **M-S3** single-worker pool poison iff a step hangs | VERY LOW (pure arithmetic on pre-fetched data) | **0 step timeouts in 30 days** (`future.result(timeout=5)` would log "timed out after"). No screening step has ever hung. | **UNCHANGED (now verified VERY LOW).** |
| **M-D1** candle volume semantics | LATENT (only on MODE_FULL) | `subscribe()` hardcodes **`set_mode(KiteTicker.MODE_LTP)`** (`live_feed.py:182`); "MODE_LTP default" (`:169`). **MODE_FULL is used nowhere**, and no path promotes a token to it — a code change would be required. | **LOWER — latent behind a code change**, not a config/runtime state. |
| **M-K2** dead G5 per-strategy-window audit | consequence ~nil "because all strategies use the default window" | ⚠️ **The premise is FALSE.** All 16 strategies set **`entry_start_time: "09:25"`** (`config/strategies/*.yaml`), *before* the global `entry_start: 10:00` — the exact condition G5 exists to WARN about. `system_config.yaml:31-33` says so explicitly ("the 16 strategies declaring 09:25 … the floor binds … inert TODAY"). So the dead audit **does** suppress a WARN that would fire for all 16. | **PREMISE CORRECTED.** The consequence is *not* nil — but it is **low-value** (the 09:25 is intentional and the 10:00 floor handles it, so the WARN would be informational). Not a tier move; the reasoning was wrong. |

### Tier C (assumed inert behind the delivery gate — verified per-item, NOT as a group)

| Item | Assumed gate | Evidence (gate checked independently) | Verdict |
|---|---|---|---|
| **M-C2** delivery cap TOCTOU | `force_intraday_only` | `is_delivery_entry = sizing_result.bucket == "positional"` (`risk_engine.py:240`); `bucket` is **derived from intent** (`position_sizer` PS7). `force_intraday_only` **dormants all DELIVERY strategies** (`control.py:90`) → no positional signal reaches the sizer → `bucket` is never "positional". Gate is the **system's own flag on its own signal flow.** | **UNCHANGED — genuinely behind `force_intraday_only`.** |
| **M-O7** GTT-exit skips release | `delivery_enabled` + `force_intraday_only` | The whole CNC-GTT path (`CncGttPlacer`) is constructed with `delivery_enabled` and is **"Inert until a DELIVERY intent flows (gated by delivery_enabled + force_intraday_only)"** (`main.py:2517-2518`). **Double-gated.** | **UNCHANGED — strongest gate of the three.** |
| **M-O4** EOD promotion naked reverse | `force_intraday_only` | **See the HIGHER section.** Gate is the **broker position book, not the system flag** — product-blind `current_qty` (`:1563-1567`) + a human same-day CNC path. | **HIGHER — not behind the delivery flip.** |
| **M-O8** conditionally armed on CO usage | needs a code change to switch to CO | Record: **0 CO orders / 395 LIMIT_TRIPLE**. Root cause: 13 strategies declare `order_protocol: CO_PLUS_TGT`, but that field is **dead config** — OP9 (`order_placer.py:31-32`): protocol is "determined by intent → `default_protocol` (LIMIT_TRIPLE by default); **per-strategy config via scanner_configs is 'Future'**." `main.py` passes **no `default_protocol`** and there is no config key feeding it → the hardcoded LIMIT_TRIPLE governs. Switching to CO ⇒ wire the per-strategy routing **or** pass `default_protocol=CO_PLUS_TGT` in `main.py` — **a code change either way.** | **UNCHANGED — confirmed: no config flip alone arms CO.** |

---

## Bottom line

- **One item is HIGHER: M-O4.** Its Tier-C "inert until delivery" label is wrong — it is gated by the
  broker position book (a human same-day CNC coincidence), live now, low-probability. It must **not** be
  bundled into Slice 2.5's delivery coupling, and Rama's answer to "does this account ever see manual CNC?"
  settles whether it is inert-in-practice or a very-low-reachability live item near M-O5's class.
- **Four are LOWER than assumed** — M-O9 (0/395 trails), M-A2 (0 reconnects/30d), M-K3 (no FK-write cron
  uses `connect()`), M-D1 (MODE_LTP hardcoded; MODE_FULL needs code).
- **Three are UNCHANGED and now verified** — M-S3 (0 timeouts/30d), M-C2 (genuinely `force_intraday_only`),
  M-O7 (double-gated), plus **M-O8 confirmed** (CO needs a code change; CO_PLUS_TGT is dead config).
- **One premise was wrong but low-consequence** — M-K2 (strategies use 09:25, not the default; the dead
  audit suppresses a real but informational WARN).

**Net: the queue below M-O5 is no longer inherited — it is verified.** The only reordering candidate is
M-O4, and it turns on one operational fact only Rama has. Everything else is unchanged or lower, which is
the result that lets the queue stop being re-examined. The week's three-for-three wrong-reachability
record is now four-checked: three moved, this pass moved one more (M-O4) and confirmed the rest.

*Read-only throughout; `mode=ro` DB snapshots + 30-day log greps + deployed-tree code reads; no code,
config, schema, or state changed; the service and the EOD path were not touched.*
