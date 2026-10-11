"""manifest.json handling (Section 6).

Manifests are kept as plain ``dict`` objects, so every field — including ones
this library doesn't know — survives a read/modify/write cycle (Section 6.3).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from .errors import ERR_MANIFEST_INVALID, ERR_MODE_UNSUPPORTED, ERR_VERSION_UNSUPPORTED, MdzError

SPEC_NAME = "mdzip-spec"
SPEC_VERSION = "1.2.0"
"""The MDZip specification version this library implements and writes."""
SUPPORTED_MAJOR = 1
MODES = ("document", "project")

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

Manifest = Dict[str, Any]


def parse_manifest(data: bytes) -> Manifest:
    """Parse manifest.json bytes (UTF-8 JSON object), or raise ERR_MANIFEST_INVALID."""
    try:
        text = data.decode("utf-8-sig")
        value = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise MdzError(ERR_MANIFEST_INVALID, f"manifest.json is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise MdzError(ERR_MANIFEST_INVALID, "manifest.json must be a JSON object")
    return value


def serialize_manifest(manifest: Manifest) -> bytes:
    """Serialize a manifest as UTF-8 JSON with LF line endings, keeping field order."""
    return (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def manifest_mode(manifest: Optional[Manifest]) -> str:
    """The archive's mode (Section 5.7): 'document' by default; raises for unrecognized values."""
    if manifest is None or "mode" not in manifest:
        return "document"
    mode = manifest["mode"]
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
    version = spec_version(manifest)
    if version is None:
        return
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
