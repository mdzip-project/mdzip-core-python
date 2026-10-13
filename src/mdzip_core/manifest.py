"""manifest.json handling (Section 6).

Manifests are kept as plain ``dict`` objects, so every field — including ones
this library doesn't know — survives a read/modify/write cycle (Section 6.3).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .errors import ERR_ENTRYPOINT_INVALID, ERR_MANIFEST_INVALID, ERR_MODE_UNSUPPORTED, ERR_VERSION_UNSUPPORTED, MdzError
from .paths import cover_error, entry_point_error

SPEC_NAME = "mdzip-spec"
SPEC_VERSION = "1.2.0"
"""The MDZip specification version this library implements and writes."""
SUPPORTED_MAJOR = 1
MODES = ("document", "project")

# Semantic Versioning 2.0.0, from semver.org (no leading zeros in numeric identifiers).
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
# RFC 3339 date-time (Section 6.1).
_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$")

Manifest = Dict[str, Any]


def parse_manifest_with_duplicates(data: bytes) -> Tuple[Manifest, List[str]]:
    """Parse manifest.json bytes; also return the names of duplicated members (Section 6.4).

    Raises ERR_MANIFEST_INVALID if the bytes aren't UTF-8 JSON or the top-level
    value isn't an object. For a duplicated member the last occurrence wins.
    """
    duplicates: List[str] = []

    def pairs(items):
        obj: Dict[str, Any] = {}
        for key, value in items:
            if key in obj:
                duplicates.append(key)
            obj[key] = value
        return obj

    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise MdzError(ERR_MANIFEST_INVALID, f"manifest.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MdzError(ERR_MANIFEST_INVALID, "manifest.json must be a JSON object")
    return value, duplicates


def parse_manifest(data: bytes) -> Manifest:
    """Parse manifest.json bytes (UTF-8 JSON object), or raise ERR_MANIFEST_INVALID."""
    return parse_manifest_with_duplicates(data)[0]


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_date_time(value: Any) -> bool:
    return isinstance(value, str) and bool(_DATE_TIME.match(value))


def _is_timestamp(value: Any) -> bool:
    return _is_date_time(value) or (isinstance(value, dict) and _is_date_time(value.get("when")))


_DESCRIPTIVE = {
    "title": lambda v: isinstance(v, str) and v != "",
    "language": _is_str,
    "description": _is_str,
    "version": _is_str,
    "license": _is_str,
    "keywords": lambda v: isinstance(v, list) and all(isinstance(k, str) for k in v),
    "author": lambda v: isinstance(v, dict),
    "producer": lambda v: isinstance(v, dict),
    "created": _is_timestamp,
    "modified": _is_timestamp,
    "cover": lambda v: cover_error(v) is None,
}


def validate_manifest(manifest: Manifest, *, entry_point: bool = True) -> List[str]:
    """Validate a parsed manifest (Section 6.4) and return the descriptive fields to ignore.

    Raises MdzError for a critical field with a wrong type or invalid value:
    ``spec``/``spec.version`` (ERR_MANIFEST_INVALID, or ERR_VERSION_UNSUPPORTED
    for a newer major), ``mode`` (ERR_MANIFEST_INVALID or ERR_MODE_UNSUPPORTED)
    and, unless ``entry_point`` is False, ``entryPoint`` (ERR_ENTRYPOINT_INVALID).
    Unrecognized fields are never checked.
    """
    check_spec_version(manifest)
    manifest_mode(manifest)
    if entry_point and "entryPoint" in manifest:
        problem = entry_point_error(manifest["entryPoint"])
        if problem:
            raise MdzError(ERR_ENTRYPOINT_INVALID, problem)
    return [name for name, ok in _DESCRIPTIVE.items() if name in manifest and not ok(manifest[name])]


def serialize_manifest(manifest: Manifest) -> bytes:
    """Serialize a manifest as UTF-8 JSON with LF line endings, keeping field order."""
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def manifest_mode(manifest: Optional[Manifest]) -> str:
    """The archive's mode (Section 5.7): 'document' by default; raises for unrecognized values."""
    if manifest is None or "mode" not in manifest:
        return "document"
    mode = manifest["mode"]
    if not isinstance(mode, str):
        raise MdzError(ERR_MANIFEST_INVALID, f"mode must be a string, not {json.dumps(mode)}")
    if mode not in MODES:
        raise MdzError(ERR_MODE_UNSUPPORTED, f"unrecognized mode {mode!r}; supported: {', '.join(MODES)}")
    return mode


def spec_version(manifest: Optional[Manifest]) -> Optional[str]:
    """The manifest's ``spec.version``, or None if absent."""
    if manifest is None:
        return None
    spec = manifest.get("spec")
    if isinstance(spec, dict) and isinstance(spec.get("version"), str):
        return spec["version"]
    return None


def check_spec_version(manifest: Optional[Manifest]) -> None:
    """Reject a manifest whose ``spec.version`` major exceeds what this library supports (Section 11.2).

    A missing version is accepted as baseline 1.x (Section 11.3).
    """
    if manifest is None or "spec" not in manifest:
        return
    spec = manifest["spec"]
    if not isinstance(spec, dict):
        raise MdzError(ERR_MANIFEST_INVALID, f"spec must be an object, not {json.dumps(spec)}")
    if "version" not in spec:
        return
    version = spec["version"]
    if not isinstance(version, str):
        raise MdzError(ERR_MANIFEST_INVALID, f"spec.version must be a string, not {json.dumps(version)}")
    match = _SEMVER.match(version)
    if not match:
        raise MdzError(ERR_MANIFEST_INVALID, f"spec.version {version!r} is not a semantic version")
    if int(match.group(1)) > SUPPORTED_MAJOR:
        raise MdzError(
            ERR_VERSION_UNSUPPORTED,
            f"archive targets spec {version}; this library supports {SUPPORTED_MAJOR}.x",
        )


def set_modified(manifest: Manifest, when: str) -> None:
    """Refresh ``modified`` keeping its existing form (ISO string or object with ``when``)."""
    current = manifest.get("modified")
    if isinstance(current, dict):
        current["when"] = when
    else:
        manifest["modified"] = when if isinstance(current, str) else {"when": when}
