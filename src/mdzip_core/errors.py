"""Errors raised by mdzip, with stable error codes.

Codes follow the MDZip specification's suggested error categories
(Section 16.5) where one exists; a few more specific codes are used for path
validation and resource limits, matching the spec's shared test fixtures.
"""

from __future__ import annotations

# Archive container (Section 4)
ERR_ZIP_INVALID = "ERR_ZIP_INVALID"
ERR_ZIP_ENCRYPTED = "ERR_ZIP_ENCRYPTED"

# Paths (Section 5.4). ERR_PATH_INVALID is the general category; the
# specific codes below are the ones used by path-validation-cases.json.
ERR_PATH_INVALID = "ERR_PATH_INVALID"
ERR_PATH_EMPTY = "ERR_PATH_EMPTY"
ERR_PATH_LEADING_SLASH = "ERR_PATH_LEADING_SLASH"
ERR_PATH_TRAVERSAL = "ERR_PATH_TRAVERSAL"
ERR_PATH_RESERVED_CHAR = "ERR_PATH_RESERVED_CHAR"
ERR_PATH_CONTROL_CHAR = "ERR_PATH_CONTROL_CHAR"
ERR_DUPLICATE_PATH = "ERR_DUPLICATE_PATH"
ERR_PATH_CONFLICT = "ERR_PATH_CONFLICT"
"""A file's path is also the directory of another entry, such as ``docs`` and ``docs/intro.md``."""

# Manifest and entry point (Sections 5.5, 6, 11)
ERR_MANIFEST_INVALID = "ERR_MANIFEST_INVALID"
ERR_ENTRYPOINT_INVALID = "ERR_ENTRYPOINT_INVALID"
ERR_ENTRYPOINT_MISSING = "ERR_ENTRYPOINT_MISSING"
ERR_ENTRYPOINT_UNRESOLVED = "ERR_ENTRYPOINT_UNRESOLVED"
ERR_VERSION_UNSUPPORTED = "ERR_VERSION_UNSUPPORTED"
ERR_MODE_UNSUPPORTED = "ERR_MODE_UNSUPPORTED"

# Implementation-specific
ERR_LIMIT_EXCEEDED = "ERR_LIMIT_EXCEEDED"
"""A configured resource limit (entries, size, compression ratio) was exceeded."""
ERR_ENTRYPOINT_CHANGED = "ERR_ENTRYPOINT_CHANGED"
"""An update would change or break which file the entry point resolves to (Section 14.1)."""
ERR_NOT_FOUND = "ERR_NOT_FOUND"
"""A requested entry isn't in the archive."""


class MdzError(Exception):
    """An MDZip error with a stable ``code`` (e.g. ``"ERR_ENTRYPOINT_MISSING"``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
