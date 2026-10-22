import json
import io
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from baikal import llm
from baikal.core import atomic_json
from baikal.updates import save_packet


class LocalLLM(unittest.TestCase):
    def test_proposal_transport_preserves_versions_and_rejects_bad_citations(self):
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = {
                "source_id": "local-test",
                "sha256": "a" * 64,
                "page": 2,
                "quote": "Тестовый учёт нерпы.",
            }
            atomic_json(
                root / "config/sources.json",
                {
                    "sources": [
                        {"id": "local-test", "sha256": "a" * 64, "report_year": 2020}
                    ]
                },
            )
            atomic_json(
                root / "config/encyclopedia.json",
                {
                    "sections": [
                        {
                            "subtopics": [
                                {
                                    "topics": [
                                        {"id": "seal", "article": "syntheses/seal.md"}
                                    ]
                                }
                            ]
                        }
                    ]
                },
            )
            atomic_json(
                root / "data/processed" / ("a" * 64) / "pages/0002.json",
                {"status": "ok", "text": ref["quote"]},
            )
            p = root / "wiki/syntheses/seal.md"
            p.parent.mkdir(parents=True)
            p.write_text(
                "# Existing checked text\n\n<!-- HUMAN NOTES -->\nPreserve annotation.\n"
            )
            before = p.read_bytes()
            pid, _ = save_packet(
                root, "proposals", {"topic": "seal", "evidence": [ref]}
            )
            response = {
                "title": "Synthetic transport fixture",
                "claims": [{"text": "Fixture one", "evidence": [ref]}],
            }
            opener = MagicMock()

            def reply(*args, **kwargs):
                return io.BytesIO(
                    json.dumps(
                        {"choices": [{"message": {"content": json.dumps(response)}}]}
                    ).encode()
                )

            opener.open.side_effect = reply
            with (
                patch.object(llm, "ROOT", root),
                patch.object(llm.urllib.request, "build_opener", return_value=opener),
                patch.dict(
                    os.environ,
                    {
                        "BAIKAL_LLM_URL": "http://127.0.0.1:1234/v1/chat/completions",
                        "BAIKAL_LLM_MODEL": "test-stub",
                    },
                ),
            ):
                first = llm.synthesize("seal", proposal=pid)
                response["claims"][0]["text"] = "Fixture two"
                second = llm.synthesize("seal", proposal=pid)
                self.assertNotEqual(first["id"], second["id"])
                self.assertTrue((root / first["path"]).exists())
                packet = json.loads((root / second["path"]).read_text())
                self.assertEqual(
                    (root / packet["preceding_versions"][0]["path"]).read_bytes(),
                    before,
                )
                response["claims"][0]["evidence"] = [{**ref, "page": 3}]
                with self.assertRaisesRegex(ValueError, "unsupported citation"):
                    llm.synthesize("seal", proposal=pid)
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(
                len(list((root / "data/wiki_updates/llm_drafts").glob("*.json"))), 2
            )

    def test_remote_requires_explicit_opt_in_and_redirect_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "consent"):
            llm.validate_endpoint("https://provider.example/v1/chat/completions")
        llm.validate_endpoint("http://127.0.0.1:1234/v1/chat/completions")
        with self.assertRaisesRegex(ValueError, "redirects"):
            llm.NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://provider.example"
            )

    def test_local_http_transport_writes_cited_draft(self):
        received = []
        evidence = {
            "source_id": "local-test",
            "page": 2,
            "quote": "Измерено 12 единиц.",
        }

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                content = {
                    "title": "Проверка",
                    "claims": [{"text": "Измерено 12 единиц.", "evidence": [evidence]}],
                }
                data = json.dumps(
                    {"choices": [{"message": {"content": json.dumps(content)}}]}
                ).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                atomic_json(root / "datasets/wiki_evidence.json", {"air": [evidence]})
                atomic_json(
                    root / "config/sources.json",
                    {
                        "sources": [
                            {
                                "id": "local-test",
                                "sha256": "a" * 64,
                                "report_year": 2020,
                            }
                        ]
                    },
                )
                with (
                    patch.object(llm, "ROOT", root),
                    patch.dict(
                        os.environ,
                        {
                            "BAIKAL_LLM_URL": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                            "BAIKAL_LLM_MODEL": "test-stub",
                        },
                    ),
                ):
                    result = llm.synthesize("air")
                self.assertEqual(result["status"], "draft")
                draft = json.loads((root / result["path"]).read_text())
                self.assertFalse(result["published"])
                self.assertEqual(draft["result"]["claims"][0]["evidence"][0]["page"], 2)
                self.assertEqual(draft["evidence"][0]["sha256"], "a" * 64)
                self.assertIsNone(draft["evidence"][0]["observation_period"])
                self.assertFalse((root / "wiki").exists())
                self.assertFalse((root / "datasets/article_provenance.json").exists())
                self.assertEqual(received[0]["max_tokens"], 1800)
                self.assertIn(
                    "Измерено",
                    received[0]["messages"][1]["content"]
                    .encode()
                    .decode("unicode_escape")
                    if "\\u" in received[0]["messages"][1]["content"]
                    else received[0]["messages"][1]["content"],
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
