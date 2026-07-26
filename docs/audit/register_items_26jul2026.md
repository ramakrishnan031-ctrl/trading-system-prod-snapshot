# Register items — 26-Jul-2026

⚠️ **The operator register (`MASTER_PENDING_REGISTER_*.txt`) is external and git-excluded**, so
these are written here to be transcribed. One line each, as asked; the evidence is underneath.

---

## C1 — `utils.startup_checks.check_holiday_calendar()`

> 📋 **REGISTER LINE:** *`check_holiday_calendar` is NOT an eighth built-and-never-run instance —
> it runs on every boot. 5 of its 8 outcomes are unreachable (an upstream loader enforces the same
> things harder); the 3 reachable ones are the ONLY production validation of holiday-list CONTENT
> anywhere. KEEP IT. The one dead branch that mattered (file missing) is now covered by
> `security_monitor.check_nse_holiday_calendar`, where it can actually run.*

### ⚠️ The premise was wrong, and checking it changed the answer

The batch carried it as *"built-but-never-run — an eighth instance of the class."* It is called:

```
main.py:2069                run_all_startup_checks(...)          unconditional, every boot
utils/startup_checks.py:1621    holiday_cal = check_holiday_calendar(config_dir, today_date, logger)
```

So the function runs. What is true — and is the sharper finding — is that **most of its
outcomes cannot be reached from there**, because `load_all()` at `main.py:1816` has already
killed the boot 253 lines earlier for the same input. A downstream check cannot catch an
upstream death.

### Measured, branch by branch

Each malformed calendar was put through `NseHolidaysConfig` — which is exactly what `load_all`
does per file — rather than reasoned about:

| outcome of `check_holiday_calendar` | reaches `:1621`? | why |
|---|---|---|
| file missing | 🔴 **NO** | `load_all` raises `ConfigMissingError`, main returns 5 |
| YAML parse error | 🔴 **NO** | `load_all` calls `yaml.safe_load` first |
| missing `holidays:` key | 🔴 **NO** | MEASURED — pydantic `ValidationError` |
| `holidays` not a list | 🔴 **NO** | MEASURED — pydantic `ValidationError` |
| invalid date format | 🔴 **NO** | MEASURED — pydantic `ValidationError` |
| **fewer than `min_holidays` (10)** | ✅ **YES** | MEASURED — pydantic has no count floor |
| **duplicate holiday date** | ✅ **YES** | MEASURED — pydantic does not dedupe |
| **date not in the current year** | ✅ **YES** | MEASURED — pydantic validates the type, not the year |

**3 of 8 reachable.** And those three are not decoration: a truncated list, a double-counted
closed day, or a stray 2019 date are exactly the ways a *present* calendar can be wrong, and
**nothing else in production looks for them.** (The suite checks the same three properties in
`test_real_nse_holidays_yaml_loads` — but that is the suite, and this batch is about the
difference between a check that runs and a check somebody runs.)

### Decision: KEEP, and the dead branch is now wired elsewhere

⭐ The file-missing branch was the one worth having, and it was the one that could never fire.
It now fires — from `scripts/security_monitor.check_nse_holiday_calendar`, in a separate,
always-on process that does not die when the trading service does. **On the morning the boot
fails, that is the only thing on the box that can say why.** See `cbcad2c`.

⚠️ **What is NOT fixed, and stays recorded:** the same data read twice in one boot still has two
OPPOSITE failure policies — the SU6 guard reads the YAML directly and *swallows* a missing file
(`main.py:1769`, "proceed with startup") while `load_all`'s blanket "all 8 must exist" turns the
same absence into a dead boot. **The file is a HARD boot requirement serving a SOFT purpose.**
Nothing on the boot path was touched: v45 stays Tuesday's single variable.

---

## C2 — the two remaining "weaker" paper gaps

From the 26-Jul sweep (`paper_fidelity_gaps_26jul2026.md`), classified WEAKER — they make paper
prove *less*, they do not make it prove the *opposite*. Recorded so nobody later reads paper's
green as coverage that is not there.

> 📋 **REGISTER LINE:** *`zerodha_adapter.cancel_order` returns success unconditionally in paper ⇒
> **a paper run can never demonstrate a cancel that FAILS**, so CHECK1's mid-fill deferral
> (`check1_mid_fill_defer_sec`) has no paper rehearsal at all: turning that knob on is an
> unrehearsed path straight into live. It ships at 0.0 = OFF and 0 is proven a no-op (`81f9372`),
> so today there is no false green — only absent coverage.*

> 📋 **REGISTER LINE:** *`zerodha_adapter.get_trades()` returns `[]` in paper ⇒ **CHECK1 rungs 1–2
> and the closure-source CONTRADICTION rule are unreachable in a paper session**, however many
> paper sessions run. ⛔ The contradiction rule is a SAFETY property — never offer "paper saw no
> contradiction" as evidence for it. Second-order: `_resolve_exit_price` also loses its
> broker-trade rung in paper and silently falls through to LTP.*

⛔ **Neither is proposed for a fix here.** The cheapest remedy, if one is ever wanted, is not a
richer simulation but a **refusal seam** (make one call fail on demand) — one seam serves
`cancel_order`, `modify_order`, `place_order` and the GTT trio, and changes no default behaviour.
That is a design conversation, not a batch item.

---

## ~~Available, deliberately NOT taken~~ — BUILT 26-Jul (`e6ade32`)

> 📋 **REGISTER LINE — CLOSED.** *Raised here as "available, Rama's call": the December reminder
> sent exactly ONE email, because `_send` writes a sentinel only for CRITICAL and the backoff
> downgrades everything after the first notice — so missing one message on 15-Dec meant the next
> thing heard was a service that would not start. **Approved and built the same night.** The
> finding key now carries a PHASE from a closed three-value set, so the ledger re-alerts at full
> severity on the escalation — that is the "a changed condition re-alerts" rule applying, not a
> way around "never CRITICAL twice", and the CLOSED set is what bounds it (a DATE in the key was
> measured at 17 CRITICALs). MEASURED window: **7 alerts, 2 CRITICAL** — 15-Dec notice, 28-Dec
> final.*

Standing backstop meanwhile, so this is a nicety and not a hole: `last_run.json` reports
`clean=false` on every ~60 s pass while the file is absent, and
`ops/control_tower/aggregator.read_security` turns that into a finding that appears in **every
daily pull report** until it lands (pushed to Telegram once, by `select_push`'s new/reopened
rule, then silent).

---

## Cross-reference

The **WRONG**-class gap — the one that makes paper print a PASS rather than prove less — is the
overnight carry, and it has its own report: `paper_overnight_carry_26jul2026.md`.
