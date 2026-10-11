"""Reading .mdz archives safely (Sections 4, 5, 13)."""

from __future__ import annotations

import builtins
import io
import os
import stat
import zipfile
from dataclasses import dataclass, field
from typing import IO, Dict, List, Optional, Union

from .entrypoint import resolve_entry_point
from .errors import (
    ERR_DUPLICATE_PATH,
    ERR_LIMIT_EXCEEDED,
    ERR_MANIFEST_INVALID,
    ERR_NOT_FOUND,
    ERR_ZIP_ENCRYPTED,
    ERR_ZIP_INVALID,
    MdzError,
)
from .links import ArchiveSource, FilesystemHost, LinkHost, LinkTarget, resolve_link
from .manifest import Manifest, check_spec_version, manifest_mode, parse_manifest
from .paths import find_path, is_markdown, nfc, path_error

MANIFEST_NAME = "manifest.json"

Source = Union[str, os.PathLike, bytes, bytearray, IO[bytes]]


@dataclass(frozen=True)
class Limits:
    """Resource limits applied when opening and reading archives (Section 13.1).

    Declared sizes in the ZIP directory are checked on open; actual
    decompressed bytes are checked again while reading, since declared sizes
    can lie.
    """

    max_entries: int = 100_000
    max_total_uncompressed: int = 4 * 1024**3
    max_entry_uncompressed: int = 1024**3
    max_compression_ratio: float = 1000.0
    """Per entry, uncompressed / compressed, for entries over 1 MiB."""


DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Diagnostic:
    """A finding from ``MdzArchive.validate``: ``level`` is 'error', 'warning' or 'info'."""

    level: str
    code: str
    message: str


