"""mdzip — read and write MDZip (.mdz) archives.

Implements the MDZip specification 1.2.0 (https://github.com/mdzip-project/mdzip-spec)
with only the Python standard library.

    import mdzip

    with mdzip.open("guide.mdz") as archive:
        print(archive.entry_point, archive.title)
        text = archive.read_text(archive.entry_point)

    data = mdzip.create({"index.md": "# Hello\\n"}, title="Hello")
    data = mdzip.update(data, write={"images/logo.png": png_bytes})
"""

from ._version import __version__
from .archive import DEFAULT_LIMITS, Diagnostic, Limits, MdzArchive, open
from .entrypoint import resolve_entry_point
from .errors import MdzError
from .filters import DEFAULT_FILTERS, matches_filters
from .links import ArchiveSource, FilesystemHost, HostSource, LinkHost, LinkTarget, resolve_link
from .manifest import SPEC_VERSION
from .paths import is_markdown, is_support_file, same_path, validate_path
from .writer import create, create_from_directory, update

__all__ = [
    "__version__",
    "SPEC_VERSION",
    "open",
    "create",
    "create_from_directory",
    "update",
    "MdzArchive",
    "MdzError",
    "Limits",
    "DEFAULT_LIMITS",
    "Diagnostic",
    "resolve_entry_point",
    "resolve_link",
    "ArchiveSource",
    "HostSource",
    "LinkHost",
    "LinkTarget",
    "FilesystemHost",
    "matches_filters",
    "DEFAULT_FILTERS",
    "validate_path",
    "same_path",
    "is_markdown",
    "is_support_file",
]
