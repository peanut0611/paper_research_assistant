"""Small persistence primitive for complete metadata records."""

import os
import tempfile
import time
from pathlib import Path


_WINDOWS = os.name == "nt"


def replace_path(source: Path, destination: Path) -> None:
    """Retry only transient Windows rename locks; publish stays atomic."""
    delays = (.025, .05, .1, .2, .4)
    for attempt in range(len(delays) + 1):
        try:
            source.replace(destination)
            return
        except PermissionError as error:
            if not _WINDOWS or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Replace one file only after its temporary sibling was fully written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        replace_path(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
