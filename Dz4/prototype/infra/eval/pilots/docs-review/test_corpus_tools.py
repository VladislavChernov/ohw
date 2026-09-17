"""Offline tests: no Docker, HTTP calls or model downloads."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import corpus_tools as tools


class CorpusToolsTests(unittest.TestCase):
    def test_dataset_sources_and_ids(self):
        self.assertEqual(len(tools.validate()["documents"]), 7)

    def make_snapshot(self, base):
        root = base / "project"
        for doc in tools.validate()["documents"]:
            path = root / doc["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            content = '{"verdict":"fail"}' if doc.get("render") else "# Документ\nТекст.\n"
            path.write_text(content, encoding="utf-8")
        snapshot = base / "snapshot"
        tools.prepare(root, snapshot)
        return root, snapshot

    def test_prepare_hashes_json_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root, snapshot = self.make_snapshot(Path(temp))
            locked = tools.check_snapshot(snapshot)
            self.assertEqual(len(list(snapshot.glob("*.md"))), 7)
            report = locked["documents"][-1]
            text = (snapshot / report["snapshot_file"]).read_text(encoding="utf-8")
            self.assertIn('```json\n{"verdict":"fail"}', text)
            self.assertTrue(report["source_url"].endswith(".json"))
            with self.assertRaises(ValueError):
                tools.prepare(root, snapshot)
            (snapshot / locked["documents"][0]["snapshot_file"]).write_text("changed", encoding="utf-8")
            with self.assertRaises(AssertionError):
                tools.check_snapshot(snapshot)

    def test_upload_uses_manifest_sources_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            _, snapshot = self.make_snapshot(Path(temp))
            submitted = []

            def fake_api(url, body=None, timeout=30):
                if url.endswith("/health"):
                    return {"mode": "sentence-transformer", "dimensions": 1024}
                if url.endswith("/activate"):
                    return {"activated": True}
                if url.endswith("/embed"):
                    return {"vector": [0.0] * 1024}
                if url.endswith("/documents"):
                    submitted.append(body)
                    return {"job_id": str(len(submitted))}
                if "/jobs/" in url:
                    return {"status": "succeeded"}
                return {"revision": "revision-test"}

            receipt = Path(temp) / "receipt.json"
            with patch.object(tools, "api", side_effect=fake_api):
                tools.upload(snapshot, receipt)
            expected = [d["source_url"] for d in tools.validate()["documents"]]
            self.assertEqual([d["source_url"] for d in submitted], expected)
            self.assertTrue(all(d["doc_type"] == "md" for d in submitted))
            self.assertEqual(json.loads(receipt.read_text(encoding="utf-8"))["revision"], "revision-test")

    def test_upload_rejects_mock_embeddings(self):
        with tempfile.TemporaryDirectory() as temp:
            _, snapshot = self.make_snapshot(Path(temp))
            with patch.object(tools, "api", return_value={"mode": "mock", "dimensions": 1024}) as api:
                with self.assertRaises(AssertionError):
                    tools.upload(snapshot, Path(temp) / "receipt.json")
                self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
