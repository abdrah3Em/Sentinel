import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sentinel import process  # noqa: E402


@pytest.fixture(autouse=True)
def _select_process(request):
    """Grid tests live in test_grid_*.py; everything else exercises the oil pumping station."""
    process.use("grid" if "test_grid" in request.node.nodeid else "oil")
    yield
