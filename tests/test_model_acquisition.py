"""Resource/integrity fixtures only; no model weights are fetched by these tests."""

import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.download_evaluation_model import plan, download, FILENAME, MAX_BYTES
from baikal.core import ROOT


class ModelAcquisition(unittest.TestCase):
    def test_standalone_entrypoints_import_without_project_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ["download_evaluation_model.py", "evaluate_local_llm.py"]:
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / name), "--help"],
                    cwd=d,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def metadata(self):
        return {
            "sha": "a" * 40,
            "cardData": {"license": "apache-2.0"},
            "siblings": [
                {"rfilename": FILENAME, "lfs": {"size": 10, "sha256": "b" * 64}}
            ],
        }

    def resource(self):
        return {
            "memory_limit_bytes": 16 * 1024**3,
            "memory_available_bytes": 8 * 1024**3,
            "disk_free_bytes": 8 * 1024**3,
        }

    def test_metadata_requires_published_hash_license_and_resource_budget(self):
        data = self.metadata()
        res = self.resource()
        self.assertEqual(plan(data, res)["bytes"], 10)
        for key in ["memory_limit_bytes", "memory_available_bytes", "disk_free_bytes"]:
            with self.assertRaises(ValueError):
                plan(data, {**res, key: 0})
        data["siblings"][0]["lfs"]["size"] = MAX_BYTES + 1
        with self.assertRaises(ValueError):
            plan(data, res)
        data = self.metadata()
        data["siblings"][0]["lfs"]["sha256"] = ""
        with self.assertRaises(ValueError):
            plan(data, res)
        data = self.metadata()
        data["cardData"]["license"] = "unverified"
        with self.assertRaises(ValueError):
            plan(data, res)

    def test_excess_bytes_abort_and_remove_only_own_partial_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            info = plan(self.metadata(), self.resource())
            with patch(
                "scripts.download_evaluation_model.urllib.request.urlopen",
                return_value=io.BytesIO(b"GGUF" * 4),
            ):
                with self.assertRaisesRegex(ValueError, "budget"):
                    download(info, root)
            self.assertFalse((root / FILENAME).exists())
            self.assertFalse((root / (FILENAME + ".partial")).exists())
            partial = root / (FILENAME + ".partial")
            partial.write_bytes(b"Existing user partial")
            with self.assertRaises(FileExistsError):
                download(info, root)
            self.assertEqual(partial.read_bytes(), b"Existing user partial")

    def test_existing_mismatched_model_is_preserved_without_network(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            p = root / FILENAME
            p.write_bytes(b"Existing model")
            with patch(
                "scripts.download_evaluation_model.urllib.request.urlopen"
            ) as network:
                with self.assertRaisesRegex(ValueError, "left unchanged"):
                    download(plan(self.metadata(), self.resource()), root)
                network.assert_not_called()
            self.assertEqual(p.read_bytes(), b"Existing model")
