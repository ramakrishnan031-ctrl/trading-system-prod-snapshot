"""
tests/unit/test_ni6_config_hash_change_is_expected.py

NI-6 (22-Aug-2026) — NOTHING WAS FIXED. This pins the EXPLANATION so it cannot
quietly stop being true, because the explanation is what stops someone "fixing" a
correct hash change months from now.

THE CLAIM, recorded at core/config_snapshotter._resolve_config_dict:
a config_hash change means a config FILE changed. It does NOT mean a config VALUE
changed — AppConfig.file_hashes carries the sha256 of each file's RAW BYTES and
that dict is inside the hashed payload, so a COMMENT-ONLY edit moves the hash while
every value and the canonical JSON's length stay identical.

That is counter-intuitive enough to be worth a test rather than a sentence.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from core.config_loader import load_all                      # noqa: E402
from core.config_snapshotter import (                        # noqa: E402
    config_to_canonical_json,
    hash_config_json,
    _resolve_config_dict,
)


def _snapshot(config_dir: Path) -> tuple[str, str, dict]:
    app = load_all(config_dir)
    payload = _resolve_config_dict(app)
    canonical = config_to_canonical_json(payload)
    return hash_config_json(canonical), canonical, payload


def test_file_hashes_is_inside_the_hashed_payload() -> None:
    """The mechanism behind the whole note. If this stops holding, the note is wrong."""
    _h, _j, payload = _snapshot(_REPO / "config")
    assert "file_hashes" in payload, (
        "AppConfig.file_hashes is no longer in the snapshot payload — the NI-6 note at "
        "config_snapshotter._resolve_config_dict is now WRONG and must be rewritten."
    )
    assert "system_config.yaml" in payload["file_hashes"]


def test_a_comment_only_edit_moves_the_hash_but_no_value() -> None:
    """A COMMENT-ONLY change moves config_hash. Every value, and the JSON length, do not.

    This is the half that reads as an anomaly on the first post-deploy day and is
    not one.
    """
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "config"
        shutil.copytree(_REPO / "config", cfg)
        before_hash, before_json, before_payload = _snapshot(cfg)

        # append a pure comment line -- binary mode, so no newline translation
        target = cfg / "system_config.yaml"
        raw = target.read_bytes()
        target.write_bytes(raw + b"\n# NI-6 probe: a comment, and nothing else.\n")

        after_hash, after_json, after_payload = _snapshot(cfg)

    assert after_hash != before_hash, (
        "a comment-only edit did NOT move config_hash — the NI-6 note is wrong"
    )
    assert after_payload["file_hashes"] != before_payload["file_hashes"]

    # ...and nothing about the VALUES moved. Same length too: file_hashes is a
    # fixed-width hex digest, so even the canonical JSON's SIZE is unchanged —
    # which is why the hash is the only thing that moves and why it looks alarming.
    assert len(before_json) == len(after_json)
    before_payload.pop("file_hashes")
    after_payload.pop("file_hashes")
    assert before_payload == after_payload, "a comment-only edit changed a config VALUE"
    assert config_to_canonical_json(before_payload) == config_to_canonical_json(after_payload)


def test_the_five_item_1_keys_are_in_the_snapshot() -> None:
    """The other half of the expected change: five NEW fields, correctly recorded."""
    _h, _j, payload = _snapshot(_REPO / "config")
    ps = payload["system"]["position_sizing"]
    risk = payload["system"]["risk"]
    for key in ("delivery_risk_per_trade_pct", "delivery_max_concentration_pct",
                "delivery_max_position_value_pct"):
        assert key in ps, f"{key} missing from the snapshotted config"
    for key in ("delivery_max_sector_exposure_pct", "delivery_daily_loss_limit_pct"):
        assert key in risk, f"{key} missing from the snapshotted config"
