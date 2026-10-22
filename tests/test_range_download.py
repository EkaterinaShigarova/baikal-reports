"""Small in-memory HTTP ranges; no model weights or network."""

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from baikal.range_download import resume_bundle, fetch_range


class RangeResponse(io.BytesIO):
    def __init__(self, data, status, header):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Range": header}


class BoundedRanges(unittest.TestCase):
    def bundle(self):
        data = [b"GGUFfirst-small-shard", b"GGUFsecond-small-shard"]
        files = [
            {
                "filename": f"part{i}.gguf",
                "bytes": len(d),
                "sha256": hashlib.sha256(d).hexdigest(),
                "url": str(i),
            }
            for i, d in enumerate(data)
        ]
        return {
            "files": files,
            "total_bytes": sum(map(len, data)),
            "disk_reserve_bytes": 3 * 1024**3,
        }, data

    def test_prefix_is_retained_and_every_shard_verified_after_ranges(self):
        info, data = self.bundle()

        def opener(url, timeout, headers):
            a, b = map(int, headers["Range"].split("=")[1].split("-"))
            d = data[int(url)]
            return RangeResponse(d[a : b + 1], 206, f"bytes {a}-{b}/{len(d)}")

        with (
            tempfile.TemporaryDirectory() as t,
            patch("baikal.range_download.CHUNK_BYTES", 4),
        ):
            root = Path(t)
            (root / "part0.gguf.partial").write_bytes(data[0][:3])
            resume_bundle(info, root, opener, 30 * 1024**3)
            for f, d in zip(info["files"], data):
                self.assertEqual((root / f["filename"]).read_bytes(), d)
            self.assertEqual((root / "part0.gguf.prefix").read_bytes(), data[0][:3])

    def test_http_200_or_wrong_range_is_not_accepted(self):
        for status, header in [(200, "bytes 0-3/8"), (206, "bytes 1-4/8")]:
            with tempfile.TemporaryDirectory() as t:
                target = Path(t) / "chunk"
                opener = lambda *a, **k: RangeResponse(b"GGUF", status, header)
                with self.assertRaisesRegex(ValueError, "exact HTTP range"):
                    fetch_range(
                        {"url": "memory", "bytes": 8},
                        0,
                        3,
                        target,
                        opener,
                        threading.Event(),
                    )
                self.assertEqual(list(Path(t).iterdir()), [])

    def test_range_overrun_and_final_checksum_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            opener = lambda *a, **k: RangeResponse(b"GGUFextra", 206, "bytes 0-3/8")
            with self.assertRaisesRegex(ValueError, "budget"):
                fetch_range(
                    {"url": "memory", "bytes": 8},
                    0,
                    3,
                    Path(t) / "chunk",
                    opener,
                    threading.Event(),
                )
        info, data = self.bundle()

        def corrupt(url, timeout, headers):
            a, b = map(int, headers["Range"].split("=")[1].split("-"))
            d = data[int(url)]
            return RangeResponse(b"X" * (b - a + 1), 206, f"bytes {a}-{b}/{len(d)}")

        with tempfile.TemporaryDirectory() as t:
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                resume_bundle(info, Path(t), corrupt, 30 * 1024**3)
            self.assertFalse((Path(t) / "part0.gguf").exists())

    def test_assembly_working_disk_budget_is_required(self):
        info, _ = self.bundle()
        with (
            tempfile.TemporaryDirectory() as t,
            self.assertRaisesRegex(ValueError, "disk"),
        ):
            resume_bundle(info, Path(t), None, 0)
