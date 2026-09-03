# DESIGN — the forced-exit path · 03-Sep-2026

**Status: DRAFT FOR RED-TEAM. ⛔ No code written. ⛔ Nothing deployed.**
Evidence base: `docs/incident/2026-09-03_naked_position_ANANTRAJ.md` and
`docs/incident/2026-09-03_ANANTRAJ_broker_book.json`.
Every claim below traces to a measurement in those two files.

---

## 0 · SCOPE

**In scope:** the two exit paths that can leave a position naked —
`order_reconciler._check9_missing_exits` and `orders/mis_autosquareoff.py` —
plus the order-state staleness that disabled the working remedy.

**⛔ Out of scope, deliberately:** no parallel exit subsystem · no refactor of
`order_placer` · no change to entry, sizing, strategy or capital · no change to
`eod_squareoff` (🔬 its 26 completed exits are LIMIT and it works) · D3 alert text
and D6 dead columns are recorded, ⛔ not fixed here.

---

## 1 · ROOT CAUSES — ⭐ TWO, ⛔ NOT FIVE

The five filed defects reduce to two independent causes plus one cross-cutting
weakness. ⭐ They are independent: **fixing either alone still leaves the incident
reproducible.**

### RC-1 · PAYLOAD — two sites construct an order the API always refuses
🔬 `orders WHERE order_type='MARKET'` = **0 rows of 1315**. The system has never
recorded placing a MARKET order. Both MARKET fills on 03-Sep came from the
broker's **GTT engine** and from **the UI** — ⛔ neither through `place_order`.
🔬 Exactly two sites can emit a raw MARKET exit:
- `mis_autosquareoff.py:71` (`PASS_2_EXIT_PROTOCOL`) and `:546` — ⛔ the module does
  not even import `marketable_limit_price`;
- `order_reconciler.py` check9 (~3000) — ⛔ no LTP fetch, no conversion.
⚠️ **Residual class:** every *converting* site falls back to raw MARKET when LTP is
missing ⇒ 🔴 **"no quote" silently means "no exit"**.

### RC-2 · STATE — exit decisions are made on local order state that lags
🔬 D2: verification read **27–53 ms** after cancel; terminal at **+1.115 s**.
🔬 §4.1: the recovery SL's local row stayed non-terminal for **7 m 01 s** after the
exchange cancelled it, because **nothing polls a non-entry order between placement
and a terminal-parent sweep**. That is what disabled G5b.
🔴 ⭐ **The decisive observation: the one component that read the broker (CHECK9) was
correct; every component that read local state was wrong.**

### RC-3 · cross-cutting — no error classification
🔬 D5: 8 identical validation rejections over **111.4 s**. A validation error is
deterministic; retrying it is guaranteed waste at the worst possible moment.

---

## 2 · 🔴 THE CONSTRAINT THAT OUTRANKS EVERYTHING ELSE

🔬 Last emergency attempt **15:12:09**; 👤 Rama's manual fill **15:12:11** — **2 s**.

⭐ **The only reason there was no double sell is that the exit was broken.**
⇒ ⭐ Fixing RC-1 *removes that accidental protection* and puts a never-before-run
path live, retrying every ~14 s, in a system with a measured position-state lag of
14 s to 7 m.

**CONSTRAINT (non-negotiable):** every exit attempt must re-read the **broker's**
position for that symbol **immediately before submitting**, and abort if flat or if
quantity differs. ⛔ Internal idempotency is insufficient — **the racing party is a
human, and no internal lock sees him.**

⇒ ⭐ This constraint is *why* RC-2 must be fixed **before or with** RC-1, ⛔ never
after. Shipping RC-1 alone makes the system more dangerous, not less.

---

## 3 · THE DESIGN

