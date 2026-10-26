"""Local API-key configuration, independent of the process working directory."""

import os
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_openai_api_key(project_root: Path | None = None) -> str:
    """Read the environment first, otherwise the root .env OPENAI_API_KEY entry.

    Only this key is read; no shell execution, variable expansion or environment
    mutation. Supports UTF-8 BOM, optional export, quotes and trailing comments.
    """
    if "OPENAI_API_KEY" in os.environ:
        return os.environ["OPENAI_API_KEY"].strip()
    path = (project_root if project_root is not None else PROJECT_ROOT) / ".env"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError):
        raise ValueError("Cannot read project .env; check permissions and UTF-8 encoding") from None
    value = ""
    for line in text.splitlines():
        match = re.match(r"^\s*(?:export\s+)?OPENAI_API_KEY\s*=\s*(.*)$", line)
        if match is None:
            continue
        raw = match[1].strip()
        if raw.startswith(("'", '"')):
            closing = raw.find(raw[0], 1)
            if closing == -1 or (raw[closing + 1:].strip() and not raw[closing + 1:].lstrip().startswith("#")):
                raise ValueError("Invalid OPENAI_API_KEY quoting in project .env")
            value = raw[1:closing].strip()
        else:
            value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
            if value.startswith("#"):
                value = ""
    return value


def valid_api_key_format(key: str) -> bool:
    """Check local formatting only; no network or credential validation."""
    return bool(key) and key.isascii() and not any(char.isspace() for char in key)
