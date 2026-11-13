"""Atomic file writers, so an interrupted run never leaves a half-written data file."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any


@contextmanager
def _atomic_open(path: Path) -> Iterator[IO[str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            yield handle
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    """Write ``data`` as pretty-printed UTF-8 JSON, replacing ``path`` atomically.

    Args:
        path: Destination file.
        data: JSON-serialisable data.
    """
    with _atomic_open(path) as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8, replacing ``path`` atomically.

    Args:
        path: Destination file.
        text: File contents.
    """
    with _atomic_open(path) as handle:
        handle.write(text)
