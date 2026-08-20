"""
Tests for the integrity manifest and the verify path.

These matter more than the rest of the suite: they are the tests behind the
claim that a bundle is tamper-evident. A verify that returns "ok" for a
modified bundle is worse than having no verify at all, because it launders a
compromised bundle as a checked one.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from caltopo_evidence.manifest import (                             # noqa: E402
    MANIFEST_DIGEST_NAME, MANIFEST_NAME, build_manifest, verify_bundle, write_manifest,
)
from caltopo_evidence.model import PhotoRecord                      # noqa: E402
from caltopo_evidence.client import sha256_file                     # noqa: E402


class BundleFixture(unittest.TestCase):
    """Builds a small, real on-disk bundle so verify is exercised against files."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.map_id = "TESTMAP"
        self.out = self.tmp / self.map_id
        self.photos = self.out / self.map_id
        self.photos.mkdir(parents=True)

        (self.photos / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"A" * 100)
        (self.photos / "b.jpg").write_bytes(b"\xff\xd8\xff" + b"B" * 200)

        self.records = []
        for name in ("a.jpg", "b.jpg"):
            p = self.photos / name
            r = PhotoRecord(map_id=self.map_id, saved_filename=name,
                            filesize_bytes=p.stat().st_size,
                            sha256=sha256_file(p), download_status="ok")
            self.records.append(r)

        self.csv_name = f"{self.map_id}_photos.csv"
        (self.out / self.csv_name).write_text("map_id,saved_filename\nTESTMAP,a.jpg\n")

        manifest = build_manifest(
            map_id=self.map_id, map_title="Test Map", base_url="https://example.invalid",
            records=self.records, map_state_sha256="0" * 64,
            csv_name=self.csv_name, csv_sha256=sha256_file(self.out / self.csv_name),
            photo_dir_name=self.map_id, operator="tester",
        )
        write_manifest(self.out, manifest)


class TestVerifyHappyPath(BundleFixture):
    def test_intact_bundle_verifies(self):
        res = verify_bundle(self.out)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(res["checked"], 3)      # 2 photos + the CSV


class TestVerifyDetectsTampering(BundleFixture):
    def test_modified_photo_detected(self):
        (self.photos / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"X" * 100)
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])
        self.assertTrue(any("digest mismatch" in p for p in res["problems"]),
                        res["problems"])

    def test_truncated_photo_reports_SIZE_not_just_a_digest_mismatch(self):
        """
        Detection here is redundant -- the digest would catch it too. What this
        pins is the DIAGNOSTIC: a reader of a failed verification needs to know
        the file was truncated rather than that its contents were altered.
        """
        (self.photos / "b.jpg").write_bytes(b"\xff\xd8\xff")
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])
        self.assertTrue(any("size" in p and "recorded" in p for p in res["problems"]),
                        res["problems"])

    def test_deleted_photo_detected(self):
        (self.photos / "a.jpg").unlink()
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])
        self.assertTrue(any("missing" in p for p in res["problems"]))

    def test_modified_csv_detected(self):
        (self.out / self.csv_name).write_text("map_id,saved_filename\nEVIL,z.jpg\n")
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])

    def test_ADDED_file_detected(self):
        """
        The check people forget. A per-file loop over the manifest can never
        notice a file that was ADDED to the bundle -- yet an extra photo in an
        evidence folder is exactly as compromising as a modified one.
        """
        (self.photos / "planted.jpg").write_bytes(b"\xff\xd8\xffZZZ")
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])
        self.assertTrue(any("not listed in manifest" in p for p in res["problems"]),
                        res["problems"])

    def test_edited_manifest_detected_by_sidecar(self):
        """Rewriting the manifest to match a swapped file must not verify."""
        mpath = self.out / MANIFEST_NAME
        doc = json.loads(mpath.read_text())
        (self.photos / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"X" * 100)
        for entry in doc["files"]:
            if entry["saved_filename"] == "a.jpg":
                entry["sha256"] = sha256_file(self.photos / "a.jpg")
        mpath.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False))
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])
        self.assertTrue(any("manifest was modified" in p for p in res["problems"]),
                        res["problems"])

    def test_missing_sidecar_is_a_failure_not_a_pass(self):
        """
        Deleting the sidecar must not make verification easier. Treating an
        absent integrity file as 'nothing to check' is the classic way a
        tamper-evidence scheme quietly stops being one.
        """
        (self.out / MANIFEST_DIGEST_NAME).unlink()
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])

    def test_missing_manifest_is_a_failure(self):
        (self.out / MANIFEST_NAME).unlink()
        res = verify_bundle(self.out)
        self.assertFalse(res["ok"])


class TestManifestContents(BundleFixture):
    def test_records_provenance(self):
        doc = json.loads((self.out / MANIFEST_NAME).read_text())
        self.assertEqual(doc["source_map"]["map_id"], self.map_id)
        self.assertEqual(doc["source_map"]["map_title"], "Test Map")
        self.assertEqual(doc["retrieval"]["operator"], "tester")
        self.assertIn("media_bytes", doc["retrieval"]["endpoints_used"])
        self.assertTrue(doc["retrieval"]["completed_at_utc"].endswith("Z"))

    def test_does_not_overclaim_signing(self):
        """
        The note must say tamper-evidence, not signature. Someone will quote
        this document in a declaration; it has to be accurate about what it is.
        """
        doc = json.loads((self.out / MANIFEST_NAME).read_text())
        note = doc["integrity_note"].lower()
        self.assertIn("not", note)
        self.assertIn("sign", note)

    def test_failed_downloads_are_not_listed_as_files(self):
        recs = list(self.records)
        recs.append(PhotoRecord(saved_filename="", download_status="failed: 502"))
        doc = build_manifest(
            map_id="M", map_title="", base_url="u", records=recs,
            map_state_sha256="0" * 64, csv_name="c.csv", csv_sha256="0" * 64,
            photo_dir_name="M")
        self.assertEqual(len(doc["files"]), 2)
        self.assertEqual(doc["counts"]["photos_found"], 3)
        self.assertIn("failed: 502", doc["counts"]["by_status"])


if __name__ == "__main__":
    unittest.main()