### 3.1 · RC-1 — one exit-order constructor, used by both sites
⭐ Use the mechanism the repo already has and already proves in production
(`eod_squareoff`'s 26 LIMIT exits): fresh LTP → `marketable_limit_price(side, ltp,
EMERGENCY_EXIT_BUFFER_PCT, tick)` → `order_type="LIMIT"`.
- ⭐ Both defect sites call **the same** constructor. ⛔ Not two implementations.
- 🔴 ⭐ **The no-LTP fallback must NOT be raw MARKET.** 🔬 That fallback is guaranteed
  to be rejected, so it is not a fallback — it is a silent failure. ⭐ Options for
  red-team: (a) abort + escalate loudly (the `t2` script's choice: *"NEVER falls
  back to MARKET"*); (b) MARKET **with** an explicit `market_protection`. ⭐ (a) is
  preferred because it needs no unverified parameter.
- ⚠️ **`market_protection` is NOT adopted on reasoning.** 🔬 Both successful MARKET
  fills report `market_protection: 0`, so the response field is uninformative and
  **the required value is unverified for this account**. 📄 This path has been
  "fixed" twice from a reasoned symptom (01-Jul tag length; and the 10-Jul canary
  was documented yet never applied here) and broke both times. ⇒ ⭐ **If a MARKET
  variant is ever used it must first be proven against the live account.**

### 3.2 · RC-2 — decide from the broker, not from the local table
- ⭐ **`_verify_cancelled`**: keep the mechanism (it already reads broker history —
  ⛔ do not replace it), add a **bounded settle window with re-reads**. 🔬 Terminal
  appeared at **+1.115 s**; the poll was at **+27–53 ms**. ⛔ No fixed sleep — poll
  until terminal or a deadline, then decide.
- 🔴 ⭐ **Reorder: verify the cancel BEFORE the position is left unprotected.** Today
  cancels are sent first and verified after, so a verification miss **destroys the
  protection and submits nothing**. ⭐ That ordering is the defect, ⛔ not the guard.
- ⭐ **G5b's gate** must not depend on a local row that nothing refreshes. ⭐ Gate the
  recovery on **broker** open orders for the symbol (the source CHECK9 already uses
  and got right), ⛔ or on a local row with a proven refresh path.

### 3.3 · RC-3 — classify broker errors
⭐ Terminal (validation, margin, auth) ⇒ **stop and escalate**. ⭐ Retryable
(timeout, rate-limit, transient) ⇒ bounded retry. ⛔ Never loop a validation error.

### 3.4 · 🔴 THE ORDERING QUESTION FOR RED-TEAM
🔬 The code comment says CHECK9 *"Runs before G5b so naked positions are caught
before recovery attempts."* ⇒ ⭐ force-exit is **deliberately preferred** over
re-protect.
⚠️ ⭐ The evidence argues the opposite: a **recovery SL cannot double-sell and cannot
be rejected for market protection**, and 🔬 G5b is the only remedy that actually
worked (15:07:16, 11.5 s). ⇒ ⭐ **Should re-protection be tried first, with
force-exit as the escalation?** ⛔ Not decided here.

---

## 4 · VERIFICATION — ⛔ NOT REASONING

📄 Two prior symptom-fixes of this same path were reasoned and both broke. ⇒ ⭐ The
fix ships only with:
1. a test asserting **both** defect sites construct an order **identical** to the
   proven `eod_squareoff` payload shape — 🔬 `t2_cnc_gtt_realtest.py` *claims* this
   parity in prose and it is **false**; ⇒ ⭐ **a docstring asserting parity is a
   claim requiring a test**;
2. a test that the no-LTP path **cannot** emit raw MARKET;
3. a test that an exit attempt aborts when the broker reports flat (§2);
4. 👤 an observed live fill of the repaired path, off a real naked-position drill —
   ⛔ **until then the path stays UNPROVEN**, exactly as it is today.

---

## 4A · 🔴 REVISION 2 (FILE 128 + ChatGPT red-team) — THE ORDERING IS IMPOSSIBLE

⭐ §3.2's *"verify the cancel before the position is left unprotected"* is
**logically impossible** — you cannot verify a cancellation that has not happened.
⭐ Accepted. ⭐ But the proposed remedy (**place the replacement first, then cancel**)
has its own hole: 🔬 ANANTRAJ was LONG **1** with **two** resting sells (SL @620.10,
TGT @643.65). ⇒ ⭐ Establishing a replacement first makes **three sells against a
qty-1 long** — reintroducing the double-sell exposure §2 calls non-negotiable.

⇒ 🔴 ⭐ **Both orderings have a hole because both REPLACE.** ⭐ Zero-protection window
vs multiple-resting-sell window. ⛔ Neither is acceptable as designed.

### 4A.1 · CANDIDATE **O3 — CONVERT THE INCUMBENT** (👤 FILE 128 §2.1)
⭐ Neither cancel-then-place nor place-then-cancel. ⭐ **Modify the resting SL into
the exit:**
1. read the **broker** position and remaining quantity;
2. cancel the **TGT only** (🔬 far OTM — 643.65 vs 628.05 LTP; low double-fill risk);
3. **`modify_order`** the resting SL into an immediately-executable exit;
4. verify at the broker; residual ⇒ re-enter the state machine.

⇒ ⭐ At every instant **≥1 exit-capable order rests at the broker, and never more
than the position quantity.** ⇒ ⭐ The naked window is **eliminated**, ⛔ not relocated.
⇒ ⭐ It also dissolves most of RC-2: ⭐ there is no cancel on the critical path, so
`_verify_cancelled`'s settle window stops being safety-critical.

### 4A.2 · 🔬 O3 VERIFICATION — DOC-CITED. ⭐ PLAUSIBLE, ⛔ NOT VERIFIED.
`https://kite.trade/docs/connect/v3/orders/`:
| question | 🔬 doc answer |
|---|---|
| (a) can `order_type` be modified for `variety="regular"`? | ⭐ **YES — it is in the modifiable list**, verbatim: *"order_type, quantity, price, trigger_price, disclosed_quantity, validity"*. ⚠️ ⛔ But the doc does **not** state whether **SL → LIMIT conversion** is permitted. |
| (b) SELL-SL trigger-vs-LTP validation rule | 🔴 ⛔ **NOT STATED** in the doc. |
| (c) modify rate limits | 🔴 ⛔ **NOT STATED** in the doc. |
| (d) does the local state layer handle a modified order? | 🔴 ⛔ **UNESTABLISHED** — 🔬 `order_monitor` produced **1 log line** on a full trading day (§4C). |

