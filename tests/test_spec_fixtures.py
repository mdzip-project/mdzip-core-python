"""The MDZip spec's shared fixtures (tests/fixtures, synced with mdzip-spec tools/sync-fixtures.mjs)."""

from __future__ import annotations

import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path

import mdzip_core
from mdzip_core.errors import MdzError
from mdzip_core.links import ArchiveSource, HostSource
from mdzip_core.manifest import parse_manifest_with_duplicates, validate_manifest
from mdzip_core.paths import entry_set_error, find_path, path_error, same_path

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class MarkerTest(unittest.TestCase):
    def test_fixtures_match_marker(self):
        marker = load("SPEC-FIXTURES.json")
        for name, expected in marker["files"].items():
            actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"{name} differs from spec {marker['ref']}; re-run sync-fixtures.mjs")


class PrebuiltArchivesTest(unittest.TestCase):
    def test_minimal(self):
        self.assertEqual(mdzip_core.open(FIXTURES / "minimal.mdz").entry_point, "index.md")

    def test_with_manifest(self):
        archive = mdzip_core.open(FIXTURES / "with-manifest.mdz")
        self.assertEqual(archive.entry_point, archive.manifest["entryPoint"])

    def test_missing_entry_point_target(self):  # T-003
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.open(FIXTURES / "invalid-missing-index.mdz").entry_point
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_MISSING")

    def test_bad_entry_point(self):  # T-004
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.open(FIXTURES / "invalid-bad-entrypoint.mdz").entry_point
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_MISSING")

    def test_crlf(self):  # T-005
        archive = mdzip_core.open(FIXTURES / "line-endings-crlf.mdz")
        self.assertIn("\r\n", archive.read_text(archive.entry_point))


class EntryPointCasesTest(unittest.TestCase):
    def test_cases(self):
        for case in load("entrypoint-cases.json"):
            with self.subTest(case["name"]):
                manifest = json.loads(case["manifestJson"]) if "manifestJson" in case else case["manifest"]
                files = [f for f in case["files"] if f != "manifest.json"]
                expected = case["expectedEntryPoint"]
                if expected is None:
                    with self.assertRaises(MdzError) as ctx:
                        mdzip_core.resolve_entry_point(files, manifest)
                    if "expectedError" in case:
                        self.assertEqual(ctx.exception.code, case["expectedError"])
                else:
                    self.assertEqual(mdzip_core.resolve_entry_point(files, manifest), expected)


class PathCasesTest(unittest.TestCase):
    def test_validation(self):
        for case in load("path-validation-cases.json"):
            with self.subTest(case["name"]):
                error = path_error(case["path"])
                if case["valid"]:
                    self.assertIsNone(error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error.code, case["errorCode"])

    def test_validation_on_open(self):
        # The same cases through open(), with each name stored exactly as given.
        # zipfile rewrites "\" to "/" on Windows when a ZipInfo is built, so the
        # name is set afterwards; the archive must check the stored name.
        for case in load("path-validation-cases.json"):
            with self.subTest(case["name"]):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    if case["path"] != "index.md":
                        z.writestr("index.md", "# Index\n")
                    info = zipfile.ZipInfo("placeholder")
                    info.filename = case["path"]
                    z.writestr(info, "content\n")
                if case["valid"]:
                    mdzip_core.open(buf.getvalue())
                else:
                    with self.assertRaises(MdzError) as raised:
                        mdzip_core.open(buf.getvalue())
                    self.assertEqual(raised.exception.code, case["errorCode"])

    def test_matching(self):
        for case in load("path-matching-cases.json"):
            with self.subTest(case["name"]):
                self.assertEqual(same_path(case["entry"], case["lookup"]), case["matches"])

    def test_create_filters(self):
        for case in load("create-filter-cases.json"):
            with self.subTest(case["name"]):
                included = [p for p in case["inputPaths"] if mdzip_core.matches_filters(p, case["filters"])]
                excluded = [p for p in case["inputPaths"] if p not in included]
                self.assertEqual(sorted(included), sorted(case["expectedIncluded"]))
                self.assertEqual(sorted(excluded), sorted(case["expectedExcluded"]))


