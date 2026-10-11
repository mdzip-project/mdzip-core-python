"""Consumer behavior and safety (Sections 4, 5, 13, 14.2)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile

import mdzip
from mdzip.errors import MdzError


def raw_zip(entries, *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as z:
        for item in entries:
            if isinstance(item, zipfile.ZipInfo):
                z.writestr(item, b"x")
            else:
                name, data = item
                z.writestr(name, data)
    return buf.getvalue()


def code_of(fn):
    try:
        fn()
    except MdzError as exc:
        return exc.code
    return None


class OpeningTest(unittest.TestCase):
    def test_archive_without_manifest_is_valid(self):  # T-007
        archive = mdzip.open(raw_zip([("index.md", "# Hi\n")]))
        self.assertIsNone(archive.manifest)
        self.assertEqual(archive.mode, "document")
        self.assertEqual(archive.entry_point, "index.md")
        self.assertEqual([d.level for d in archive.validate()], ["info"])

    def test_not_a_zip(self):
        self.assertEqual(code_of(lambda: mdzip.open(b"this is not a zip")), "ERR_ZIP_INVALID")

    def test_encrypted_entry(self):
        data = bytearray(raw_zip([("index.md", "x")], compression=zipfile.ZIP_STORED))
        # Set the encryption bit in the local and central headers.
        for sig in (b"PK\x03\x04", b"PK\x01\x02"):
            at = data.find(sig)
            flag_at = at + (6 if sig == b"PK\x03\x04" else 8)
            data[flag_at] |= 0x01
        self.assertEqual(code_of(lambda: mdzip.open(bytes(data))), "ERR_ZIP_ENCRYPTED")

    def test_traversal_and_absolute_entry_names(self):
        self.assertEqual(code_of(lambda: mdzip.open(raw_zip([("../escape.md", "x")]))), "ERR_PATH_TRAVERSAL")
        self.assertEqual(code_of(lambda: mdzip.open(raw_zip([("/abs.md", "x")]))), "ERR_PATH_LEADING_SLASH")

    def test_nfc_duplicates(self):
        data = raw_zip([("café.md", "a"), ("café.md", "b")])
        self.assertEqual(code_of(lambda: mdzip.open(data)), "ERR_DUPLICATE_PATH")

    def test_lookup_is_nfc_and_case_sensitive(self):
        archive = mdzip.open(raw_zip([("café.md", "x"), ("Notes.md", "n")]))
        self.assertEqual(archive.find("café.md"), "café.md")
        self.assertIsNone(archive.find("notes.md"))

    def test_newer_major_version_is_rejected(self):
        data = raw_zip([("manifest.json", json.dumps({"spec": {"version": "2.0.0"}})), ("index.md", "x")])
        self.assertEqual(code_of(lambda: mdzip.open(data)), "ERR_VERSION_UNSUPPORTED")

    def test_unknown_mode_is_rejected(self):
        for mode in ("book", "Document", "PROJECT"):
            data = raw_zip([("manifest.json", json.dumps({"mode": mode})), ("index.md", "x")])
            with self.subTest(mode):
                self.assertEqual(code_of(lambda: mdzip.open(data)), "ERR_MODE_UNSUPPORTED")

    def test_malformed_manifest(self):
        data = raw_zip([("manifest.json", "{not json"), ("index.md", "x")])
        self.assertEqual(code_of(lambda: mdzip.open(data)), "ERR_MANIFEST_INVALID")

    def test_non_utf8_flagged_names_are_repaired(self):
        # Many tools write UTF-8 names without setting the UTF-8 flag; zipfile
        # then decodes them as CP437. Build such an archive by patching bytes:
        # an ASCII placeholder of the same length, replaced in both headers.
        data = raw_zip([("cafXX.md", "x")], compression=zipfile.ZIP_STORED)
        data = data.replace(b"cafXX.md", "café.md".encode("utf-8"))
        archive = mdzip.open(data)
        self.assertIn("café.md", archive.files)

    def test_symlinks_are_ignored(self):
        link = zipfile.ZipInfo("link.md")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        archive = mdzip.open(raw_zip([("index.md", "x"), link]))
        self.assertNotIn("link.md", archive.files)
        self.assertTrue(any("symbolic link" in w.message for w in archive.warnings))


class LimitsTest(unittest.TestCase):
    def test_entry_count(self):
        data = raw_zip([(f"{i}.md", "x") for i in range(5)])
        self.assertEqual(code_of(lambda: mdzip.open(data, limits=mdzip.Limits(max_entries=3))), "ERR_LIMIT_EXCEEDED")

    def test_compression_ratio(self):
        data = raw_zip([("index.md", "x"), ("bomb.txt", b"\0" * (8 * 1024 * 1024))])
        self.assertEqual(code_of(lambda: mdzip.open(data, limits=mdzip.Limits(max_compression_ratio=100))), "ERR_LIMIT_EXCEEDED")

    def test_entry_size(self):
        data = raw_zip([("index.md", "x" * 5000)])
        self.assertEqual(code_of(lambda: mdzip.open(data, limits=mdzip.Limits(max_entry_uncompressed=1000))), "ERR_LIMIT_EXCEEDED")

    def test_total_size(self):
        data = raw_zip([("a.md", "x" * 600), ("b.md", "y" * 600)])
        self.assertEqual(code_of(lambda: mdzip.open(data, limits=mdzip.Limits(max_total_uncompressed=1000))), "ERR_LIMIT_EXCEEDED")


class LinksOnDiskTest(unittest.TestCase):
    def test_links_resolve_next_to_the_archive_and_into_another(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "Notes.md"), "w") as f:
                f.write("# Notes\n")
            with open(os.path.join(root, "Other.mdz"), "wb") as f:
                f.write(mdzip.create({"index.md": "# O\n", "guide.md": "g"}))
            path = os.path.join(root, "Project.mdz")
            with open(path, "wb") as f:
                f.write(mdzip.create({"index.md": "# P\n", "chapters/intro.md": "i"}))
            archive = mdzip.open(path)
            self.assertEqual(archive.resolve_link("chapters/intro.md", "../../Notes.md").as_dict(), {"kind": "host", "path": "Notes.md"})
            self.assertEqual(
                archive.resolve_link("index.md", "../Other.mdz/guide.md#top").as_dict(),
                {"kind": "archive", "archive": "Other.mdz", "entry": "guide.md", "fragment": "top"},
            )
            self.assertEqual(archive.resolve_link("index.md", "chapters/intro.md").kind, "internal")
            # Above the archive's own folder: unresolved, never clamped.
            self.assertEqual(archive.resolve_link("index.md", "../../Notes.md").as_dict(), {"kind": "unresolved", "reason": "not-found"})

    def test_archive_from_bytes_has_no_host_location(self):
        archive = mdzip.open(mdzip.create({"index.md": "x"}))
        self.assertEqual(archive.resolve_link("index.md", "../Notes.md").reason, "no-host-location")


if __name__ == "__main__":
    unittest.main()
