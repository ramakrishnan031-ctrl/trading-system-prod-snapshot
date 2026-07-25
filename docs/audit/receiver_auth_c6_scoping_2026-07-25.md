# C6 — receiver auth: what shape is it in today?

> **TWO routes, not many** — `GET /health` and `POST /webhook/<scanner_name>`. Nothing else is registered.
> **They diverge in four places**, only one of which is the duplication C6 targets: the auth *state* is already shared (`_secret`, `_require_hmac`, `_ip_limiter` are instance attributes read by both), so what is duplicated is the **check**, written twice in two different shapes — not the configuration behind it.
> **The PB-01 route is SAFE for Monday.** It is not a separate route: `/webhook/pb01_breakout_retest` **is** `/webhook/<scanner_name>`, so its auth is byte-identical to the trading routes by construction, and auth runs *before* the EOD branch. **Its Monday risk is elsewhere — see §M, which is a boot-wiring problem, not an auth problem.**

**READ-ONLY scoping pass.** 2026-07-25 (IST). Deployed HEAD `570b3e8`. Code read only; **no test POST was sent**; no config, no restart, no design. Every claim below is MEASURED from quoted source unless marked ASSUMED.

---

## §B1 — Every route, and exactly how each authenticates

`grep -nE "@app\.route|add_url_rule" signals/webhook_receiver.py` → **two registrations. That is the complete set.**

### Route 1 — `GET /health` (`webhook_receiver.py:271`)

Auth is written **inline in the route closure**:

```python
271:        @app.route("/health", methods=["GET"])
272:        def health():
283:            source_ip: str = request.remote_addr or "unknown"
285:            # Limit BEFORE auth, exactly as /webhook does, so a flood is cheap to reject.
286:            if receiver._ip_limiter is not None and not receiver._ip_limiter.allow(source_ip):
287:                return jsonify({"error": "rate limit exceeded"}), 429
289:            # No secret configured -> no auth surface to enforce; preserve old behaviour
290:            # rather than hard-fail a deployment that never had a secret.
291:            if receiver._secret:
292:                sig_header: str = request.headers.get("X-Webhook-Signature", "")
293:                token_param: str = request.args.get("token", "")
294:                ok = False
295:                if sig_header.startswith("sha256="):
296:                    expected_hex = _hmac.new(
297:                        receiver._secret.encode(), b"", hashlib.sha256
298:                    ).hexdigest()
299:                    ok = _hmac.compare_digest(sig_header[7:], expected_hex)
300:                elif receiver._require_hmac:
311:                    ok = False
312:                elif token_param:
313:                    ok = _hmac.compare_digest(token_param, receiver._secret)
314:                if not ok:
317:                    return jsonify({"error": "authentication required"}), 401
```

Then, and only then, it returns `kill_switch_active`, `queue_size`, `queue_capacity` (`:319-328`).

### Route 2 — `POST /webhook/<scanner_name>` (`webhook_receiver.py:330`)

The route is a two-line delegate; auth lives two frames down in `_process_request`:

```python
330:        @app.route("/webhook/<scanner_name>", methods=["POST"])
331:        def webhook(scanner_name: str):
332:            return receiver._handle_webhook(scanner_name)
```

```python
472:        if self._secret:
473:            sig_header: str = request.headers.get("X-Webhook-Signature", "")
474:            token_param: str = request.args.get("token", "")
475:            if sig_header.startswith("sha256="):
476:                provided_hex = sig_header[7:]
477:                expected_hex = _hmac.new(
478:                    self._secret.encode(), raw_body, hashlib.sha256
479:                ).hexdigest()
480:                if not _hmac.compare_digest(provided_hex, expected_hex):
481:                    return jsonify({"error": "HMAC signature mismatch"}), 401
482:            elif self._require_hmac:
486:                return jsonify({
487:                    "error": "HMAC signature required (require_hmac=True); "
488:                             "token param is not accepted"
489:                }), 401
490:            elif token_param:
491:                if not _hmac.compare_digest(token_param, self._secret):
492:                    return jsonify({"error": "Invalid token"}), 401
493:            else:
494:                return jsonify({"error": "Missing auth: provide X-Webhook-Signature header or ?token= param"}), 401
```

