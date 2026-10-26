# Changelog

## [0.1.0] - 2026-10-26

First public release. Implements MDZip spec 1.2.0 and passes the spec's shared
fixtures from [v1.2.1](https://github.com/mdzip-project/mdzip-spec/releases/tag/v1.2.1),
including the T-018 ZIP-bomb tests. Standard library only; Python 3.10+.

- Reading: `mdzip_core.open` (path, bytes or file object), entry point
  discovery, case-sensitive NFC lookups, `files`, `directories`,
  `special_files`, `find`, `find_directory`, `read_bytes`, `read_text`,
  `manifest`, `mode`, `validate()`.
- Writing: `create`, `create_from_directory`, and `update`, which keeps
  untouched entries byte for byte, unknown manifest fields, directory entries
  and symbolic links (unless asked to drop them).
- Links: `resolve_link` for links inside, out of and into archives, with a
  trust boundary (spec §9.3–9.6).
- Safety: rejects unsafe or duplicate paths, encrypted entries and unsupported
  compression; never follows symbolic links; `Limits` caps entry count, sizes
  and compression ratio, checked on open and again while reading. Corrupt entry
  data fails with `ERR_ZIP_INVALID`.
- Errors: `MdzError` with a stable `code`.
