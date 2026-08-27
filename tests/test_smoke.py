from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.seed_demo import create_demo


ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SmokeTest(unittest.TestCase):
    def test_demo_server_and_public_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            static = work / "static"
            static.mkdir()
            for source in (ROOT / "annotation_platform/static").iterdir():
                if source.is_file():
                    (static / source.name).write_bytes(source.read_bytes())
            create_demo(work / "demo.sqlite3", work / "audit.jsonl", static)
            port = free_port()
            env = os.environ.copy()
            env.update({
                "ANNOTATION_DB": str(work / "demo.sqlite3"),
                "ANNOTATION_AUDIT": str(work / "audit.jsonl"),
                "ANNOTATION_STATIC": str(static),
                "ANNOTATION_EXPORT_ROOT": str(work / "exports"),
                "ANNOTATION_AUTH_REQUIRED": "false",
            })
            process = subprocess.Popen(
                [sys.executable, "-m", "annotation_platform.server", "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(60):
                    try:
                        health = json.load(urlopen(base + "/api/health", timeout=1))
                        break
                    except Exception:
                        time.sleep(0.05)
                else:
                    self.fail("demo server did not become ready")
                self.assertTrue(health["ok"])
                self.assertEqual(json.load(urlopen(base + "/api/summary"))["total"], 12)
                self.assertIn("人工标注", urlopen(base + "/").read().decode("utf-8"))
                self.assertIn("部分未标", urlopen(base + "/review.html").read().decode("utf-8"))
                app_js = urlopen(base + "/app.js").read().decode("utf-8")
                self.assertIn("请给出最终标签", app_js)
                self.assertIn("正在自动跳转下一条", app_js)
                self.assertIn(b"<svg", urlopen(base + "/demo/group-01.svg").read(200))
                with self.assertRaises(HTTPError) as blocked:
                    urlopen(base + "/%2e%2e/README.md")
                self.assertEqual(blocked.exception.code, 404)
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