There is **no third route, no unauthenticated sibling, and no `add_url_rule` elsewhere in the module.** (Enumerated, not asserted — Standing Rule 1.) The `@app.errorhandler(500)` at `:334` is not a route.

---

## §B2 — Where each auth check sits relative to the work

**The 403-investigation gate order HOLDS for `/webhook`, confirmed line by line:**

| # | gate | line | outcome |
|---|---|---|---|
| 1 | shutting-down | `:409` | 503 |
| 2 | per-IP rate limit | `:419` | 429 |
| 3 | **AUTH** | `:472-494` | 401 |
| 4 | unknown scanner | `:497-499` | 404 |
| 5 | **EOD route → returns** | `:507-509` | 200/400 |
| 6 | kill switch | `:512` | 403 |
| 7 | backpressure | `:520` | 503 |
| 8 | entry window | `:527` | 403 |
| 9 | parse | `:531+` | 400 |

**⚠️ Work that happens BEFORE auth on `/webhook`** (flagged as B2 asks — none of it is a defect, but a refactor must know it exists):

- **`raw_body: bytes = request.get_data()` (`:404`)** — the full body is read into memory before any auth. Bounded by `MAX_CONTENT_LENGTH = 1 MB` (`:220`, FIX-077). Structurally unavoidable: the HMAC is computed *over* the body, so the body must be read to authenticate at all.
- **Two `_write_audit(...)` DB writes on the pre-auth exits** — `:411` (503, shutting down) and `:421` (429, rate-limited). So **an unauthenticated caller can cause `webhook_audit` rows.** Bounded by the per-IP token bucket. MEASURED; consequence-rated low, recorded because a "single global auth" that moved auth earlier would change this behaviour.

**`/health`** does strictly less before auth: the IP limiter (`:286`) and nothing else — no body read, no DB write. The limiter-before-auth ordering is **deliberate and identical on both routes** ("so a flood is cheap to reject", `:285` / `:418`).

**No route does its *actual work* before authenticating.** `/health` reads kill/queue state only after `:317`; `/webhook` reaches the queue only after gate 9.

---

## §B3 — What EXACTLY differs between the two routes

**Four divergences. Only #3 is the duplication C6 exists to remove.**

1. **HMAC payload differs — necessarily.** `/health` signs `b""` (`:297`); `/webhook` signs `raw_body` (`:478`). Forced by the methods (GET carries no body). ⚠️ **Consequence worth naming: a valid `/health` signature is a CONSTANT for a given secret** — `HMAC(secret, "")` never changes, so it is indefinitely replayable. `/webhook`'s signature is body-bound and therefore is not. Not a defect (the secret is the thing being proven either way), but the two routes do not offer equivalent guarantees, and a single shared helper must not paper over that.

2. **Failure-message granularity differs — DELIBERATE.** `/webhook` returns four distinct messages (mismatch / required / invalid token / missing auth). `/health` returns one uniform `"authentication required"`. The reason is documented in situ (`:308-310`): naming the reason on `/health` would tell an anonymous caller whether `require_hmac` is on. **A refactor that unifies the messages would re-open that leak.**

3. **⭐ Control-flow shape differs — THIS is the duplication.** `/health` accumulates an `ok` boolean and issues one 401 at `:314-317`; `/webhook` returns early inside each branch. Same three-way decision (HMAC → require_hmac → token), written twice, in two shapes, in two places. The `elif receiver._require_hmac: ok = False` at `:300-311` exists solely to reproduce, in boolean form, what `:482-489` does with an early return. **That is the maintenance hazard: a future change to the auth policy must be made twice, in two idioms, and the two were already patched separately (G.1 on `/webhook` 2026-04-25; the `/health` parity fix later, per its own comment).**

4. **Audit trail differs.** `/webhook` writes a `webhook_audit` row for **every** request including 401s, via the `finally` at `:453-458`. `/health` writes nothing. **So a brute-force against `/health` leaves no row in the authoritative POST record** — it is visible only in logs. MEASURED.

