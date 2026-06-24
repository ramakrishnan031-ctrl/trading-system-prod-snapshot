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
