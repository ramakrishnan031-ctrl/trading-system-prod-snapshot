# Lessons

Durable engineering lessons distilled from incidents/post-mortems. One bullet per
lesson; link the commit/branch and the memory note that has the full story.

- **2026-06-24 — A safety-net daemon that isn't health-checked can die silently.**
  `tgt_retry_manager` (30s TGT re-place daemon) crash-looped every cycle for two
  live days (Mon 22-Jun 840 / Tue 23-Jun 230 errors) on a 1-arg vs 3-arg
  `is_within_market_hours` call — born broken on its first commit, never worked —
  yet surfaced ZERO alerts because `_loop` caught and logged the exception each
  cycle with no escalation. Two corrective patterns:
  1. **Signature-lock cross-module calls.** A helper called across modules with
     positional args should have an `inspect.signature` lock test on the caller's
     side, so a signature change breaks a test instead of a silent production loop.
     (The one test that exercised the guard *mocked* the function, hiding the bug.)
  2. **Every safety daemon must be health-checked.** A `try/except` that "keeps the
     loop alive" must also DETECT a sustained failure and alert (throttled), and
     expose liveness on `/health` so a dead/never-started worker shows up in
     pre-flight, not a week later in a post-mortem. "The loop must never die" is not
     the same as "the loop is doing its job."

  Branch `tgt-retry-postmortem-24jun`; memory `tgt_retry_crashloop_postmortem_24jun`.

- **2026-06-24 — Don't date-couple tests; derive or inject the date.** A test
  hardcoded `day="2026-06-19"` and wrote a log file *now* — but the function under
  test (`report_integrity_check`) compares the file's **mtime-day** to `day` (an
  intentional anti-staleness guard), so on any day ≠ 19-Jun the mtime didn't match
  and the assertion flipped. The test passed **only on the calendar day it was
  written**. Fix: derive the date in the test (`date.today()`) so the file's mtime
  matches; never hardcode "today" as a literal. (The function was correct — verify
  which side is actually wrong before "fixing".) Memory `hygiene_pack_24jun`.

- **2026-06-25 — Don't guard a race by reading state written asynchronously after
  the event; check an authoritative/current source, and back it with a continuous
  invariant.** G5b crash-recovery checked the LOCAL `orders` table for an existing SL
  before placing a recovery one — but that table is written by `order_monitor.track`
  ~40ms AFTER the broker placement, so the guard read 0 (12ms before the row landed)
  and placed a DUPLICATE SL outside the OCO. On the stop-hit both filled → a -1 naked
  over-sell (RAMCOIND 25-Jun; also IRFC/NIACL). Two patterns:
  1. **A TOCTOU guard must consult a source true AT the event** — the broker order
     book (reflects the SL the instant it's placed) / an in-memory registry populated
     at placement, never a table written asynchronously after. A "settling window"
     (don't crash-recover a fill seconds old — its exits are still being placed)
     closes the race deterministically and mode-agnostically.
  2. **Back prevention with a continuous invariant.** The keystone wasn't the guard —
     it was a reconciler check enforcing "exactly one live SL/TGT per open trade" every
     cycle, cancelling extras. It needn't win the race; it self-heals any duplicate
     ~12 min before a typical stop-hit. And a system-created naked position must never
     be silently disowned as "human".
  Branch `ramcoind-duplicate-exit-fix-25jun`; memory `ramcoind_duplicate_sl_incident_25jun`.
