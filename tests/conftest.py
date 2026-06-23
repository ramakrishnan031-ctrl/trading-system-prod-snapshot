"""
tests/conftest.py -- Root test configuration

Ensures project root is in sys.path so all imports work correctly, and (23-Jun)
isolates the REAL sentinel directory so no test can write a CRITICAL alert into
the live data_store (the VM alert-watcher would email it).
"""
import sys
from pathlib import Path

import pytest

# Add project root to sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

_REAL_DATA_STORE = (project_root / "data_store").resolve()

# Modules that bind alerts.critical.write_critical_sentinel at IMPORT time (a
# module-level `from alerts.critical import write_critical_sentinel`); patching only
# the source module would miss these, so we also rebind them when they are loaded.
_MODULE_LEVEL_SENTINEL_IMPORTERS = (
    "alerts.telegram_notifier",
    "scripts.cron_watchdog",
    "scripts.cron_officer",
)


@pytest.fixture(autouse=True)
def _isolate_real_sentinels(tmp_path, monkeypatch):
    """Test isolation: NO test may write a CRITICAL sentinel into the REAL
    <project>/data_store. The live alert-watcher (running on the VM) consumes any
    ``critical_alert_*.flag`` there and EMAILS it — so a full-suite run on the VM
    was emailing test-written CRITICALs as real alerts (gemini failure sentinel,
    preflight CRITICALs).

    Any ``write_critical_sentinel`` call whose ``sentinel_dir`` resolves to the real
    data_store (including the bare ``"data_store"`` default) is redirected to a
    per-test tmp sandbox. An explicit non-real dir (a test's own ``tmp_path``) passes
    through unchanged, so sentinel-content tests still work. This isolates TESTS
    only — the production sentinel/alert path is NOT modified.
    """
    import alerts.critical as _ac
    real = _ac.write_critical_sentinel
    sandbox = tmp_path / "_sentinel_sandbox"

    def guarded(*args, **kwargs):
        if "sentinel_dir" in kwargs:
            sd, positional = kwargs["sentinel_dir"], False
        elif len(args) >= 5:
            sd, positional = args[4], True
        else:
            sd, positional = "data_store", False   # the function default -> real dir
        try:
            p = Path(sd)
            if not p.is_absolute():
                p = project_root / p
            hits_real = p.resolve() == _REAL_DATA_STORE
        except Exception:
            hits_real = False
        if hits_real:
            if positional:
                args = args[:4] + (str(sandbox),) + args[5:]
            else:
                kwargs["sentinel_dir"] = sandbox
        return real(*args, **kwargs)

    monkeypatch.setattr(_ac, "write_critical_sentinel", guarded)
    for modname in _MODULE_LEVEL_SENTINEL_IMPORTERS:
        mod = sys.modules.get(modname)
        if mod is not None and hasattr(mod, "write_critical_sentinel"):
            monkeypatch.setattr(mod, "write_critical_sentinel", guarded, raising=False)
    yield
