"""Writing .mdz archives as a conforming producer (Section 14.1)."""

from __future__ import annotations

import copy
import io
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from ._version import __version__
from .archive import DEFAULT_LIMITS, MANIFEST_NAME, Limits, MdzArchive, Source
from .entrypoint import resolve_entry_point, try_resolve_entry_point
from .errors import ERR_DUPLICATE_PATH, ERR_PATH_INVALID, ERR_ENTRYPOINT_CHANGED, ERR_ENTRYPOINT_INVALID, ERR_ENTRYPOINT_MISSING, MdzError
from .filters import DEFAULT_FILTERS, matches_filters
from .manifest import MODES, SPEC_NAME, SPEC_VERSION, Manifest, parse_manifest, serialize_manifest, set_modified
from .paths import entry_point_error, entry_set_error, find_path, is_markdown, nfc, normalize_entry_point, validate_path

Content = Union[bytes, str]

PRODUCER_CORE = {
    "name": "mdzip-core-python",
    "version": __version__,
    "url": "https://github.com/mdzip-project/mdzip-core-python",
}

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _gets_lf(path: str) -> bool:
    """Markdown files and the manifest get LF line endings; other files keep theirs (Section 12.2)."""
    return is_markdown(path) or path.lower() == MANIFEST_NAME


def _to_bytes(path: str, content: Content, normalize_line_endings: bool) -> bytes:
    """Content as bytes, with LF line endings in Markdown files when asked."""
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
    if normalize_line_endings and _gets_lf(path):
        data = data.replace(b"\r\n", b"\n")
    return data


def _first_heading(markdown: bytes) -> Optional[str]:
    for line in markdown.decode("utf-8", errors="replace").splitlines():
        m = re.match(r"^#\s+(.+?)\s*#*\s*$", line)
        if m:
            return m.group(1)
    return None


def _zip_info(path: str, when: Tuple[int, int, int, int, int, int], compress: bool) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=when)
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.create_system = 3  # Unix
    info.external_attr = 0o100644 << 16  # regular file, rw-r--r--
    info.flag_bits |= 0x800  # UTF-8 names (Section 4)
    return info


