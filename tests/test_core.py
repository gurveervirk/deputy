from deputy.core import create_context
from unittest.mock import MagicMock

def test_create_context_sets_skip_paths():
    conn = MagicMock()
    ctx = create_context("/base", conn)
    assert ".venv" in ctx.skip_paths
    assert ".git" in ctx.skip_paths
    assert "__pycache__" in ctx.skip_paths
