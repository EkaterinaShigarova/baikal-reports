import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from baikal import llm
from baikal.core import atomic_json


class LocalLLM(unittest.TestCase):
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
                with (
                    patch.object(llm, "ROOT", root),
                    patch.object(llm, "WIKI", root / "wiki"),
                    patch.object(llm, "build"),
                    patch.object(llm, "append_log"),
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
                draft = (root / "wiki/syntheses/air-llm-draft.md").read_text()
                self.assertIn("local-test:p2", draft)
                self.assertIn("Черновик", draft)
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