⇒ ⭐ **VERDICT: `order_type` being officially modifiable is real, citable support and
it is the key enabler.** ⛔ But 3 of 4 questions are unstated, so **O3 cannot be
adopted on doc evidence alone.** ⭐ It needs a live drill on a real position before
it becomes primary. 📄 Two prior symptom-fixes of this path were reasoned and both
broke — ⛔ do not make O3 the third.
⇒ ⭐ **If O3 proves out, it is primary and §4A's replacement orderings are dropped.
⭐ If it does not, the design must state EXPLICITLY which window it accepts** —
⛔ never leave that implicit.

## 4B · 🔴 THE DATABASE TELLS A FALSE STORY (👤 FILE 128 §4)
🔬 `orders WHERE order_type='MARKET'` = **0 rows of 1315** — ⭐ yet **8 MARKET exits
were attempted today.** ⭐ A rejected order returns no `order_id`, so it leaves
**no row**. ⇒ 🔴 ⭐ **The most critical event of the day is invisible in the database.**

⭐ What the DB alone would tell an auditor tomorrow about ANANTRAJ:
`exit_reason=MANUAL` · `closure_source=OWN_SL` · `exits_verified=1` ·
`exits_verify_detail=ok` · ⛔ no record of 8 failed emergency exits · ⛔ no record of
a naked window ⇒ ⭐ **"a clean, verified stop-loss exit."** ⭐ Every part false, and
⛔ nothing in the DB contradicts it. ⭐ Only the log knows.

⇒ ⭐ **DESIGN REQUIREMENT: persist attempted-and-rejected orders** — payload, broker
error, timestamp, attempt number — against the trade. ⭐ Without it the failure is
unmeasurable **and §4's verification plan has no evidence to draw on**: ⛔ you cannot
prove a repaired path fills if its failures leave no trace.

## 4C · ⚠️ THE DESIGN ROUTES **AROUND** A COMPONENT, ⛔ IT DOES NOT REPAIR IT
🔬 `order_monitor` produced **1 log line** on a full trading day; the recovery SL's
row went terminal only via the 15:17 *"Sweep: marked 1 stale orders as CANCELLED
(parent trade terminal)"*.
⇒ ⭐ §3.2 gates G5b on broker state — ⭐ sound locally, ⭐ but be explicit: 🔴 **it
routes around a component that appears not to be doing its job.**
⇒ ⚠️ ⭐ If `order_monitor` is what keeps the whole `orders` table fresh, then after
this fix **every other consumer of local order state is still stale** — ⭐ D2, D4 and
the G5b suppression become three visible symptoms of one dead component, ⭐ and there
may be more nobody has tripped over. ⭐ 📄 **Fifth "built yet inert" instance.**
⇒ ⏸ **RECORDED, ⛔ NOT FIXED HERE:** what is `order_monitor` responsible for, is it
running, why one log line, and who else depends on it.

