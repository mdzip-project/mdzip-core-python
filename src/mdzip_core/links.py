"""Link resolution (Sections 9.3–9.5): links inside an archive, links that
leave it for files next to the .mdz, and links into (other) archives.

Resolution needs a *host*: something that knows what files and archives exist
around the archive. ``FilesystemHost`` uses a directory on disk; tests and
applications can supply their own (see ``LinkHost``).
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Optional, Protocol, Union
from urllib.parse import unquote_to_bytes

from .paths import ascii_lower, find_path

UnresolvedReason = Literal["not-found", "invalid", "unsupported", "no-host-location", "nested-archive", "denied"]
LinkUse = Literal["navigate", "embed"]

_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_BAD_SEGMENT = re.compile(r"[/\\\x00-\x1f\x7f]")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")


@dataclass(frozen=True)
class ArchiveSource:
    """A link written in document ``path`` of the archive at host path ``archive``."""

    archive: str
    path: str


@dataclass(frozen=True)
class HostSource:
    """A link written in an ordinary file at host path ``host``."""

    host: str


LinkSource = Union[ArchiveSource, HostSource]


@dataclass(frozen=True)
class LinkTarget:
    """Where a link points.

    ``kind`` is one of:

    - ``internal`` — ``entry`` in the source archive;
    - ``host`` — host file ``path``;
    - ``archive`` — ``entry`` in archive ``archive``, addressed through the host
      (possibly the source archive);
    - ``url`` — an external URL, not resolved as a path;
    - ``unresolved`` — with a ``reason``.
    """

    kind: Literal["internal", "host", "archive", "url", "unresolved"]
    entry: Optional[str] = None
    path: Optional[str] = None
    archive: Optional[str] = None
    fragment: Optional[str] = None
    reason: Optional[UnresolvedReason] = None

    def as_dict(self) -> Dict[str, str]:
        """The non-empty fields, in the shape used by the spec's link fixture."""
        out: Dict[str, str] = {"kind": self.kind}
        for name in ("archive", "entry", "path", "fragment", "reason"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


class LinkHost(Protocol):
    """What link resolution needs to know about the files around an archive."""

    def kind(self, path: str) -> Optional[Literal["file", "directory", "archive"]]:
        """What exists at a host path: a file, a directory, an MDZip archive, or nothing."""

    def find_entry(self, archive: str, entry: str) -> Optional[str]:
        """The stored entry path in ``archive`` matching ``entry`` (NFC, case-sensitive), or None."""

    def entry_point(self, archive: str) -> Optional[str]:
        """The entry point of ``archive`` (Section 5.5), or None."""

    # Optional capability flags (default True); see Sections 9.4 and 9.5.
    host_location: bool
    outside_links: bool
    into_archive_links: bool

    # Optional trust settings (Section 9.6).
    trust_root: Optional[str]
    """Host path of the trust boundary: targets outside it are denied. ``""`` (the
    default) trusts the whole host; None means no trusted location at all."""
    allowed_paths: Iterable[str]
    """Host paths outside the boundary that the user explicitly allowed."""
    load_outside_resources: bool
    """Whether embeds may load resources from outside an archive (inside the boundary only). Default False."""


def _unresolved(reason: UnresolvedReason) -> LinkTarget:
    return LinkTarget(kind="unresolved", reason=reason)


def is_mdz_name(name: str) -> bool:
    """True for a name with an ``.mdz`` extension, compared ASCII case-insensitively."""
    return ascii_lower(name).endswith(".mdz")


def _dir_segments(path: str) -> List[str]:
    return [s for s in path.split("/")[:-1] if s]


def _segments(dest: str) -> Optional[List[str]]:
    """Destination → decoded segments (Section 9.3 steps 3–4), or None if invalid."""
    if dest.startswith("/") or "\\" in dest:
        return None
    segs: List[str] = []
    for raw in dest.split("/"):
        if "%" in raw:
            # Decode exactly once; invalid escapes and non-UTF-8 bytes make the link unresolved (Section 9.3).
            if _BAD_PERCENT.search(raw):
                return None
            s = unquote_to_bytes(raw).decode("utf-8")
        else:
            s = raw
        if _BAD_SEGMENT.search(s):
            return None
        segs.append(s)
    return segs


def _permitted(host: LinkHost, path: str) -> bool:
    """Whether a host path is inside the trust boundary or explicitly allowed (Section 9.6)."""
    if path in set(getattr(host, "allowed_paths", ()) or ()):
        return True
    root = getattr(host, "trust_root", "")
    if root is None:
        return False
    return root == "" or path == root or path.startswith(root.rstrip("/") + "/")


def resolve_link(source: LinkSource, link: str, host: LinkHost, use: LinkUse = "navigate") -> LinkTarget:
    """Resolve ``link`` written in ``source`` (Sections 9.3–9.6).

    ``use`` is ``"navigate"`` for a link the user follows or ``"embed"`` for a
    resource the document loads automatically (an image, stylesheet, ...).
    Host targets outside the host's trust boundary are ``denied``, whether or
    not they exist; embeds that leave an archive are ``denied`` unless the host
    enables ``load_outside_resources``.
    """
    host_location = getattr(host, "host_location", True)
    outside = getattr(host, "outside_links", True)
    into = getattr(host, "into_archive_links", True)
    load_outside = getattr(host, "load_outside_resources", False)

    if _SCHEME.match(link):
        return LinkTarget(kind="url")

    # Split at the first "#" before removing the query: a "?" in the fragment stays there (Section 9.3).
    dest, _, fragment = link.partition("#")
    dest = dest.partition("?")[0]
    frag = fragment or None
    # A trailing "/" names a directory: never an archive entry or a host file (only an archive itself).
    directory = dest.endswith("/")

    def with_frag(target: LinkTarget) -> LinkTarget:
        return LinkTarget(**{**target.__dict__, "fragment": frag}) if frag else target

    if dest == "":
        if isinstance(source, ArchiveSource):
            return with_frag(LinkTarget(kind="internal", entry=source.path))
        return with_frag(LinkTarget(kind="host", path=source.host))

    try:
        segs = _segments(dest)
    except UnicodeDecodeError:
        segs = None
    if segs is None:
        return _unresolved("invalid")

    i = 0
    if isinstance(source, ArchiveSource):
        stack = _dir_segments(source.path)
        left = False
        while i < len(segs):
            s = segs[i]
            i += 1
            if s in ("", "."):
                continue
            if s == "..":
                if not stack:
                    left = True
                    break
                stack.pop()
            else:
                stack.append(s)
        if not left:
            if directory:
                return _unresolved("not-found")
            entry = host.find_entry(source.archive, "/".join(stack))
            return with_frag(LinkTarget(kind="internal", entry=entry)) if entry else _unresolved("not-found")
        if not outside:
            return _unresolved("unsupported")
        if not host_location:
            return _unresolved("no-host-location")
        if use == "embed" and not load_outside:
            return _unresolved("denied")
        stack = _dir_segments(source.archive)
    else:
        stack = _dir_segments(source.host)

    while i < len(segs):
        s = segs[i]
        i += 1
        if s in ("", "."):
            continue
        if s == "..":
            if not stack:
                return _unresolved("not-found")
            stack.pop()
            continue
        stack.append(s)
        host_path = "/".join(stack)
        if is_mdz_name(s) and _permitted(host, host_path) and host.kind(host_path) == "archive":
            if not into:
                return _unresolved("unsupported")
            if isinstance(source, ArchiveSource) and host_path != source.archive and use == "embed" and not load_outside:
                return _unresolved("denied")
            return _inside_archive(host_path, segs[i:], host, with_frag, directory)

    host_path = "/".join(stack)
    # Checked before existence, so a link can't probe for files outside the boundary.
    if not _permitted(host, host_path):
        return _unresolved("denied")
    if directory:
        return _unresolved("not-found")
    return with_frag(LinkTarget(kind="host", path=host_path)) if host.kind(host_path) == "file" else _unresolved("not-found")


def _inside_archive(archive: str, segs: List[str], host: LinkHost, with_frag, directory: bool = False) -> LinkTarget:
    inner: List[str] = []
    for j, s in enumerate(segs):
        if s in ("", "."):
            continue
        if s == "..":
            if not inner:
                return _unresolved("invalid")
            inner.pop()
            continue
        inner.append(s)
        if is_mdz_name(s) and j < len(segs) - 1:
            return _unresolved("nested-archive")
    if not inner:
        entry_point = host.entry_point(archive)
        return with_frag(LinkTarget(kind="archive", archive=archive, entry=entry_point)) if entry_point else _unresolved("not-found")
    if directory:
        return _unresolved("not-found")
    entry = host.find_entry(archive, "/".join(inner))
    return with_frag(LinkTarget(kind="archive", archive=archive, entry=entry)) if entry else _unresolved("not-found")


class FilesystemHost:
    """A LinkHost backed by a directory on disk.

    Host paths are ``/``-separated and relative to ``root``; links can't climb
    above it. Archives are opened read-only on demand (and cached by
    modification time) to look up entries; nothing is ever extracted or
    written (Section 9.6).
    """

    host_location = True

    def __init__(
        self,
        root: Union[str, os.PathLike],
        *,
        outside_links: bool = True,
        into_archive_links: bool = True,
        trust_root: Optional[str] = "",
        allowed_paths: Iterable[str] = (),
        load_outside_resources: bool = False,
    ) -> None:
        self.root = os.fspath(root)
        self.outside_links = outside_links
        self.into_archive_links = into_archive_links
        self.trust_root = trust_root
        """Trust boundary (Section 9.6), relative to ``root``. ``""`` trusts all of ``root``."""
        self.allowed_paths = tuple(allowed_paths)
        self.load_outside_resources = load_outside_resources
        self._cache: Dict[str, tuple] = {}

    def _abs(self, path: str) -> str:
        return os.path.join(self.root, *path.split("/")) if path else self.root

    def kind(self, path: str) -> Optional[Literal["file", "directory", "archive"]]:
        full = self._abs(path)
        if os.path.isdir(full):
            return "directory"
        if os.path.isfile(full):
            if is_mdz_name(path) and zipfile.is_zipfile(full):
                return "archive"
            return "file"
        return None

    def _archive(self, path: str):
        from .archive import MdzArchive  # local import: archive imports links

        full = self._abs(path)
        try:
            mtime = os.stat(full).st_mtime_ns
        except OSError:
            return None
        cached = self._cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            archive = MdzArchive.open(full)
        except Exception:
            archive = None
        self._cache[path] = (mtime, archive)
        return archive

    def find_entry(self, archive: str, entry: str) -> Optional[str]:
        a = self._archive(archive)
        return find_path(a.files, entry) if a else None

    def entry_point(self, archive: str) -> Optional[str]:
        a = self._archive(archive)
        if not a:
            return None
        try:
            return a.entry_point
        except Exception:
            return None
