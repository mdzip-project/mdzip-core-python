"""The MDZip spec's shared fixtures (tests/fixtures, synced with mdzip-spec tools/sync-fixtures.mjs)."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

import mdzip
from mdzip.errors import MdzError
from mdzip.links import ArchiveSource, HostSource
from mdzip.paths import find_path, path_error, same_path

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
        self.assertEqual(mdzip.open(FIXTURES / "minimal.mdz").entry_point, "index.md")

    def test_with_manifest(self):
        archive = mdzip.open(FIXTURES / "with-manifest.mdz")
        self.assertEqual(archive.entry_point, archive.manifest["entryPoint"])

    def test_missing_entry_point_target(self):  # T-003
        with self.assertRaises(MdzError) as ctx:
            mdzip.open(FIXTURES / "invalid-missing-index.mdz").entry_point
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_MISSING")

    def test_bad_entry_point(self):  # T-004
        with self.assertRaises(MdzError) as ctx:
            mdzip.open(FIXTURES / "invalid-bad-entrypoint.mdz").entry_point
        self.assertEqual(ctx.exception.code, "ERR_ENTRYPOINT_MISSING")

    def test_crlf(self):  # T-005
        archive = mdzip.open(FIXTURES / "line-endings-crlf.mdz")
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
                        mdzip.resolve_entry_point(files, manifest)
                    if "expectedError" in case:
                        self.assertEqual(ctx.exception.code, case["expectedError"])
                else:
                    self.assertEqual(mdzip.resolve_entry_point(files, manifest), expected)


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

    def test_matching(self):
        for case in load("path-matching-cases.json"):
            with self.subTest(case["name"]):
                self.assertEqual(same_path(case["entry"], case["lookup"]), case["matches"])

    def test_create_filters(self):
        for case in load("create-filter-cases.json"):
            with self.subTest(case["name"]):
                included = [p for p in case["inputPaths"] if mdzip.matches_filters(p, case["filters"])]
                excluded = [p for p in case["inputPaths"] if p not in included]
                self.assertEqual(sorted(included), sorted(case["expectedIncluded"]))
                self.assertEqual(sorted(excluded), sorted(case["expectedExcluded"]))


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
                target = mdzip.resolve_link(source, case["link"], FixtureHost(fixture["host"], case.get("context")))
                self.assertEqual(target.as_dict(), case["expected"])


class RoundTripTest(unittest.TestCase):
    """P-004 / P-005: unknown manifest fields and untouched entries survive every flow, twice."""

    def setUp(self):
        self.exp = load("round-trip-expectations.json")
        self.original = (FIXTURES / "round-trip.mdz").read_bytes()

    def flow(self, name, data, round_no):
        if name == "open and save without an edit":
            return mdzip.update(data)
        if name == "change a recognized manifest field":
            return mdzip.update(data, manifest={"title": "Edited title"})
        if name == "edit document content":
            archive = mdzip.open(data)
            return mdzip.update(data, write={"index.md": archive.read_text("index.md") + "\nEdited.\n"})
        if name == "add a resource":
            return mdzip.update(data, write={f"images/added-{round_no}.png": b"\x89PNG"})
        if name == "remove a recognized resource":
            if round_no == 1:
                return mdzip.update(data, remove=["docs/guide.md"])
            return mdzip.update(data, manifest={"title": "Second save"})
        raise AssertionError(f"unknown flow {name}")

    def check(self, data, flow):
        archive = mdzip.open(data)
        for item in self.exp["preservedManifestValues"]:
            value = archive.manifest
            for key in item["pointer"].split("/")[1:]:
                value = value[key.replace("~1", "/").replace("~0", "~")]
            self.assertEqual(value, item["value"], f"{flow['name']}: {item['pointer']}")
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


if __name__ == "__main__":
    unittest.main()