---

## §B4 — The secret: one value, all routes

**One secret. No per-route tokens.**

- `self._secret = secret_token` (`:159`) — a single instance attribute; both routes read it (`:291`, `:472`).
- Production source: `secret_token=os.environ.get("WEBHOOK_SECRET")` (`main.py:2897`).
- **Required in BOTH paper and live**, fail-fast at startup: `required_startup_secrets()` returns `WEBHOOK_SECRET` (`main.py:226`), consumed by `run_all_startup_checks` (C-2, 02-Jul — paper was previously exempt, which left the paper webhook open).
- ⭐ **Therefore the `if self._secret:` / `if receiver._secret:` guards are UNREACHABLE-FALSE in production** — the boot fails before the receiver is constructed if the env var is unset. They exist for tests and for the historical "deployment that never had a secret" case (`:289-290`). MEASURED from the startup-check list; the branch itself is real code, so it stays a test-only path, not dead code.
- **BL-18 construction guard** (`:146-151`): `require_hmac=True` with an empty secret raises `ValueError` at construction — no silent downgrade.
- **Is it the value Chartink sends?** Yes — as the **query-param bearer** `?token=<secret>`, compared with `compare_digest` (`:313`, `:491`). Accepted **only while `require_hmac=False`** (currently false). This is why READ-FIRST (A) stands: Chartink cannot sign HMAC, so flipping `require_hmac` 401s every POST on both routes.

---

## §B5 — ⭐ The PB-01 capture route, specifically (it goes live Monday)

**It is not a separate route.** `config/scan_webhook_map.yaml:78` states the contract: *"Routing key MUST equal the `/webhook/<scanner_name>` URL path"*, and the entry is `pb01_breakout_retest` (`:81-84`). So the Monday POST is to **`/webhook/pb01_breakout_retest`**, handled by the single `POST /webhook/<scanner_name>` route.

**Consequences, all MEASURED:**

- Its auth is **byte-identical to the trading routes** — literally the same lines (`:472-494`), because it is the same code path. There is no PB-01-specific auth to be weaker.
- **Auth runs BEFORE the EOD branch.** Auth `:472-494` → unknown-scanner `:497` → EOD dispatch `:507-509`. `_handle_eod`'s own docstring records the invariant: *"Auth already ran in `_process_request` before this"* (`:696`).
- Unknown-scanner 404 also sits **after** auth, so an unauthenticated caller cannot enumerate scanner names.

**VERDICT: the PB-01 route's auth is present, correct, and not weaker than the trading routes. It is safe for Monday, and it is not a C6 blocker.**

---

## §M — ⚠️ THE ACTUAL MONDAY RISK (found in passing; NOT an auth problem, NOT C6)

Stated separately because it is a **boot-wiring** issue and would otherwise be lost inside an auth report.

The EOD capture is **late-bound** — `webhook_receiver.set_eod_capture(pb01_capture_worker)` (`main.py:3203`), not passed at construction (`main.py:2890-2898`). That whole block is wrapped:

```python
3208:        except Exception as exc:   # never let the watchlist break startup
3209:            _log.error("pb01 watchlist wiring failed (continuing without it): %s", exc)
3210:            pb01_capture_worker = None
```

If **anything** in that wiring raises at the 08:15 boot (`OhlcFetcher`, `WatchlistCaptureWorker`, `Pb01EntryStage`, an import), the receiver keeps `_eod_capture = None` and the 17:00 alert takes this path:

```python
718:        if self._eod_capture is None:
719:            self._log.warning(
720:                "EOD alert %s: watchlist disabled (no capture worker) → fail-safe miss "
721:                "for %d symbol(s)", scanner_name, len(symbols))
722:            return jsonify({"accepted": 0, "captured": 0, "detail": "watchlist disabled"}), 200
```

