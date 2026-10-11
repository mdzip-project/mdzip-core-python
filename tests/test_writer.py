"""Producer behavior (Section 14.1)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone

import mdzip
from mdzip.errors import MdzError

WHEN = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def raw_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


class CreateTest(unittest.TestCase):
    def test_always_writes_a_full_manifest(self):
        archive = mdzip.open(mdzip.create({"index.md": "# Guide\n"}, when=WHEN))
        m = archive.manifest
        self.assertEqual(m["spec"], {"name": "mdzip-spec", "version": "1.2.0"})
        self.assertEqual(m["entryPoint"], "index.md")
        self.assertEqual(m["title"], "Guide")
        self.assertEqual(m["mode"], "document")
        self.assertEqual(m["producer"]["core"]["name"], "mdzip-core-python")
        self.assertEqual(m["created"], {"when": "2026-10-10T12:00:00Z"})

    def test_entry_point_is_discovered_with_support_files_set_aside(self):
        data = mdzip.create({"notes.md": "# Notes\n", "AGENTS.md": "# Agents\n", "README.md": "# Readme\n"})
        self.assertEqual(mdzip.open(data).manifest["entryPoint"], "notes.md")

    def test_ambiguous_entry_point_needs_an_explicit_one(self):
        with self.assertRaises(MdzError) as ctx:
            mdzip.create({"a.md": "a", "b.md": "b"})
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_UNRESOLVED")
        data = mdzip.create({"a.md": "a", "b.md": "b"}, entry_point="b.md", mode="project")
        self.assertEqual(mdzip.open(data).entry_point, "b.md")

    def test_invalid_explicit_entry_point(self):
        with self.assertRaises(MdzError) as ctx:
            mdzip.create({"index.md": "x", "cover.png": b"x"}, entry_point="cover.png")
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_INVALID")

    def test_paths_are_written_in_nfc(self):
        data = mdzip.create({"café.md": "# Café\n"})
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertIn("café.md", z.namelist())
            self.assertTrue(z.getinfo("café.md").flag_bits & 0x800, "non-ASCII names carry the UTF-8 flag")

    def test_nfc_duplicates_are_rejected(self):
        with self.assertRaises(MdzError) as ctx:
            mdzip.create({"café.md": "a", "café.md": "b"})
        self.assertEqual(ctx.exception.code, "ERR_DUPLICATE_PATH")

    def test_invalid_paths_are_rejected(self):
        for bad in ("../x.md", "/x.md", "a\\b.md", "a:b.md"):
            with self.subTest(bad), self.assertRaises(MdzError):
                mdzip.create({"index.md": "x", bad: "y"})

    def test_text_gets_lf_line_endings(self):
        archive = mdzip.open(mdzip.create({"index.md": "a\r\nb\r\n"}))
        self.assertEqual(archive.read_bytes("index.md"), b"a\nb\n")

    def test_extra_manifest_fields_are_kept(self):
        data = mdzip.create({"index.md": "x"}, manifest={"x-tool": {"a": [1, None]}, "language": "fr"})
        m = mdzip.open(data).manifest
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
            archive = mdzip.open(mdzip.create_from_directory(d))
            self.assertEqual(sorted(archive.files), ["images/a.png", "index.md", "manifest.json"])
            self.assertEqual(archive.manifest["title"], "From folder")
            self.assertTrue(archive.manifest["x-keep"])
            self.assertEqual(archive.manifest["spec"]["version"], "1.2.0")


class UpdateTest(unittest.TestCase):
    def test_adds_a_manifest_to_an_archive_without_one(self):
        data = mdzip.update(raw_zip({"notes.md": "# N\n"}), write={"AGENTS.md": "# A\n"})
        m = mdzip.open(data).manifest
        self.assertEqual(m["entryPoint"], "notes.md")
        self.assertEqual(m["spec"]["version"], "1.2.0")

    def test_second_support_file_stays_resolvable(self):  # P-003
        data = mdzip.update(raw_zip({"README.md": "# R\n"}), write={"AGENTS.md": "# A\n"})
        self.assertEqual(mdzip.open(data).entry_point, "README.md")

    def test_untouched_entries_keep_bytes_and_metadata(self):
        source = mdzip.create({"index.md": "# I\n", "data.bin": bytes(range(256))}, when=WHEN)
        before = mdzip.open(source).info("data.bin")
        after_data = mdzip.update(source, write={"index.md": "# Changed\n"})
        after = mdzip.open(after_data)
        self.assertEqual(after.read_bytes("data.bin"), bytes(range(256)))
        self.assertEqual(after.info("data.bin").date_time, before.date_time)
        self.assertEqual(after.info("data.bin").compress_type, before.compress_type)

    def test_existing_spec_version_and_unknown_fields_are_kept(self):
        source = raw_zip({
            "manifest.json": json.dumps({"spec": {"version": "1.1.0", "x": 1}, "entryPoint": "index.md", "x-top": [1]}),
            "index.md": "# I\n",
        })
        m = mdzip.open(mdzip.update(source, write={"more.md": "m"})).manifest
        self.assertEqual(m["spec"], {"version": "1.1.0", "x": 1})
        self.assertEqual(m["x-top"], [1])
        self.assertIn("modified", m)

    def test_removing_the_entry_point_is_refused(self):
        source = mdzip.create({"index.md": "# I\n", "other.md": "o"})
        with self.assertRaises(MdzError) as ctx:
            mdzip.update(source, remove=["index.md"])
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_CHANGED")
        data = mdzip.update(source, remove=["index.md"], entry_point="other.md")
        self.assertEqual(mdzip.open(data).entry_point, "other.md")

    def test_manifest_as_a_file_is_refused(self):
        with self.assertRaises(MdzError):
            mdzip.update(mdzip.create({"index.md": "x"}), write={"manifest.json": "{}"})

    def test_none_deletes_a_manifest_field(self):
        source = mdzip.create({"index.md": "x"}, manifest={"x-gone": 1})
        self.assertNotIn("x-gone", mdzip.open(mdzip.update(source, manifest={"x-gone": None})).manifest)


if __name__ == "__main__":
    unittest.main()
