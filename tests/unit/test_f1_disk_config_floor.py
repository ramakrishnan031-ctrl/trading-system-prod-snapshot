"""F-1 (audit 02-Jul): the startup min_free_disk_gb floor was silently ignored.

main.py read ``getattr(app_config.system, "min_free_disk_gb", 1.0)``, but the field
lives on the nested logging config (``SystemConfig`` is ``extra="forbid"`` and has no
such top-level attribute) — so the getattr ALWAYS fell back to 1.0, ignoring the
operator-configured 2 GB floor. The fix reads ``app_config.system.logging.min_free_disk_gb``.

Kept in its own module (not appended to test_config_loader.py) so that staging it does
not re-scan test_config_loader.py, whose pre-existing ``password_env`` test lines trip
the commit-time secret scanner as a false positive.
"""
from __future__ import annotations

from pathlib import Path

from core.config_loader import load_all


def test_f1_min_free_disk_gb_reads_from_logging_not_system() -> None:
    cfg = load_all(Path("config"))
    # The fix: the correct path resolves to the configured floor (real config = 2.0).
    assert cfg.system.logging.min_free_disk_gb == 2.0
    # The bug: SystemConfig has no top-level attribute, so the old
    # getattr(app_config.system, "min_free_disk_gb", 1.0) silently returned 1.0.
    assert getattr(cfg.system, "min_free_disk_gb", None) is None