class EntrySetCasesTest(unittest.TestCase):  # T-013
    def test_cases(self):
        for case in load("entry-set-cases.json")["cases"]:
            with self.subTest(case["name"]):
                error = entry_set_error(case["entries"])
                if case["valid"]:
                    self.assertIsNone(error)
                else:
                    self.assertIsNotNone(error)
                    self.assertEqual(error.code, case["errorCode"])

    def test_archives_with_these_entries(self):
        """The reader applies the same checks to real ZIP files."""
        for case in load("entry-set-cases.json")["cases"]:
            with self.subTest(case["name"]):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    for name in case["entries"]:
                        z.writestr(name, b"" if name.endswith("/") else b"# x\n")
                    z.writestr("index.md" if "index.md" not in case["entries"] else "other.md", b"# i\n")
                if case["valid"]:
                    mdzip_core.open(buf.getvalue())
                else:
                    with self.assertRaises(MdzError) as caught:
                        mdzip_core.open(buf.getvalue())
                    self.assertEqual(caught.exception.code, case["errorCode"])


class ManifestCasesTest(unittest.TestCase):  # T-012
    def test_cases(self):
        for case in load("manifest-cases.json")["cases"]:
            expected = case["expected"]
            with self.subTest(case["name"]):
                data = case["manifestJson"].encode("utf-8")
                if not expected["valid"]:
                    with self.assertRaises(MdzError) as caught:
                        validate_manifest(parse_manifest_with_duplicates(data)[0])
                    self.assertEqual(caught.exception.code, expected["errorCode"])
                    continue
                manifest, duplicates = parse_manifest_with_duplicates(data)
                ignored = validate_manifest(manifest)
                self.assertEqual(sorted(ignored), sorted(expected.get("ignoredFields", [])))
                self.assertEqual(bool(duplicates), expected.get("duplicateMembers", False))
                for pointer, value in expected.get("values", {}).items():
                    current = manifest
                    for key in pointer.split("/")[1:]:
                        current = current[key]
                    self.assertEqual(current, value)


class FixtureHost:
    """LinkHost over the link fixture's host model."""

    def __init__(self, model, context=None):
        self.files = model["files"]
        self.directories = model["directories"]
        self.archives = model["archives"]
        context = context or {}
        self.host_location = context.get("hostLocation", True)
        self.outside_links = context.get("outsideLinks", True)
        self.into_archive_links = context.get("intoArchiveLinks", True)
        self.trust_root = context.get("trustRoot", model.get("trustRoot", ""))
        self.allowed_paths = context.get("allowedPaths", [])
        self.load_outside_resources = context.get("loadOutsideResources", False)

    def kind(self, path):
        if path in self.archives:
            return "archive"
        if path in self.files:
            return "file"
        if path in self.directories:
            return "directory"
        return None

    def find_entry(self, archive, entry):
        a = self.archives.get(archive)
        return find_path(a["entries"], entry) if a else None

    def entry_point(self, archive):
        a = self.archives.get(archive)
        return a["entryPoint"] if a else None


class LinkCasesTest(unittest.TestCase):
    def test_cases(self):
        fixture = load("link-resolution-cases.json")
        for case in fixture["cases"]:
            with self.subTest(case["name"]):
                src = case["from"]
                source = ArchiveSource(src["archive"], src["path"]) if "archive" in src else HostSource(src["host"])
                host = FixtureHost(fixture["host"], case.get("context"))
                target = mdzip_core.resolve_link(source, case["link"], host, case.get("use", "navigate"))
                self.assertEqual(target.as_dict(), case["expected"])


