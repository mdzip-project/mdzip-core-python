# mdzip-core (Python)

Read and write **MDZip** (`.mdz`) archives in Python: one or more Markdown documents and their images and other assets, packaged in a single ZIP file.

This library implements the [MDZip specification](https://github.com/mdzip-project/mdzip-spec) **1.2.0**. It's a standalone implementation (not a binding to the JavaScript or C# cores) that uses only the Python standard library, and it's tested against the specification's shared fixtures.

- Python 3.10+
- No dependencies
- Install `mdzip-core`, import `mdzip_core` (not `mdzip`: an unrelated PyPI project named [`mdzip`](https://pypi.org/project/mdzip/), for molecular-dynamics data, installs a module with that name)

## Reading

```python
import mdzip_core

with mdzip_core.open("guide.mdz") as archive:
    print(archive.title, archive.mode)          # "My Guide", "document"
    entry = archive.entry_point                 # e.g. "index.md" (Section 5.5)
    text = archive.read_text(entry)
    logo = archive.read_bytes("images/logo.png")
    for doc in archive.documents:               # every Markdown file
        ...
    for finding in archive.validate():          # conformance notes, never raises
        print(finding.level, finding.code, finding.message)
```

`mdzip_core.open` accepts a path, `bytes`, or a binary file object. The archive is read into memory; nothing is extracted to disk.

Opening an archive is safe for untrusted input:

- It rejects invalid or encrypted ZIPs, entry paths that are absolute, contain `..` or reserved characters, and entries that duplicate each other after Unicode NFC normalization.
- It rejects a `spec.version` from a newer major version, and unrecognized `mode` values.
- `mdzip_core.Limits` caps entry count, total and per-entry size, and compression ratio. Sizes are checked against the ZIP directory, then again while reading, since declared sizes can lie.
- Symbolic links are never followed or read. They're listed in `archive.special_files` (not in `files`), with a warning.

Path lookups follow the spec's matching rule: case-sensitive, after NFC normalization. So `café.md` stored either way is found, and `Index.md` doesn't match `index.md`.

The manifest is a plain `dict` (`archive.manifest`), so fields this library doesn't know are kept.

## Writing

```python
data = mdzip_core.create(
    {"index.md": "# My Guide\n\nSee ![logo](images/logo.png).\n", "images/logo.png": png_bytes},
    title="My Guide",                          # defaults to the entry point's first heading
    application={"name": "my-tool", "version": "1.0"},
)
open("guide.mdz", "wb").write(data)

data = mdzip_core.create_from_directory("my-guide/")   # Markdown + images by default; see DEFAULT_FILTERS
```

Every archive gets a `manifest.json` with `spec.version` (`1.2.0`), `entryPoint`, `title` and `mode`, as the spec requires of producers. Paths are written in NFC with UTF-8 names. Markdown files get LF line endings; other files are written exactly as given, since some formats (Windows batch files, RFC 4180 CSV) depend on CRLF. Pass `normalize_line_endings=False` to keep CRLF in Markdown too.

### Updating

```python
data = mdzip_core.update(
    "guide.mdz",
    write={"images/new.png": png_bytes, "index.md": new_text},
    remove=["old.md"],
    manifest={"title": "Second edition"},          # None as a value deletes a field
)
```

`update` follows the spec's rules for modifying archives:

- Entries you don't touch are kept byte for byte, with their timestamps and compression method.
- Unknown manifest fields are kept, at every level.
- Symbolic link entries are kept unchanged too, so the archive stays non-conforming and the warning from opening it still applies. Pass `drop_special_files=True` or name them in `remove=` once the user agrees to remove them.
- A manifest is added if the archive has none.
- The entry point must resolve to the same file afterwards. Pass `entry_point=` to change it on purpose; otherwise the update fails with `ERR_ENTRYPOINT_CHANGED`.

## Links

```python
target = archive.resolve_link("chapters/intro.md", "../../Notes.md")
target.kind   # "internal", "host", "archive", "url" or "unresolved"
```

Links resolve per spec Sections 9.3–9.5:

- links inside the archive;
- relative links that climb out of it to files next to the `.mdz`;
- links into archives, like `Project.mdz/docs/design.md`.

They're never clamped at the archive root. Without a host, links can reach the archive's own folder when it was opened from a file, and are unresolved when it was opened from bytes. Pass `mdzip_core.FilesystemHost(root)` to resolve against a larger folder, or implement `LinkHost` for anything else.

The host's folder is the trust boundary (spec Section 9.6): targets outside it are `denied`, whether or not they exist. Narrow it with `FilesystemHost(root, trust_root="Notes")`, or list targets the user allowed with `allowed_paths=[...]`. Pass `use="embed"` when resolving a resource the document loads automatically, such as an image. Embeds that leave the archive are `denied` unless the host sets `load_outside_resources=True`, and even then only inside the boundary.

## Errors

Failures raise `mdzip_core.MdzError` with a stable `code`. Codes follow the spec's suggested categories (Section 16.5), for example `ERR_ENTRYPOINT_INVALID`, `ERR_ENTRYPOINT_MISSING`, `ERR_DUPLICATE_PATH` and `ERR_ZIP_ENCRYPTED`, plus a few specific to this library (`ERR_LIMIT_EXCEEDED`, `ERR_ENTRYPOINT_CHANGED`, `ERR_NOT_FOUND`).

## Development

```sh
PYTHONPATH=src python -m unittest discover -s tests -t .
```

`tests/fixtures/` is a copy of the spec's shared fixtures, made with the spec's sync tool and checked by `SPEC-FIXTURES.json`. Don't edit it by hand. To refresh it:

```sh
node ../mdzip-spec/tools/sync-fixtures.mjs tests/fixtures --ref v1.2.0
```

## License

Apache-2.0
