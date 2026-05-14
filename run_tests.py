#!/usr/bin/env python
"""
Simple test runner that ensures sys.path is set correctly before running pytest.
"""
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Now run pytest
import pytest

if __name__ == "__main__":
    sys.exit(pytest.main([
        "tests/unit/",
        "tests/integration/",
        "tests/core/",
        "-q",
        "--tb=no",
    ]))