def _repair_name(info: zipfile.ZipInfo) -> str:
    """Entry name as UTF-8 (Section 4). Archives without the UTF-8 flag are
    decoded by zipfile as CP437; many tools write UTF-8 anyway, so try that."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        return name.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


class MdzArchive:
    """A read-only, opened .mdz archive.

    Use ``MdzArchive.open(path_or_bytes)``. The archive contents are kept in
    memory; nothing is ever extracted to disk.
    """

    def __init__(self, data: bytes, *, path: Optional[str] = None, limits: Limits = DEFAULT_LIMITS) -> None:
        self._data = data
        self.path = path
        """Filesystem path the archive was opened from, if any."""
        self.limits = limits
        self.warnings: List[Diagnostic] = []
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, ValueError, OSError) as exc:
            raise MdzError(ERR_ZIP_INVALID, f"not a valid ZIP archive: {exc}") from exc

        self._infos: Dict[str, zipfile.ZipInfo] = {}
        self._directories: List[str] = []
        seen: Dict[str, str] = {}
        total = 0
        infos = self._zip.infolist()
        if len(infos) > limits.max_entries:
            raise MdzError(ERR_LIMIT_EXCEEDED, f"archive has {len(infos)} entries (limit {limits.max_entries})")
        for info in infos:
            if info.flag_bits & 0x1:
                raise MdzError(ERR_ZIP_ENCRYPTED, f"entry '{info.filename}' is encrypted")
            name = _repair_name(info)
            if name.endswith("/"):
                self._directories.append(name)
                continue
            error = path_error(name)
            if error:
                raise error
            if _is_symlink(info):
                self.warnings.append(Diagnostic("warning", "ERR_PATH_INVALID", f"symbolic link '{name}' ignored"))
                continue
            key = nfc(name)
            if key in seen:
                raise MdzError(ERR_DUPLICATE_PATH, f"'{name}' and '{seen[key]}' are the same path (Section 5.4)")
            seen[key] = name
            self._check_declared_size(name, info)
            total += info.file_size
            if total > limits.max_total_uncompressed:
                raise MdzError(ERR_LIMIT_EXCEEDED, f"total uncompressed size exceeds {limits.max_total_uncompressed} bytes")
            self._infos[name] = info

        self.manifest: Optional[Manifest] = None
        """The parsed manifest.json (a plain dict, all fields kept), or None."""
        manifest_name = find_path(self._infos, MANIFEST_NAME)
        if manifest_name is not None:
            self.manifest = parse_manifest(self.read_bytes(manifest_name))
            check_spec_version(self.manifest)
        self.mode = manifest_mode(self.manifest)
        """'document' or 'project' (Section 5.7)."""

    def _check_declared_size(self, name: str, info: zipfile.ZipInfo) -> None:
        limits = self.limits
        if info.file_size > limits.max_entry_uncompressed:
            raise MdzError(ERR_LIMIT_EXCEEDED, f"'{name}' is {info.file_size} bytes (limit {limits.max_entry_uncompressed})")
        if info.file_size > 1024 * 1024 and info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > limits.max_compression_ratio:
                raise MdzError(
                    ERR_LIMIT_EXCEEDED,
                    f"'{name}' has compression ratio {ratio:.0f}:1 (limit {limits.max_compression_ratio:.0f}:1)",
                )

    # ---- Opening ----

    @classmethod
    def open(cls, source: Source, *, limits: Limits = DEFAULT_LIMITS) -> "MdzArchive":
        """Open an archive from a path, bytes, or a binary file object."""
        if isinstance(source, (bytes, bytearray)):
            return cls(bytes(source), limits=limits)
        if hasattr(source, "read"):
            return cls(source.read(), limits=limits)  # type: ignore[union-attr]
        path = os.fspath(source)
        with builtins.open(path, "rb") as f:
            return cls(f.read(), path=path, limits=limits)

    def __enter__(self) -> "MdzArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._zip.close()

    # ---- Contents ----

    @property
    def data(self) -> bytes:
        """The archive bytes."""
        return self._data

    @property
    def files(self) -> List[str]:
        """File entry paths (no directories), as stored."""
        return list(self._infos)

    @property
    def documents(self) -> List[str]:
        """Markdown file entries, sorted."""
        return sorted(p for p in self._infos if p.lower() != MANIFEST_NAME and is_markdown(p))

    def find(self, path: str) -> Optional[str]:
        """The stored entry path matching ``path`` (NFC, case-sensitive; Section 5.4.1), or None."""
        return find_path(self._infos, path)

    def __contains__(self, path: str) -> bool:
        return self.find(path) is not None

    def read_bytes(self, path: str) -> bytes:
        """An entry's bytes, enforcing the decompressed-size limits as it reads."""
        name = self.find(path)
        if name is None:
            raise MdzError(ERR_NOT_FOUND, f"'{path}' is not in the archive")
        info = self._infos[name]
        cap = min(self.limits.max_entry_uncompressed, max(info.file_size, 0)) + 1
        with self._zip.open(info) as f:
            data = f.read(cap)
        if len(data) > info.file_size or len(data) > self.limits.max_entry_uncompressed:
            raise MdzError(ERR_LIMIT_EXCEEDED, f"'{name}' decompresses to more than its declared size")
        return data

    def read_text(self, path: str) -> str:
        """An entry's text, decoded as UTF-8 (Section 7.1); CRLF is left as written."""
        return self.read_bytes(path).decode("utf-8-sig")

    def info(self, path: str) -> zipfile.ZipInfo:
        """The ZIP metadata of an entry."""
        name = self.find(path)
        if name is None:
            raise MdzError(ERR_NOT_FOUND, f"'{path}' is not in the archive")
        return self._infos[name]

    # ---- Entry point and metadata ----

    @property
    def entry_point(self) -> str:
        """The primary Markdown file (Section 5.5). Raises MdzError if it can't be determined."""
        return resolve_entry_point(self._infos, self.manifest)

    @property
    def title(self) -> Optional[str]:
        """The manifest title, or None."""
        value = (self.manifest or {}).get("title")
        return value if isinstance(value, str) and value else None

    # ---- Links ----

    def resolve_link(self, document: str, link: str, host: Optional[LinkHost] = None) -> LinkTarget:
        """Resolve ``link`` written in ``document`` of this archive (Sections 9.3–9.5).

        Without ``host``: if the archive was opened from a file, links may
        reach files in the archive's own directory (and below it); if it was
        opened from bytes, links that leave the archive are unresolved
        (no host location, Section 9.4).
        """
        if host is None:
            host = _DefaultHost(self)
        name = os.path.basename(self.path) if self.path else "archive.mdz"
        archive_path = getattr(host, "archive_path", name)
        return resolve_link(ArchiveSource(archive_path, document), link, host)

    # ---- Validation ----

    def validate(self) -> List[Diagnostic]:
        """Conformance findings for this archive, without raising.

        Opening already rejects invalid ZIPs, encryption, invalid or duplicate
        paths, unsupported versions and modes; this adds the entry point,
        manifest content and cover checks.
        """
        found = list(self.warnings)
        try:
            self.entry_point
        except MdzError as exc:
            found.append(Diagnostic("error", exc.code, exc.message))
        if self.manifest is None:
            found.append(
                Diagnostic("info", "INFO_NO_MANIFEST", "no manifest.json; valid, but a conforming producer would write one (Section 14.1)")
            )
        else:
            if not isinstance(self.manifest.get("spec"), dict) or "version" not in self.manifest.get("spec", {}):
                found.append(Diagnostic("warning", ERR_MANIFEST_INVALID, "manifest has no spec.version (required in producer-written manifests)"))
            if "entryPoint" not in self.manifest:
                found.append(Diagnostic("warning", ERR_MANIFEST_INVALID, "manifest has no entryPoint (required in producer-written manifests)"))
            title = self.manifest.get("title")
            if title is not None and (not isinstance(title, str) or title == ""):
                found.append(Diagnostic("warning", ERR_MANIFEST_INVALID, "title must be a non-empty string"))
            cover = self.manifest.get("cover")
            if isinstance(cover, str) and self.find(cover) is None:
                found.append(Diagnostic("warning", "WARN_COVER_MISSING", f"cover '{cover}' is not in the archive"))
        return found


class _DefaultHost:
    """Host for MdzArchive.resolve_link without an explicit host."""

    def __init__(self, archive: MdzArchive) -> None:
        self._archive = archive
        self.archive_path = os.path.basename(archive.path) if archive.path else "archive.mdz"
        self.host_location = archive.path is not None
        self.outside_links = True
        self.into_archive_links = True
        self._fs = FilesystemHost(os.path.dirname(os.path.abspath(archive.path))) if archive.path else None

    def kind(self, path: str):
        return self._fs.kind(path) if self._fs else None

    def find_entry(self, archive: str, entry: str) -> Optional[str]:
        if archive == self.archive_path:
            return self._archive.find(entry)
        return self._fs.find_entry(archive, entry) if self._fs else None

    def entry_point(self, archive: str) -> Optional[str]:
        if archive == self.archive_path:
            try:
                return self._archive.entry_point
            except MdzError:
                return None
        return self._fs.entry_point(archive) if self._fs else None


def open(source: Source, *, limits: Limits = DEFAULT_LIMITS) -> MdzArchive:  # noqa: A001 - mirrors zipfile/io style
    """Open an .mdz archive from a path, bytes or binary file object."""
    return MdzArchive.open(source, limits=limits)