def _write_zip(entries: Sequence[Tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    """Write entries as a ZIP. zipfile sets the UTF-8 flag for non-ASCII names and uses
    ZIP64 only when a field would overflow (Section 4)."""
    problem = entry_set_error(info.filename for info, _ in entries)
    if problem:
        raise problem
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info, data in entries:
            z.writestr(info, data, compress_type=info.compress_type)
    return buf.getvalue()


def _normalized_files(files: Mapping[str, Content], normalize_line_endings: bool) -> Dict[str, bytes]:
    """NFC paths, validated (Section 5.4); no two paths equal after NFC."""
    out: Dict[str, bytes] = {}
    for raw, content in files.items():
        path = nfc(raw)  # archive paths, never OS paths: a backslash is invalid (Section 5.4)
        validate_path(path)
        if path.endswith("/"):
            raise MdzError(ERR_PATH_INVALID, f"'{raw}' ends in '/'; a file path can't name a directory")
        if path in out:
            raise MdzError(ERR_DUPLICATE_PATH, f"'{raw}' duplicates another path after NFC normalization")
        out[path] = _to_bytes(path, content, normalize_line_endings)
    return out


def create(
    files: Mapping[str, Content],
    *,
    title: Optional[str] = None,
    mode: Optional[str] = None,
    entry_point: Optional[str] = None,
    manifest: Optional[Manifest] = None,
    application: Optional[Mapping[str, str]] = None,
    when: Optional[datetime] = None,
    normalize_line_endings: bool = True,
    compress: bool = True,
) -> bytes:
    """Build a new .mdz archive and return its bytes.

    ``files`` maps archive paths to content (``str`` is written as UTF-8).
    The archive always gets a ``manifest.json`` (Section 14.1) with
    ``spec.version``, ``entryPoint``, ``title`` and ``mode``; ``manifest``
    supplies further fields (or overrides), and is kept as given otherwise.

    The entry point is ``entry_point`` if given, else discovered from the
    files (Section 5.5). The title defaults to the entry point's first
    heading, then its file name.

    With ``normalize_line_endings`` (the default), CRLF in Markdown files is
    written as LF; other files are written exactly as given (Section 12.2).
    """
    contents = _normalized_files(files, normalize_line_endings)
    given = contents.pop(MANIFEST_NAME, None)
    if manifest is None and given is not None:
        manifest = parse_manifest(given)  # a manifest.json among the files is the starting point
    base: Manifest = copy.deepcopy(dict(manifest)) if manifest else {}
    mode = mode or base.get("mode") or "document"
    if mode not in MODES:
        raise MdzError("ERR_MODE_UNSUPPORTED", f"mode must be one of {MODES}")

    ep = _choose_entry_point(contents, entry_point or base.get("entryPoint"))
    stamp = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    iso = stamp.isoformat().replace("+00:00", "Z")

    out: Manifest = {}
    out["spec"] = {**(base.get("spec") if isinstance(base.get("spec"), dict) else {}), "name": SPEC_NAME, "version": SPEC_VERSION}
    producer = dict(base.get("producer") or {}) if isinstance(base.get("producer"), dict) else {}
    producer["core"] = dict(PRODUCER_CORE)
    if application:
        producer["application"] = dict(application)
    out["producer"] = producer
    out["title"] = title or base.get("title") or _first_heading(contents[ep]) or os.path.splitext(ep.split("/")[-1])[0]
    out["mode"] = mode
    out["entryPoint"] = ep
    for key, value in base.items():
        if key not in out:
            out[key] = value
    out.setdefault("created", {"when": iso})
    set_modified(out, iso)

    date = stamp.timetuple()[:6]
    entries = [(_zip_info(MANIFEST_NAME, date, compress), serialize_manifest(out))]
    entries += [(_zip_info(p, date, compress), data) for p, data in contents.items()]
    return _write_zip(entries)


def _choose_entry_point(contents: Mapping[str, bytes], requested: Optional[str]) -> str:
    if requested is not None:
        problem = entry_point_error(requested)
        if problem:
            raise MdzError(ERR_ENTRYPOINT_INVALID, problem)
        found = find_path(contents, normalize_entry_point(requested))
        if found is None:
            raise MdzError(ERR_ENTRYPOINT_MISSING, f"entry point '{requested}' is not among the files")
        return found
    return resolve_entry_point(contents, None)


def create_from_directory(
    directory: Union[str, os.PathLike],
    *,
    filters: Optional[Iterable[str]] = DEFAULT_FILTERS,
    **options: Any,
) -> bytes:
    """Pack a folder into an .mdz (paths relative to ``directory``).

    Only files matching ``filters`` are included (``None`` includes
    everything); a ``manifest.json`` in the folder is used as the starting
    manifest, so its fields are kept. Other options are passed to ``create``.
    """
    root = os.fspath(directory)
    files: Dict[str, bytes] = {}
    existing: Optional[Manifest] = None
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as f:
                data = f.read()
            if rel == MANIFEST_NAME:
                existing = parse_manifest(data)
                continue
            if filters is None or matches_filters(rel, filters):
                files[rel] = data
    if existing is not None and "manifest" not in options:
        options["manifest"] = existing
    return create(files, **options)


def update(
    source: Source,
    *,
    write: Optional[Mapping[str, Content]] = None,
    remove: Iterable[str] = (),
    manifest: Optional[Mapping[str, Any]] = None,
    entry_point: Optional[str] = None,
    when: Optional[datetime] = None,
    normalize_line_endings: bool = True,
    drop_special_files: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> bytes:
    """Modify an archive and return the new bytes (Section 14.1).

    - Entries not in ``write`` or ``remove`` are kept byte-for-byte, with
      their original timestamps and compression method.
    - Symbolic link entries (``MdzArchive.special_files``) are kept the same
      way, so the archive stays non-conforming (Section 4) and the warning
      from opening it still applies. Pass ``drop_special_files=True``, or
      name them in ``remove``, when the user agrees to remove them; writing
      a file at the same path replaces the link with that file.
    - Markdown files in ``write`` get LF line endings when
      ``normalize_line_endings`` is set; other files are written as given
      (Section 12.2).
    - The manifest is kept as a dict, so unknown fields survive; ``manifest``
      sets or replaces top-level fields (``None`` values delete them).
    - A manifest is added if the archive has none, with ``spec.version`` and
      the entry point the archive resolved to before the update.
    - The entry point must still resolve to the same file afterwards, unless
      ``entry_point`` names the new one; otherwise ERR_ENTRYPOINT_CHANGED.
    """
    archive = source if isinstance(source, MdzArchive) else MdzArchive.open(source, limits=limits)
    before = try_resolve_entry_point(archive.files, archive.manifest)

    writes = _normalized_files(write or {}, normalize_line_endings)
    if MANIFEST_NAME in writes:
        raise MdzError("ERR_MANIFEST_INVALID", "pass manifest changes with manifest=, not as a file in write=")
    removals = set()
    for path in remove:
        stored = archive.find(path)
        if stored is None:
            special = archive._special_entry(path)
            stored = special[0] if special else None
        if stored is None:
            raise MdzError("ERR_NOT_FOUND", f"'{path}' is not in the archive")
        removals.add(nfc(stored))

    # Kept entries, in archive order, then new ones.
    entries = []
    replaced = set()
    for stored in archive.files:
        key = nfc(stored)
        if stored.lower() == MANIFEST_NAME or key in removals:
            continue
        info = archive.info(stored)
        if key in writes:
            entries.append((key, None, writes[key]))
            replaced.add(key)
        else:
            entries.append((stored, info, archive.read_bytes(stored)))
    kept_special = set()
    if not drop_special_files:
        for stored in archive.special_files:
            key = nfc(stored)
            if key not in removals and key not in writes:
                entries.append(archive._special_entry(stored))
                kept_special.add(stored)
    for path, data in writes.items():
        if path not in replaced:
            entries.append((path, None, data))

    stamp = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    iso = stamp.isoformat().replace("+00:00", "Z")
    new_manifest: Manifest = copy.deepcopy(archive.manifest) if archive.manifest is not None else {}
    if archive.manifest is None:
        new_manifest["spec"] = {"name": SPEC_NAME, "version": SPEC_VERSION}
        new_manifest["producer"] = {"core": dict(PRODUCER_CORE)}
        if before is not None:
            new_manifest["entryPoint"] = before
    for key, value in (manifest or {}).items():
        if value is None:
            new_manifest.pop(key, None)
        else:
            new_manifest[key] = copy.deepcopy(value)
    if entry_point is not None:
        new_manifest["entryPoint"] = entry_point
    set_modified(new_manifest, iso)

    paths = [p for p, _, _ in entries if p not in kept_special]  # links are never documents
    try:
        after = resolve_entry_point(paths, new_manifest)
    except MdzError as exc:
        raise MdzError(ERR_ENTRYPOINT_CHANGED, f"the update would leave the entry point unresolvable ({exc.message})") from exc
    if entry_point is None and before is not None and nfc(after) != nfc(before):
        raise MdzError(ERR_ENTRYPOINT_CHANGED, f"the update would change the entry point from '{before}' to '{after}'")

    date = stamp.timetuple()[:6]
    out = [(_zip_info(MANIFEST_NAME, date, True), serialize_manifest(new_manifest))]
    for path, info, data in entries:
        if info is not None:
            kept = zipfile.ZipInfo(path, date_time=info.date_time)  # stored name, as UTF-8
            kept.compress_type = info.compress_type
            kept.create_system = info.create_system
            kept.external_attr = info.external_attr
            kept.flag_bits = 0x800
            out.append((kept, data))
        else:
            out.append((_zip_info(path, date, True), data))
    return _write_zip(out)
