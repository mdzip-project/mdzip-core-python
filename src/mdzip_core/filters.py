"""Glob filters for choosing which files to pack (the spec's create-filter fixture)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, Sequence

DEFAULT_FILTERS: Sequence[str] = (
    "**/*.md",
    "**/*.markdown",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.gif",
    "**/*.webp",
    "**/*.svg",
    "**/*.avif",
)
"""Markdown and common image types."""


@lru_cache(maxsize=256)
def _compile(pattern: str) -> "re.Pattern[str]":
    """``**/`` matches zero or more directories, ``**`` anything, ``*`` and ``?`` within one segment.
    Extensions match ASCII case-insensitively."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE | re.ASCII)


def matches_filters(path: str, filters: Iterable[str]) -> bool:
    """True if ``path`` (``/``-separated, relative) matches any of the glob ``filters``."""
    return any(_compile(f).match(path) for f in filters)
