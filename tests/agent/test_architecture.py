"""Keep the reusable runtime independent of the research application."""
import ast
from pathlib import Path

import pytest


@pytest.mark.parametrize("layer, forbidden", [
    ("agent", ("paper_assistant.research", "paper_assistant.tools", "paper_assistant.web", "openai", "numpy", "torch")),
    ("ai", ("paper_assistant.research", "paper_assistant.agent", "paper_assistant.tools", "paper_assistant.web")),
])
def test_runtime_layers_do_not_import_application_or_tool_implementations(layer, forbidden):
    package = Path(__file__).parents[2] / "src/paper_assistant" / layer
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                assert not any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden), (path.name, name)
