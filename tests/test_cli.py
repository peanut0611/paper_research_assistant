"""Exercise package entry points from a different cwd without model calls."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("command, expected", [
    ([], "web"), (["web"], "--port"), (["research"], "--task-id"),
])
def test_package_help_routes_without_starting_service_or_model(tmp_path, command, expected):
    root = Path(__file__).parents[1]
    result = subprocess.run([sys.executable, "-m", "paper_assistant", *command, "--help"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(root / "src"), "OPENAI_API_KEY": ""},
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert not (tmp_path / "data").exists()
