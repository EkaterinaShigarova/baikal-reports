"""Check registration documented in USER_GUIDE in an isolated package copy."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from baikal.core import ROOT, read_json, sha256


class GuideCommands(unittest.TestCase):
    def test_register_and_status_are_isolated_idempotent_and_do_not_download(self):
        original = sha256(ROOT / "config/sources.json")
        url = next(
            s["url"]
            for s in read_json(ROOT / "config/sources.json")["sources"]
            if s["id"].startswith("official-")
        )
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shutil.copytree(
                ROOT / "baikal",
                root / "baikal",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            (root / "config").mkdir()
            (root / "config/sources.json").write_text('{"sources":[]}')
            cmd = [
                sys.executable,
                "-m",
                "baikal",
                "add-source",
                "--url",
                url,
                "--name",
                "Synthetic registration fixture",
            ]
            outputs = [
                subprocess.run(
                    cmd, cwd=root, capture_output=True, text=True, check=True
                ).stdout.strip()
                for _ in range(2)
            ]
            self.assertEqual(outputs[0], outputs[1])
            self.assertTrue(outputs[0].startswith("official-"))
            registered = read_json(root / "config/sources.json")["sources"]
            self.assertEqual(len(registered), 1)
            self.assertEqual(registered[0]["authenticity"], "official_domain_candidate")
            self.assertFalse((root / "data/raw").exists())
            status = subprocess.run(
                [sys.executable, "-m", "baikal", "status"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("discovered", status.stdout)
        self.assertEqual(sha256(ROOT / "config/sources.json"), original)
