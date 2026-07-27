"""27-Jul-2026 -- the logs/ + reports/ write guard (tests/conftest.py).

Closes the last two doors of the test-side-effect class, after outbound network
(4bb32a1) and the real data_store (1542c6e).

⭐ THESE TESTS EXIST TO PROVE THE GUARD IS NOT VACUOUS. Every assertion here fails
if ``_block_real_artifact_dirs`` is removed or narrowed -- which is the only thing
that makes the suite's silence about logs/ meaningful.
"""
from pathlib import Path

import pytest

# ⚠️ NOT ``from tests.conftest import RealArtifactDirBlocked``. pytest loads the
# root conftest as the top-level module ``conftest``; importing it again via the
# ``tests.conftest`` path yields a SECOND module object with a DIFFERENT class of
# the same name, and ``pytest.raises`` then fails to match an exception the guard
# really did raise. Matching on the base class + the distinctive message is immune
# to how the conftest happens to be imported. ``test_the_exception_type_is_ours``
# below keeps it precise.
#
# The base is OSError, not RuntimeError -- see the guard's docstring for why that
# choice is load-bearing (production tolerates an unwritable logs/ on purpose).
RealArtifactDirBlocked = OSError

PROJECT_ROOT = Path(__file__).parent.parent.parent
REAL_LOGS = PROJECT_ROOT / "logs"
REAL_REPORTS = PROJECT_ROOT / "reports"


def test_builtins_open_write_into_real_logs_is_blocked():
    """The plain ``open(..., 'w')`` door."""
    with pytest.raises(RealArtifactDirBlocked, match="BLOCKED write into the real logs/"):
        open(REAL_LOGS / "test_guard_should_never_exist.log", "w")


def test_the_exception_type_is_ours():
    """Keeps the OSError match above honest: the guard raises its own type, not some
    incidental OSError (e.g. a genuine ENOENT) from elsewhere."""
    with pytest.raises(OSError) as exc:
        open(REAL_LOGS / "test_guard_type_check.log", "w")
    assert type(exc.value).__name__ == "RealArtifactDirBlocked"


def test_production_best_effort_writes_stay_best_effort():
    """⭐ THE REASON THE BASE CLASS IS OSError.

    ``main.py:1763-1767`` writes the holiday-reminder sentinel inside
    ``except OSError: pass`` -- a deliberate decision that the write is best-effort.
    The guard must be caught by that handler, so the side effect is prevented WITHOUT
    turning a working production path into 4 failing tests. Reproduces that shape.

    ⛔ If this ever fails, the guard was widened back to RuntimeError and
    test_interactive_startup.py is about to break for a reason nobody will connect
    to this file.
    """
    sentinel = REAL_LOGS / ".holiday_notified_9999-06-06"
    try:
        sentinel.write_text("x")
    except OSError:
        pass                                    # exactly production's handler
    assert not sentinel.exists(), "the write must not have happened"


def test_builtins_open_append_into_real_logs_is_blocked():
    """Append creates the file too -- 'a' must be caught, not just 'w'."""
    with pytest.raises(RealArtifactDirBlocked):
        open(REAL_LOGS / "test_guard_append.log", "a")


def test_path_write_text_into_real_logs_is_blocked():
    """⭐ THE ONE THAT MOTIVATED THE io.open PATCH.

    ``Path.write_text`` routes through ``io.open``, which resolves the name from the
    ``io`` module at call time -- patching ``builtins.open`` alone does NOT catch it.
    This is exactly how ``main.py:1751`` writes the holiday-reminder suppression
    marker that was measured in the real logs/ on 27-Jul.
    """
    with pytest.raises(RealArtifactDirBlocked):
        (REAL_LOGS / ".holiday_notified_9999-12-31").write_text("blocked")


def test_the_actual_production_call_site_is_blocked():
    """main.py:1751's exact shape: Path("logs") / f".holiday_notified_{date}".

    Uses a RELATIVE path, as production does, so this also proves the guard resolves
    relative paths against cwd rather than only matching absolutes.
    """
    with pytest.raises(RealArtifactDirBlocked):
        (Path("logs") / ".holiday_notified_9999-01-01").write_text("x")


def test_write_into_real_reports_is_blocked():
    with pytest.raises(RealArtifactDirBlocked, match="BLOCKED write into the real reports/"):
        open(REAL_REPORTS / "test_guard_should_never_exist.txt", "w")


def test_nested_write_under_real_reports_is_blocked():
    """A subdirectory is still inside the protected root."""
    with pytest.raises(RealArtifactDirBlocked):
        open(REAL_REPORTS / "daily_review" / "test_guard.json", "w")


def test_reads_are_not_blocked():
    """Reads are deliberately allowed -- unlike a WAL DB, reading a file creates
    nothing, and tests legitimately read committed fixture content under reports/."""
    assert (REAL_REPORTS / "__init__.py").read_text() is not None


def test_writing_elsewhere_is_unaffected(tmp_path):
    """The guard must not be a blanket write ban."""
    target = tmp_path / "fine.log"
    target.write_text("ok")
    assert target.read_text() == "ok"
    with open(tmp_path / "also_fine.log", "w") as fh:
        fh.write("ok")


def test_opt_in_fixture_restores_the_real_open(allow_real_artifact_dirs, tmp_path):
    """The escape hatch works -- and is visible in the test signature.

    Writes to tmp_path, not to the real tree: proving the fixture restores the
    unguarded ``open`` does not require actually polluting logs/.
    """
    import builtins
    import io
    assert builtins.open.__name__ != "guarded_builtins_open"
    assert io.open.__name__ != "guarded_io_open"
    (tmp_path / "x.log").write_text("ok")