## 4D · 🔴 THERE IS A WORKING BACKSTOP, AND THE DESIGN MUST NOT BREAK IT
🔬 Measured over all history:
- `closure_source='OWN_EOD'` = **27 closures, ALL product `MIS`** (13:24 → 15:17:30);
- their `leg='EOD'` orders are **27 of 27 `LIMIT` and `COMPLETE`.**
⇒ 🔴 ⭐ **The system's 15:17 `eod_squareoff` is the de-facto MIS backstop and it
works — 27 for 27, using LIMIT.** ⭐ That is *why* it works, and it is the proven
payload shape RC-1 should copy.
⇒ ⭐ **The real exposure window is 15:07 → 15:17**, where the MIS squareoff (🔬 never
run) and check9 (🔬 rejected 8×) both fail and only `eod_squareoff` is left.
⚠️ 🔬 In that window, **4 MIS positions have historically closed via an
`EXTERNAL_UNATTRIBUTED` actor** — COALINDIA 15:09:01, TATAPOWER 15:12:47,
HDFCSILVER 15:15:01, MOSCHIP 15:16:35. ⛔ **Whether that was Zerodha's own cutoff or
a manual close is NOT DETERMINABLE** — the label means the system could not
attribute it, both are external and untagged, and the broker books for those days
are gone (daily). ⭐ Stated flat; ⛔ it is not an argument for any option.

## 4E · FOLDED IN FROM THE RED-TEAM (verbatim intent)
- ⭐ **A marketable LIMIT is not a guaranteed exit** ⇒ ⭐ carry **partial-fill and
  residual handling** explicitly. ⛔ A partial fill must not be recorded as an exit.
- ⭐ Add a **STATE-UNKNOWN** state ⇒ ⛔ **no blind retry after an ambiguous
  submission.** 🔬 Today's 8 blind retries are the anti-pattern.
- ⭐ ⛔ Do not *merely* move G5b's gate to broker open orders — ⭐ the gate needs a
  defined state contract, ⛔ not a different stale source.
- ⭐ **Safety property, and the acceptance test for the whole design:**
  ⛔ no blind cancel · ⛔ no blind exit · ⛔ no blind retry · ⛔ no safety decision from
  local state alone.
- ⭐ *"Change MARKET to LIMIT"* is ⛔ **not** the insight. ⭐ The insight is:
  **the safest state transition is the one that never transitions.**

## 4F · 🔴 REVISION 3 — TWO BLOCKERS FOUND AFTER THE DESIGN WAS WRITTEN

### 4F.1 · `mis_autosquareoff` PERSISTS NOTHING, AND C6 ARMS A SECOND DOUBLE-SELL
🔬 It **does** submit — `self._adapter.place_order(...)` at **`:541`**, tag
`mis_autosq_{pass}`. 🔬 Its **only** store call is a **read** (`:500`). ⛔ No
`insert_order`, ⛔ no order-manager, ⛔ no write.
⇒ ⭐ `PASS_2_EXIT_PROTOCOL="MARKET"` (`:71`, used `:546`) is **LIVE config**, ⛔ not
dead; ⚠️ PASS_1 **hardcodes** `"MARKET"` — 📄 a Zero-Hardcoding violation, and it
means PASS_1 cannot be reconfigured at all, only rewritten. ⭐ Both passes must
route through the one shared constructor.
⇒ 🔴 **A submitted exit rests at the broker INVISIBLE to the local store.**
CHECK9/G5b decide from local state ⇒ they see *"no active SL"* ⇒ **a second sell.**
⛔ **C5 does NOT cover this** — C5 re-reads the **position**, which is still open
while an unfilled exit rests.
⇒ 🔴 **HARD DEPENDENCY: C6 ships only alongside persistence.** ⭐ The path is dormant
today only because 🔬 `EXIT_SUBMITTED` = 0; **the fix is what triggers it.**

⭐ **And the tag must land in the same commit.** 🔬 `mis_autosq_pass_1` is a **static
string with no trade identity**. For `rc_recovery_sl` that was survivable because
G5b **persists**, so the local row carried the `trade_id`. ⛔ Here there is no row —
⭐ the tag is the **only** link back to the trade. ⇒ A fill would be unmappable, and
would surface as **`EXTERNAL_UNATTRIBUTED`** — 🔬 the exact label already on 4
historical closures in this window.
⚠️ 🔬 Mind the 20-char broker limit (📄 01-Jul BANSALWIRE was rejected for tag
length): `trd_`+12hex = 16 fits; ⛔ `mis_autosq_pass_1`+trade id does not.

