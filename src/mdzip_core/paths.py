"""Archive path rules: validation (Section 5.4), matching (Section 5.4.1),
Markdown extensions (Section 7.3) and support files (Section 5.5)."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional

from .errors import (
    ERR_DUPLICATE_PATH,
    ERR_PATH_CONFLICT,
    ERR_PATH_CONTROL_CHAR,
    ERR_PATH_EMPTY,
    ERR_PATH_INVALID,
    ERR_PATH_LEADING_SLASH,
    ERR_PATH_RESERVED_CHAR,
    ERR_PATH_TRAVERSAL,
    MdzError,
)

SUPPORT_FILE_NAMES = ("readme", "agents", "license", "changelog", "contributing")
"""Support file names (Section 5.5), compared ASCII case-insensitively, with .md or .markdown."""

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED = re.compile(r'[\\:*?"<>|]')
_MARKDOWN_EXT = re.compile(r"\.(md|markdown)$")


def nfc(path: str) -> str:
    """Unicode Normalization Form C."""
    return unicodedata.normalize("NFC", path)


def ascii_lower(text: str) -> str:
    """Lowercase A–Z only, never locale-aware (Section 5.4.1): ``LICENSE`` → ``license``."""
    return "".join(chr(ord(c) + 32) if "A" <= c <= "Z" else c for c in text)


def same_path(a: str, b: str) -> bool:
    """Path equality per Section 5.4.1: case-sensitive, after NFC normalization."""
    return nfc(a) == nfc(b)


def find_path(paths: Iterable[str], wanted: str) -> Optional[str]:
    """The stored path in ``paths`` that matches ``wanted`` (Section 5.4.1), or None."""
    target = nfc(wanted)
    for p in paths:
        if nfc(p) == target:
            return p
    return None


def is_markdown(path: str) -> bool:
    """True for ``.md`` / ``.markdown`` files, extension compared ASCII case-insensitively."""
    return bool(_MARKDOWN_EXT.search(ascii_lower(path)))


def is_support_file(path: str) -> bool:
    """True for a root-level support file (Section 5.5): README, AGENTS, LICENSE, CHANGELOG, CONTRIBUTING."""
    if "/" in path or not is_markdown(path):
        return False
    stem = _MARKDOWN_EXT.sub("", ascii_lower(path))
    return stem in SUPPORT_FILE_NAMES


def path_error(path: str) -> Optional[MdzError]:
    """Why ``path`` isn't a valid archive entry path (Section 5.4), or None if it is."""
    if path == "":
        return MdzError(ERR_PATH_EMPTY, "path is empty")
    if path.startswith("/"):
        return MdzError(ERR_PATH_LEADING_SLASH, f"'{path}' starts with '/'")
    if _CONTROL.search(path):
        return MdzError(ERR_PATH_CONTROL_CHAR, f"'{path!r}' contains a control character")
    if _RESERVED.search(path):
        return MdzError(ERR_PATH_RESERVED_CHAR, f"'{path}' contains a reserved character (\\ : * ? \" < > |)")
    # A trailing "/" marks a directory entry; every segment before it must be a real name.
    segments = (path[:-1] if path.endswith("/") else path).split("/")
    if any(s == ".." for s in segments):
        return MdzError(ERR_PATH_TRAVERSAL, f"'{path}' contains a '..' segment")
    if any(s == "" or s == "." for s in segments):
        return MdzError(ERR_PATH_INVALID, f"'{path}' contains an empty or '.' segment")
    return None


def entry_set_error(names: Iterable[str]) -> Optional[MdzError]:
    """Why a set of entry names can't form one archive (Section 5.4), or None.

    Names ending in ``/`` are directory entries. Two names equal after NFC are
    duplicates (``ERR_DUPLICATE_PATH``); a file whose path is also a directory
    of another entry is a conflict (``ERR_PATH_CONFLICT``).
    """
    files: dict = {}
    directories: dict = {}
    for name in names:
        is_dir = name.endswith("/")
        key = nfc(name[:-1] if is_dir else name)
        if not is_dir:
            if key in files:
                return MdzError(ERR_DUPLICATE_PATH, f"'{name}' and '{files[key]}' are the same path (Section 5.4)")
            files[key] = name
        else:
            directories.setdefault(key, name)
        parts = key.split("/")
        for i in range(1, len(parts)):
            directories.setdefault("/".join(parts[:i]), name)
    for key, name in files.items():
        if key in directories:
            return MdzError(ERR_PATH_CONFLICT, f"'{name}' is a file and also a directory of '{directories[key]}' (Section 5.4)")
    return None


def validate_path(path: str) -> None:
    """Raise MdzError if ``path`` isn't a valid archive entry path (Section 5.4)."""
    error = path_error(path)
    if error:
        raise error


def entry_point_error(value: object) -> Optional[str]:
    """Why a manifest ``entryPoint`` value isn't a valid entry point path (Section 5.5), or None.

    Valid: a non-empty string that is a relative archive path (Section 5.4),
    names a file (no trailing ``/``) and has a ``.md`` / ``.markdown``
    extension. A single leading ``./`` is allowed.
    """
    if not isinstance(value, str) or value == "":
        return "entryPoint must be a non-empty string"
    path = value[2:] if value.startswith("./") else value
    if path.endswith("/"):
        return f"entryPoint '{value}' names a directory"
    error = path_error(path)
    if error:
        return f"entryPoint '{value}' is not a valid archive path ({error.message})"
    if not is_markdown(path):
        return f"entryPoint '{value}' is not a .md or .markdown file"
    return None


def cover_error(value: object) -> Optional[str]:
    """Why a manifest ``cover`` value isn't a valid path (Section 6.2), or None.

    Same path rules as ``entryPoint`` (relative, a file, optional leading
    ``./``), without the Markdown extension requirement.
    """
    if not isinstance(value, str) or value == "":
        return "cover must be a non-empty string"
    path = value[2:] if value.startswith("./") else value
    if path.endswith("/"):
        return f"cover '{value}' names a directory"
    error = path_error(path)
    return f"cover '{value}' is not a valid archive path ({error.message})" if error else None


def normalize_entry_point(value: str) -> str:
    """Strip a single leading ``./`` from a valid entryPoint value."""
    return value[2:] if value.startswith("./") else value