class RoundTripTest(unittest.TestCase):
    """P-004 / P-005: unknown manifest fields and untouched entries survive every flow, twice."""

    def setUp(self):
        self.exp = load("round-trip-expectations.json")
        self.original = (FIXTURES / "round-trip.mdz").read_bytes()

    def flow(self, name, data, round_no):
        if name == "open and save without an edit":
            return mdzip_core.update(data)
        if name == "change a recognized manifest field":
            return mdzip_core.update(data, manifest={"title": "Edited title"})
        if name == "edit document content":
            archive = mdzip_core.open(data)
            return mdzip_core.update(data, write={"index.md": archive.read_text("index.md") + "\nEdited.\n"})
        if name == "add a resource":
            return mdzip_core.update(data, write={f"images/added-{round_no}.png": b"\x89PNG"})
        if name == "remove a recognized resource":
            if round_no == 1:
                # Remove the file and its link, as a tool would (the flow lists index.md in changes).
                archive = mdzip_core.open(data)
                index = archive.read_text("index.md").replace("See [the guide](docs/guide.md).", "")
                return mdzip_core.update(data, write={"index.md": index}, remove=["docs/guide.md"])
            return mdzip_core.update(data, manifest={"title": "Second save"})
        raise AssertionError(f"unknown flow {name}")

    def check(self, data, flow):
        archive = mdzip_core.open(data)
        for item in self.exp["preservedManifestValues"]:
            value = archive.manifest
            for key in item["pointer"].split("/")[1:]:
                value = value[key.replace("~1", "/").replace("~0", "~")]
            self.assertEqual(value, item["value"], f"{flow['name']}: {item['pointer']}")
        for path in flow.get("removes", []):
            self.assertNotIn(path, archive, f"{flow['name']}: {path} should be removed")
        for entry in self.exp["preservedEntries"]:
            if entry["path"] in flow.get("changes", []) or entry["path"] in flow.get("removes", []):
                continue
            data_bytes = archive.read_bytes(entry["path"])
            self.assertEqual(len(data_bytes), entry["bytes"], f"{flow['name']}: {entry['path']}")
            self.assertEqual(hashlib.sha256(data_bytes).hexdigest(), entry["sha256"], f"{flow['name']}: {entry['path']}")

    def test_flows(self):
        for flow in self.exp["flows"]:
            with self.subTest(flow["name"]):
                data = self.original
                for round_no in (1, 2):
                    data = self.flow(flow["name"], data, round_no)
                    self.check(data, flow)


class ArchiveCasesTest(unittest.TestCase):  # T-014, T-015, T-017, P-008
    def setUp(self):
        self.cases = {case["id"]: case for case in load("archive-cases.json")["cases"]}

    def test_symlink_entries(self):  # T-017
        case = self.cases["symlink-entries"]
        archive = mdzip_core.open(FIXTURES / case["archive"])
        self.assertEqual(archive.entry_point, case["expectedEntryPoint"])
        self.assertEqual(sorted(archive.special_files), sorted(case["symlinks"]))
        for link in case["links"]:
            with self.subTest(link["href"]):
                target = archive.resolve_link(archive.entry_point, link["href"])
                if link["expected"] == "resolved":
                    self.assertEqual((target.kind, target.entry), ("internal", link["target"]))
                else:
                    # This core skips symbolic links, so "not-followed" is unresolved.
                    self.assertEqual(target.kind, "unresolved")
        reported = " ".join(d.message for d in archive.validate())
        for name in case["symlinks"]:
            self.assertIn(f"'{name}'", reported)

    def test_modify_archive_with_symlinks(self):  # P-008
        source = FIXTURES / self.cases["symlink-entries"]["archive"]
        kept = mdzip_core.open(mdzip_core.update(source, write={"notes.md": "# Notes\n\nEdited.\n"}))
        self.assertEqual(sorted(kept.special_files), ["escape.md", "linked", "shortcut.md"])
        self.assertTrue(kept.validate(), "kept symbolic links must still be reported")
        dropped = mdzip_core.open(mdzip_core.update(source, write={"notes.md": "# Notes\n"}, drop_special_files=True))
        self.assertEqual(dropped.special_files, [])

    def test_unsupported_compression(self):  # T-014
        case = self.cases["unsupported-compression"]
        with self.assertRaises(MdzError) as ctx:
            mdzip_core.open(FIXTURES / case["archive"]).read_bytes("index.md")
        self.assertEqual(ctx.exception.code, "ERR_ZIP_INVALID")

    def test_project_mode(self):  # T-015
        case = self.cases["project-mode"]
        archive = mdzip_core.open(FIXTURES / case["archive"])
        self.assertEqual(archive.manifest["mode"], "project")
        self.assertEqual(archive.entry_point, case["expectedEntryPoint"])
        self.assertEqual(sorted(archive.documents), sorted(case["documents"]))


if __name__ == "__main__":
    unittest.main()