**A 200 back to Chartink, zero rows, one WARNING line.** Combined with the already-known capture observability gap (no Telegram, no heartbeat, no sentinel — `eod_capture_safety_2026-07-24.md` §5.2), an empty Tuesday `pb01_watchlist` would look **identical** to "no breakouts" and to "the service died at 16:20".

**Free mitigation, no code:** grep the Monday 08:15 boot log for
`"V3 Step 10b PB-01 watchlist: ENABLED"` (`main.py:3206`).
Its **presence** means the capture worker is wired; its **absence**, or the `"pb01 watchlist wiring failed"` ERROR (`:3209`), is the tell — hours before 17:00, with time to act. `watchlist.enabled: true` is set (`system_config.yaml:442`), so the block is entered.

> ⚠️ **Citation note:** the 24-Jul reports cite this key at `system_config.yaml:433-445`. That was correct then. **The 25-Jul service-window deploy inserted 8 lines into `trading_hours` above it**, so the block is now at `:441-452` and `enabled` at `:442`. Re-verified against the deployed file tonight; anyone re-reading the 24-Jul reports should apply the same +8 shift below `trading_hours`. Whether it *completes* is what the log line proves. (ASSUMED: that no exception occurs — it cannot be established by reading, only observed.)

---

## §B6 — What a single-global-auth refactor must PRESERVE (described, not designed)

Seven intentional behaviours a naive flattening would destroy:

1. **The EOD route is reachable off-hours BY DESIGN.** It `return`s at `:509` *before* the kill gate (`:512`) and the entry-window gate (`:527`), with the rationale in situ (`:501-506`: *"a post-close alert is legitimate"*). This is exactly what makes the 17:00 capture possible. **Any reordering that moves auth without preserving this early return breaks Monday.**
2. **`/health`'s uniform 401 message** — deliberate anti-oracle (`:308-317`). Do not unify messages across routes.
3. **`/health`'s empty-body HMAC** — structural to GET; a shared helper cannot assume a body.
4. **Unknown-scanner 404 AFTER auth** (`:497`) — prevents unauthenticated scanner enumeration.
5. **Rate limit BEFORE auth on both routes** — deliberate on both (`:285`, `:418`).
6. **`/webhook`'s audit row on every outcome including 401** (`:453-458`) — the authoritative POST record.
7. **The `if _secret:` bypass branch** — unreachable in prod (§B4) but relied on by tests; removing it changes the test contract, not production behaviour.

---

## §B7 — Auth state that is ALREADY shared (this materially shrinks C6)

**The state is already global to the receiver instance; only the check is duplicated.**

- `self._secret` (`:159`), `self._require_hmac` (`:169`), `self._ip_limiter` (`:214`) — all instance attributes.
- `/health` reaches them through the `receiver = self` closure captured at `:269`; `/webhook` reaches them via `self`. **Same objects, one source of truth, no per-route configuration.**
- Also already shared: `MAX_CONTENT_LENGTH` (`:220`, app-level), the 500 handler (`:334`), and secret redaction in logs (`:776-777`).

⇒ **C6 is not "introduce shared auth state" — that exists. It is "collapse two spellings of one decision into a single call site, without flattening the seven differences in §B6."** MEASURED.

---

## Evidence appendix (reproduce; all read-only)

`signals/webhook_receiver.py:146-151` (BL-18 guard), `:159,169,214,220` (shared state), `:267-337` (both route registrations), `:271-328` (`/health` incl. auth), `:401-458` (`_handle_webhook`: pre-auth work + audit `finally`), `:460-529` (gate order), `:472-494` (`/webhook` auth), `:507-509` (EOD dispatch), `:691-735` (`_handle_eod`, incl. the `_eod_capture is None` 200), `:1057-1061` (`set_eod_capture`), `:776-777` (redaction) · `main.py:226` (required secret), `:2890-2898` (construction), `:3169-3211` (PB-01 wiring + swallowing except), `:3203,3206,3209` · `config/scan_webhook_map.yaml:78,81-84` · `config/system_config.yaml:442` (was `:434` pre-25-Jul; +8 shift).

No POST sent · no config touched · no service touched · nothing designed.