### 4F.2 · 🔴 THE SANDBOX AS DESIGNED CANNOT PROVE C6 OR O3
🔬 The only on-disk sandbox description is `PATHS.md:119` — *"a SANDBOX hard_kill
drill (**mock broker + scratch DB**)"*; 📄 `SYSTEM_MAP.md:1141` adds *"sandbox
charter, no production role, its own deny list"*. ⇒ ⭐ That **contradicts** the
standing note that the sandbox holds real Zerodha order rights. ⏸ 👤 Rama resolves.

🔴 **Proven in code, ⛔ not inferred:** `zerodha_adapter.place_order` branches at
**`:573`** `if self._paper: result = self._paper_place_order(...)` and **never
reaches** `self._kite.place_order(...)` at **`:604`** — ⭐ which is the only place
Zerodha's validation runs. ⇒ ⭐ **A mock broker accepts the MARKET order and
synth-fills it. The `InputException` cannot occur.**
⇒ 🔴 ⭐ **A simulator would have passed the very order production rejected** — 📄 this
project's recorded failure mode, and how 01-Jul and 03-Sep both shipped.

⭐ **Split the evidence requirement:**
| commits | simulator sufficient? | why |
|---|---|---|
| C3 · C4 · C5 · C7 | ✅ **yes** | state-machine logic; a simulator exercises it honestly |
| **C6 · O3** | 🔴 **NO** | these ask *"what does this broker accept?"* — ⭐ only the broker answers. ⛔ A simulated fill is **not** the observed live fill the MIS gate requires |

⚠️ 🔬 **And there is no sandbox to run in:** the worktree is **gone** — `git worktree
list` shows `sandbox-work` at **`39292d3`**, marked `prunable`, and the path does
not exist. ⇒ ⭐ It must be re-provisioned, and at the **current** tree — ⛔ a sandbox
pinned to `39292d3` would test code that is not running (📄 `controls.py` /
`control_client.py` do not exist there).

## 5 · COMMIT PLAN — one fix per commit, in this order
**0. 👤 TREE advance `39292d3 → 2d08436` — PREREQUISITE, ⛔ not a GUI leftover.**
   🔬 The rollback point is not the running code; reverting today would unship 29
   backend modules with unknown effect on the exit path. ⭐ 👤 Rama's act.
1. **§4B rejected-order persistence** — ⭐ moved to FIRST. ⛔ Without it none of
   commits 2–6 can be *shown* to work, and 🔬 today's 8 failures left no row.
   🔴 ⭐ **C3 now also carries `mis_autosquareoff` persistence AND a
   trade-identifying tag (§4F.1) — one commit, ⛔ not three.** ⭐ Persisting an order
   whose tag cannot name its trade solves nothing, and ⛔ C6 is blocked on both.
2. RC-2 state contract + settle window with bounded re-reads (⛔ changes no payload)
3. RC-2 G5b gate on a defined broker-state contract (⭐ restores the remedy that
   worked) · ⭐ + the **STATE-UNKNOWN** state and the no-blind-retry rule
4. RC-3 error classification (⭐ stops the doomed retry storm)
5. **§2 pre-submit broker position re-read** (🔴 ⭐ MUST precede 6)
6. RC-1 exit payload — ⭐ **O3 if 4A.2 proves out**, else a shared marketable-LIMIT
   constructor for both sites, ⭐ with partial-fill/residual handling
7. D3 alert text · D6 hygiene · `order_monitor` investigation (§4C)
   (⭐ ⛔ none on the critical path)

⚠️ 🔴 **⛔ Do not ship 6 before 5.** ⭐ RC-1 alone re-arms a never-run path with no
protection against the human race condition — 🔬 the 2-second gap.
⭐ **And 1 before everything:** ⛔ a fix whose failures are invisible cannot be
verified, which is how this path was "fixed" twice before and broke twice.

---

## 6 · OPEN QUESTIONS FOR RED-TEAM
1. §3.4 — re-protect first, or force-exit first?
2. §3.1 — abort-and-escalate on no LTP, or a verified `market_protection`?
3. What deadline for the settle window? 🔬 The only measurement is **1.115 s**;
   ⭐ n=1. ⛔ Do not pick a number from it.
4. Should the MIS squareoff exit run at all before it has ever executed once?
   🔬 It has **never** run (0 of 27 EOD orders in its window).
5. 🔬 The TREE (`39292d3`) is **not** the running code (`2d08436`). ⇒ ⭐ A rollback
   today would unship 29 backend modules with unknown effect on the exit path.
   ⇒ ⭐ Advance the TREE **before** touching this path. 👤 Rama's act.
