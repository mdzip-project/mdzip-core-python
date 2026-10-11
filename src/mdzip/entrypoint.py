"""Entry point discovery (Section 5.5)."""

from __future__ import annotations

from typing import Iterable, Optional

from .errors import ERR_ENTRYPOINT_INVALID, ERR_ENTRYPOINT_MISSING, ERR_ENTRYPOINT_UNRESOLVED, MdzError
from .manifest import Manifest
from .paths import entry_point_error, find_path, is_markdown, is_support_file, normalize_entry_point


def resolve_entry_point(paths: Iterable[str], manifest: Optional[Manifest]) -> str:
    """The archive's primary Markdown file, per the ordered algorithm in Section 5.5.

    1. A manifest ``entryPoint``: used if it's a valid entry point path that
       matches an entry. Otherwise ERR_ENTRYPOINT_INVALID or
       ERR_ENTRYPOINT_MISSING is raised — never a silent fallback.
    2. ``index.md`` at the root.
    3. The single root-level Markdown file, setting aside support files; or,
       if every root-level Markdown file is a support file and there's
       exactly one, that file.
    4. Otherwise ERR_ENTRYPOINT_UNRESOLVED.

    ``paths`` are the archive's file entries (not directories). Returns the
    path as stored in the archive.
    """
    files = [p for p in paths if not p.endswith("/")]

    if manifest is not None and "entryPoint" in manifest:
        value = manifest["entryPoint"]
        problem = entry_point_error(value)
        if problem:
            raise MdzError(ERR_ENTRYPOINT_INVALID, problem)
        found = find_path(files, normalize_entry_point(value))
        if found is None:
            raise MdzError(ERR_ENTRYPOINT_MISSING, f"entryPoint '{value}' is not in the archive")
        return found

    index = find_path(files, "index.md")
    if index is not None:
        return index

    root_markdown = [p for p in files if "/" not in p and is_markdown(p)]
    content = [p for p in root_markdown if not is_support_file(p)]
    if len(content) == 1:
        return content[0]
    if not content and len(root_markdown) == 1:
        return root_markdown[0]

    if not root_markdown:
        raise MdzError(ERR_ENTRYPOINT_UNRESOLVED, "no Markdown file at the archive root")
    raise MdzError(
        ERR_ENTRYPOINT_UNRESOLVED,
        "several root Markdown files and no index.md or manifest entryPoint: " + ", ".join(sorted(root_markdown)),
    )


def try_resolve_entry_point(paths: Iterable[str], manifest: Optional[Manifest]) -> Optional[str]:
    """Like resolve_entry_point, but returns None instead of raising."""
    try:
        return resolve_entry_point(paths, manifest)
    except MdzError:
        return None
