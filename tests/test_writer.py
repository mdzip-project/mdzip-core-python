"""Producer behavior (Section 14.1)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone

import mdzip_core
from mdzip_core.errors import MdzError

WHEN = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def raw_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class CreateTest(unittest.TestCase):
    def test_always_writes_a_full_manifest(self):
        archive = mdzip_core.open(mdzip_core.create({"index.md": "# Guide\n"}, when=WHEN))
        m = archive.manifest
        self.assertEqual(m["spec"], {"name": "mdzip-spec", "version": "1.2.0"})
        self.assertEqual(m["entryPoint"], "index.md")
        self.assertEqual(m["title"], "Guide")
        self.assertEqual(m["mode"], "document")
        self.assertEqual(m["producer"]["core"]["name"], "mdzip-core-python")
        self.assertEqual(m["created"], {"when": "2026-10-10T12:00:00Z"})

    def test_entry_point_is_discovered_with_support_files_set_aside(self):
        data = mdzip_core.create({"notes.md": "# Notes\n", "AGENTS.md": "# Agents\n", "README.md": "# Readme\n"})
        self.assertEqual(mdzip_core.open(data).manifest["entryPoint"], "notes.md")

    def test_ambiguous_entry_point_needs_an_explicit_one(self):
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.create({"a.md": "a", "b.md": "b"})
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_UNRESOLVED")
        data = mdzip_core.create({"a.md": "a", "b.md": "b"}, entry_point="b.md", mode="project")
        self.assertEqual(mdzip_core.open(data).entry_point, "b.md")

    def test_invalid_explicit_entry_point(self):
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.create({"index.md": "x", "cover.png": b"x"}, entry_point="cover.png")
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_INVALID")

    def test_paths_are_written_in_nfc(self):
        data = mdzip_core.create({"café.md": "# Café\n"})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("café.md", z.namelist())
            self.assertTrue(z.getinfo("café.md").flag_bits & 0x800, "non-ASCII names carry the UTF-8 flag")

    def test_nfc_duplicates_are_rejected(self):
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.create({"café.md": "a", "café.md": "b"})
        self.assertEqual(ctx.exception.code, "ERR_DUPLICATE_PATH")

    def test_invalid_paths_are_rejected(self):
        for bad in ("../x.md", "/x.md", "a\\b.md", "a:b.md"):
            with self.subTest(bad), self.assertRaises(MdzError):
                mdzip_core.create({"index.md": "x", bad: "y"})

    def test_zip_flags_and_methods(self):  # P-007
        data = mdzip_core.create({"index.md": "# I\n", "café.md": "c", "images/a.png": b"\x89PNG"}, entry_point="index.md")
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                self.assertIn(info.compress_type, (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))
                if not info.filename.isascii():
                    self.assertTrue(info.flag_bits & 0x800, info.filename)

    def test_file_and_directory_conflict_is_refused(self):
        with self.assertRaises(MdzError) as caught:
            mdzip_core.create({"index.md": "x", "docs": "a", "docs/intro.md": "b"})
        self.assertEqual(caught.exception.code, "ERR_PATH_CONFLICT")
        with self.assertRaises(MdzError) as caught:
            mdzip_core.create({"index.md": "x", "docs/": "a"})
        self.assertEqual(caught.exception.code, "ERR_PATH_INVALID")

    def test_markdown_gets_lf_line_endings(self):
        archive = mdzip_core.open(mdzip_core.create({"index.md": "a\r\nb\r\n", "notes.markdown": "c\r\n"}, entry_point="index.md"))
        self.assertEqual(archive.read_bytes("index.md"), b"a\nb\n")
        self.assertEqual(archive.read_bytes("notes.markdown"), b"c\n")

    def test_other_files_keep_their_line_endings(self):
        # Section 12.2: some formats depend on CRLF (Windows batch files, RFC 4180 CSV).
        files = {"index.md": "x", "data/table.csv": "a,b\r\n1,2\r\n", "run.bat": "@echo off\r\n", "notes.txt": "t\r\n"}
        archive = mdzip_core.open(mdzip_core.create(files))
        self.assertEqual(archive.read_bytes("data/table.csv"), b"a,b\r\n1,2\r\n")
        self.assertEqual(archive.read_bytes("run.bat"), b"@echo off\r\n")
        self.assertEqual(archive.read_bytes("notes.txt"), b"t\r\n")
        self.assertNotIn(b"\r\n", archive.read_bytes("manifest.json"))

    def test_line_endings_can_be_kept(self):
        archive = mdzip_core.open(mdzip_core.create({"index.md": "a\r\n"}, normalize_line_endings=False))
        self.assertEqual(archive.read_bytes("index.md"), b"a\r\n")

    def test_extra_manifest_fields_are_kept(self):
        data = mdzip_core.create({"index.md": "x"}, manifest={"x-tool": {"a": [1, None]}, "language": "fr"})
        m = mdzip_core.open(data).manifest
        self.assertEqual(m["x-tool"], {"a": [1, None]})
        self.assertEqual(m["language"], "fr")


class CreateFromDirectoryTest(unittest.TestCase):
    def test_filters_and_existing_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            for rel, data in {
                "index.md": b"# Home\n",
                "images/a.png": b"\x89PNG",
                "notes.txt": b"skip me",
                "manifest.json": json.dumps({"title": "From folder", "x-keep": True}).encode(),
            }.items():
                os.makedirs(os.path.dirname(os.path.join(d, rel)) or d, exist_ok=True)
                with open(os.path.join(d, rel), "wb") as f:
                    f.write(data)
            archive = mdzip_core.open(mdzip_core.create_from_directory(d))
            self.assertEqual(sorted(archive.files), ["images/a.png", "index.md", "manifest.json"])
            self.assertEqual(archive.manifest["title"], "From folder")
            self.assertTrue(archive.manifest["x-keep"])
            self.assertEqual(archive.manifest["spec"]["version"], "1.2.0")


class UpdateTest(unittest.TestCase):
    def test_adds_a_manifest_to_an_archive_without_one(self):
        data = mdzip_core.update(raw_zip({"notes.md": "# N\n"}), write={"AGENTS.md": "# A\n"})
        m = mdzip_core.open(data).manifest
        self.assertEqual(m["entryPoint"], "notes.md")
        self.assertEqual(m["spec"]["version"], "1.2.0")

    def test_second_support_file_stays_resolvable(self):  # P-003
        data = mdzip_core.update(raw_zip({"README.md": "# R\n"}), write={"AGENTS.md": "# A\n"})
        self.assertEqual(mdzip_core.open(data).entry_point, "README.md")

    def test_untouched_entries_keep_bytes_and_metadata(self):
        source = mdzip_core.create({"index.md": "# I\n", "data.bin": bytes(range(256))}, when=WHEN)
        before = mdzip_core.open(source).info("data.bin")
        after_data = mdzip_core.update(source, write={"index.md": "# Changed\n"})
        after = mdzip_core.open(after_data)
        self.assertEqual(after.read_bytes("data.bin"), bytes(range(256)))
        self.assertEqual(after.info("data.bin").date_time, before.date_time)
        self.assertEqual(after.info("data.bin").compress_type, before.compress_type)

    def test_untouched_crlf_markdown_is_not_normalized(self):  # Section 12.2 / 14.1
        source = raw_zip({"index.md": "# I\r\n", "other.md": "keep\r\n"})
        after = mdzip_core.open(mdzip_core.update(source, write={"index.md": "# Changed\r\n"}))
        self.assertEqual(after.read_bytes("index.md"), b"# Changed\n")
        self.assertEqual(after.read_bytes("other.md"), b"keep\r\n")

    def test_existing_spec_version_and_unknown_fields_are_kept(self):
        source = raw_zip({
            "manifest.json": json.dumps({"spec": {"version": "1.1.0", "x": 1}, "entryPoint": "index.md", "x-top": [1]}),
            "index.md": "# I\n",
        })
        m = mdzip_core.open(mdzip_core.update(source, write={"more.md": "m"})).manifest
        self.assertEqual(m["spec"], {"version": "1.1.0", "x": 1})
        self.assertEqual(m["x-top"], [1])
        self.assertIn("modified", m)

    def test_removing_the_entry_point_is_refused(self):
        source = mdzip_core.create({"index.md": "# I\n", "other.md": "o"})
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.update(source, remove=["index.md"])
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_CHANGED")
        data = mdzip_core.update(source, remove=["index.md"], entry_point="other.md")
        self.assertEqual(mdzip_core.open(data).entry_point, "other.md")

    def test_manifest_as_a_file_is_refused(self):
        with self.assertRaises(MdzError):
            mdzip_core.update(mdzip_core.create({"index.md": "x"}), write={"manifest.json": "{}"})

    def test_none_deletes_a_manifest_field(self):
        source = mdzip_core.create({"index.md": "x"}, manifest={"x-gone": 1})
        self.assertNotIn("x-gone", mdzip_core.open(mdzip_core.update(source, manifest={"x-gone": None})).manifest)


def with_symlink(name: str = "link.md", target: bytes = b"index.md") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("index.md", "# I\n")
        link = zipfile.ZipInfo(name)
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        z.writestr(link, target)
    return buf.getvalue()


class SymlinkUpdateTest(unittest.TestCase):  # Sections 4, 13.1, 14.1
    def test_listed_but_not_a_file(self):
        archive = mdzip_core.open(with_symlink())
        self.assertEqual(archive.special_files, ["link.md"])
        self.assertNotIn("link.md", archive.files)
        self.assertNotIn("link.md", archive)

    def test_update_keeps_the_link_unchanged(self):
        data = mdzip_core.update(with_symlink(), write={"AGENTS.md": "# A\n"})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("link.md")
            self.assertEqual(info.external_attr >> 16, 0o120777)
            self.assertEqual(info.create_system, 3)
            self.assertEqual(z.read(info), b"index.md")
        self.assertEqual(mdzip_core.open(data).special_files, ["link.md"])

    def test_dropped_only_when_asked(self):
        for kwargs in ({"drop_special_files": True}, {"remove": ["link.md"]}):
            with self.subTest(kwargs):
                data = mdzip_core.update(with_symlink(), write={"AGENTS.md": "# A\n"}, **kwargs)
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    self.assertNotIn("link.md", z.namelist())

    def test_writing_the_path_replaces_the_link_with_a_file(self):
        data = mdzip_core.update(with_symlink(), write={"link.md": "# Real\n"})
        archive = mdzip_core.open(data)
        self.assertEqual(archive.special_files, [])
        self.assertEqual(archive.read_text("link.md"), "# Real\n")

    def test_a_kept_link_does_not_change_the_entry_point(self):
        # Before: index.md is the only root document. A link named other.md must not make it ambiguous.
        data = mdzip_core.update(with_symlink("other.md"), write={"AGENTS.md": "# A\n"})
        self.assertEqual(mdzip_core.open(data).entry_point, "index.md")


class DirectoryEntryTest(unittest.TestCase):  # Sections 5.4, 14.1
    SOURCE = raw_zip({
        "index.md": "# Guide\n",
        "empty/": b"",
        "docs/": b"",
        "docs/a.md": "# A\n",
        "data/blob.bin": bytes(range(256)),
    })

    def test_listed_separately_from_files(self):
        archive = mdzip_core.open(self.SOURCE)
        self.assertEqual(sorted(archive.directories), ["docs/", "empty/"])
        self.assertNotIn("empty/", archive.files)
        self.assertEqual(archive.find_directory("empty"), "empty/")
        self.assertEqual(archive.find_directory("docs/"), "docs/")
        self.assertIsNone(archive.find_directory("data"))  # implied by a file path, not stored
        self.assertIsNone(archive.find_directory("Empty"))  # case-sensitive

    def test_update_keeps_directories_and_binary_entries(self):
        before = mdzip_core.open(self.SOURCE)
        data = self.SOURCE
        for _ in range(2):  # two saves, since one can hide a second that drops data
            data = mdzip_core.update(data, write={"index.md": "# Guide\n\nEdited.\n"}, when=WHEN)
        after = mdzip_core.open(data)
        self.assertEqual(sorted(after.directories), ["docs/", "empty/"])
        self.assertEqual(after.read_bytes("data/blob.bin"), before.read_bytes("data/blob.bin"))
        self.assertEqual(after.read_text("docs/a.md"), "# A\n")

    def test_remove_drops_a_directory(self):
        after = mdzip_core.open(mdzip_core.update(self.SOURCE, remove=["empty"], when=WHEN))
        self.assertEqual(after.directories, ["docs/"])


if __name__ == "__main__":
    unittest.main()
