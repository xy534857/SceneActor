from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class HttpServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.process = subprocess.Popen(
            [
                "python3", "-u", "scripts/serve_sceneactor.py",
                "--host", "127.0.0.1", "--port", str(self.port),
                "--gateway-url", "http://unused.invalid/v1",
                "--gateway-key", "unused",
                "--api-key", "test-key",
                "--state-dir", self.temp.name,
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                if urlopen(self.base + "/v1/health", timeout=.2).status == 200:
                    return
            except OSError:
                time.sleep(.05)
        output = self.process.stdout.read() if self.process.stdout else ""
        self.fail(f"service did not start: {output}")

    def tearDown(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        if self.process.stdout:
            self.process.stdout.close()
        self.temp.cleanup()

    def get(self, path: str, *, auth: bool = False):
        headers = {"Authorization": "Bearer test-key"} if auth else {}
        return urlopen(Request(self.base + path, headers=headers), timeout=2)

    def test_ui_and_health_are_public_but_data_is_authenticated(self) -> None:
        ui = self.get("/").read().decode()
        self.assertIn("SceneActor Console", ui)
        self.assertIn("任务队列", ui)
        self.assertEqual(json.load(self.get("/v1/health")), {"ok": True})
        try:
            self.get("/v1/config")
        except HTTPError as error:
            self.assertEqual(error.code, 401)
            error.close()
        else:
            self.fail("unauthenticated config request unexpectedly succeeded")

    def test_authenticated_lists_and_config(self) -> None:
        config = json.load(self.get("/v1/config", auth=True))
        self.assertIn("generation", config["models"])
        self.assertEqual(json.load(self.get("/v1/productions", auth=True)), {"productions": []})
        self.assertEqual(json.load(self.get("/v1/performances", auth=True)), {"jobs": []})


if __name__ == "__main__":
    unittest.main()
