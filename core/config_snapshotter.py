"""
core/config_snapshotter.py — Trading System v2

W0 (daily-report redesign foundation): persist the FULL resolved runtime config
to the ``config_snapshots`` table at startup, so the daily report's Config sheet —
and any historically-correct report re-run — can read the config AS IT WAS on a
given date, DB-purely.

    SYSTEM writes the DB (this module, a system task, reads the already-resolved
    AppConfig). The REPORT later reads ONLY config_snapshots — never YAML. That is
    the whole point of W0: it moves "what config was live on date X" out of the
    in-memory-only YAML world and into the durable, queryable DB.

Design (boring, deterministic, resume-safe)
-------------------------------------------
* Serialize the effective config (``AppConfig.model_dump(mode="json")`` — the exact
  dict ``main.py`` runs on) to a STABLE JSON string (sorted keys) → ``config_json``.
  sha256(config_json) → ``config_hash``.
* IDEMPOTENT per ``(snapshot_date, config_hash)``:
    - a row with the same (date, hash) already exists → SKIP (no duplicate);
    - today has rows but the hash DIFFERS (config changed on a same-day restart)
      → INSERT a new row (the timestamps distinguish them, capturing the change).
* No secrets are logged (only the row id, the hash prefix, and the wrote/skipped
  decision). By CL5 the resolved config holds NO secret VALUES — only env-var
  NAMES (e.g. ``bot_token_env``) — so ``config_json`` itself is secret-free in
  production (the one plaintext-fallback field ``alerts.smtp.password`` is empty
  there; production resolves the password from ``password_env`` at send time).

This module owns POLICY (serialize / hash / idempotency). It uses the public
``StateStore`` API (``fetch_one`` / ``transaction``) for I/O — no new DAO on
StateStore, keeping W0's blast radius to schema + this module + one wiring call.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Serialization + hashing (pure, independently testable)
# ─────────────────────────────────────────────────────────────────────────────

# M-K5: keys whose VALUE is a secret if it is ever populated. Matched on the key name,
# case-insensitively, as a substring. `*_env` keys are deliberately NOT redacted — those
# hold env-var NAMES (bot_token_env, password_env), which are the name-indirection this
# system relies on and are meant to be visible in the report's Config sheet.
_SECRET_KEY_MARKERS = ("password", "secret", "token", "api_key", "apikey")
_REDACTED = "***REDACTED***"


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    if k.endswith("_env"):
        return False  # an env-var NAME, not a value
    return any(m in k for m in _SECRET_KEY_MARKERS)


def redact_secrets(config):
    """M-K5: replace any POPULATED secret-valued field with a redaction marker.

    Only NON-EMPTY string values are touched. That is the whole design:

    * In production nothing changes — by CL5 the resolved config carries env-var NAMES,
      not values, and the one plaintext-fallback field (``alerts.smtp.password``) is
      empty. Empty stays empty, so ``config_json`` is byte-identical, so ``config_hash``
      is identical, so the dedupe still skips and no extra snapshot row appears. This
      fix is provably a no-op on the live path.
    * If the sanctioned DEV SMTP-password fallback is ever used, the plaintext no longer
      lands in ``config_snapshots.config_json`` — a durable table that rides into every
      DB backup. Redacting at the point of persistence is the only place that helps: by
      then the value has already been resolved, and a snapshot is forever.
    """
    if isinstance(config, dict):
        return {
            k: (_REDACTED if _is_secret_key(k) and isinstance(v, str) and v
                else redact_secrets(v))
            for k, v in config.items()
        }
    if isinstance(config, list):
        return [redact_secrets(v) for v in config]
    return config


def config_to_canonical_json(config: dict) -> str:
    """
    Serialize ``config`` to a STABLE JSON string: sorted keys + compact separators.

    Stable = key-order independent, so two dicts that differ only in key insertion
    order produce byte-identical JSON (and therefore the same hash). This is the
    string stored in ``config_json`` AND the string that is hashed — the report
    reads it back with a plain ``json.loads``.

    M-K5: secrets are redacted here, so the redaction applies to BOTH the stored
    string and the hash (one path — they cannot diverge).
    """
    return json.dumps(redact_secrets(config), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def hash_config_json(config_json: str) -> str:
    """sha256 hex digest of the canonical config JSON (utf-8)."""
    return hashlib.sha256(config_json.encode("utf-8")).hexdigest()


def _resolve_config_dict(app_config) -> dict:
    """
    Return the plain dict for ``app_config``. Accepts a Pydantic AppConfig (the
    real startup object) via ``model_dump(mode="json")`` — which renders dates and
    every nested sub-config to JSON-native types — or a plain dict (tests). The
    FULL config is captured; W0 never pre-filters (the Config sheet decides later
    what to show).

    NI-6 (22-Aug-2026) — THE FIRST POST-DEPLOY DAY WILL RECORD A NEW ``config_hash``,
    AND THAT IS CORRECT. ⛔ Do not "fix" it. ⛔ Do not read it as drift. Two
    independent reasons, both MEASURED rather than reasoned:

      1. fix item 1 (``d00e574``) made five delivery-scoped keys explicit, so the
         dumped config gains five fields. A real config change, correctly recorded.

      2. THE ONE THAT WILL CATCH SOMEONE OUT: ``AppConfig`` carries ``file_hashes``
         — the sha256 of each config file's RAW BYTES (``config_loader`` CL4) — and
         that dict is INSIDE what gets hashed here. So a COMMENT-ONLY edit to
         ``system_config.yaml`` moves ``config_hash`` even though every VALUE is
         unchanged. Measured on this tree: NI-3 edited only comments; the canonical
         JSON stayed 16,206 bytes and every value was identical, while
         ``config_hash`` went ``4bed2598…`` → ``1b163ca4…``.

    ⇒ A ``config_hash`` change is evidence that a config FILE changed. It is ⛔ NOT
      evidence that a config VALUE changed. To answer "did a value move?", diff
      ``config_json`` — ⛔ never the hash.
    """
    if hasattr(app_config, "model_dump"):
        return app_config.model_dump(mode="json")
    if isinstance(app_config, dict):
        return app_config
    raise TypeError(
        "snapshot_config: app_config must be a pydantic AppConfig "
        f"(with .model_dump) or a dict, got {type(app_config).__name__}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The one entry point (called right after config is resolved at startup)
# ─────────────────────────────────────────────────────────────────────────────

def snapshot_config(
    store,
    app_config,
    *,
    snapshot_date: str,
    snapshot_ts: str,
    account_id: str,
    mode: str,
    trade_type: str,
    logger: Optional[logging.Logger] = None,
) -> Optional[int]:
    """
    Write ONE ``config_snapshots`` row for ``snapshot_date`` unless an identical
    one (same ``config_hash``) already exists for that date.

    Args:
        store:         a StateStore (uses ``fetch_one`` + ``transaction``).
        app_config:    the resolved AppConfig (or a plain dict, for tests).
        snapshot_date: YYYY-MM-DD (IST) — the run/trading date.
        snapshot_ts:   ISO-8601 IST timestamp of when the row is written.
        account_id:    primary/selected account (e.g. "LFL836").
        mode:          "PAPER" | "LIVE".
        trade_type:    "INTRADAY" | "DELIVERY" | "BOTH".
        logger:        optional; defaults to the module logger.

    Returns:
        The new ``snapshot_id`` if a row was written, or ``None`` if an identical
        snapshot already existed for the day (idempotent skip).
    """
    log = logger or logging.getLogger("config_snapshotter")

    config_dict = _resolve_config_dict(app_config)
    config_json = config_to_canonical_json(config_dict)
    config_hash = hash_config_json(config_json)

    log.info(
        "config_snapshotter: resolved config for %s (mode=%s account=%s "
        "trade_type=%s) hash=%s len=%dB",
        snapshot_date, mode, account_id, trade_type, config_hash[:12], len(config_json),
    )

    existing = store.fetch_one(
        "SELECT snapshot_id FROM config_snapshots "
        "WHERE snapshot_date = ? AND config_hash = ? LIMIT 1",
        (snapshot_date, config_hash),
    )
    if existing is not None:
        log.info(
            "config_snapshotter: identical snapshot already present for %s "
            "(id=%s, hash=%s); skipped",
            snapshot_date, existing["snapshot_id"], config_hash[:12],
        )
        return None

    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO config_snapshots "
            "(snapshot_date, snapshot_ts, account_id, mode, trade_type, "
            " config_hash, config_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (snapshot_date, snapshot_ts, account_id, mode, trade_type,
             config_hash, config_json),
        )
        new_id = cur.lastrowid

    log.info(
        "config_snapshotter: wrote config snapshot id=%s for %s (hash=%s)",
        new_id, snapshot_date, config_hash[:12],
    )
    return new_id
