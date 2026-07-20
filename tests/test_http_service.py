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

    def test_ui_and_data_are_public_on_whitelisted_service(self) -> None:
        ui = self.get("/").read().decode()
        self.assertIn("SceneActor Console", ui)
        self.assertIn("任务队列", ui)
        self.assertEqual(json.load(self.get("/v1/health")), {"ok": True})
        self.assertIn("generation", json.load(self.get("/v1/config"))["models"])

    def test_lists_are_available_without_browser_credentials(self) -> None:
        self.assertEqual(json.load(self.get("/v1/productions")), {"productions": []})
        self.assertEqual(json.load(self.get("/v1/performances")), {"jobs": []})

    def test_uploading_a_spec_registers_a_production_without_compiling(self) -> None:
        spec = {
            "scenes": [{
                "scene_id": "door", "setting": "门口", "opening": "有人等着",
                "affordances": ["door"], "max_turns": 2,
                "host_facts": {"O.weather": "下雨"}, "speaking_order": [],
            }],
            "actors": [
                {"persona": {"id": "a", "name": "甲"}, "goal": "进入", "relationship": "", "private_state": {}, "disclosure": "guarded"},
                {"persona": {"id": "guard", "name": "乙"}, "goal": "登记", "relationship": "", "private_state": {}, "disclosure": "guarded"},
            ],
            "disclosure": "AI生成的虚构表演。",
        }
        request = Request(
            self.base + "/v1/productions",
            data=json.dumps({"spec": spec}).encode(),
            headers={"Content-Type": "application/json"},
        )
        created = json.load(urlopen(request, timeout=5))
        self.assertTrue(created["production_id"].startswith("prod:"))
        self.assertEqual(created["spec"]["scenes"][0]["scene_id"], "door")
        listed = json.load(self.get("/v1/productions"))
        self.assertEqual(len(listed["productions"]), 1)
        fetched = json.load(self.get(f"/v1/productions/{created['production_id']}"))
        self.assertEqual(fetched["spec"]["actors"][0]["persona"]["id"], "a")

    def test_uploading_an_invalid_spec_is_rejected_with_the_validator_error(self) -> None:
        request = Request(
            self.base + "/v1/productions",
            data=json.dumps({"spec": {"scenes": [], "actors": []}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(request, timeout=5)
        except HTTPError as error:
            self.assertEqual(error.code, 400)
            self.assertIn("invalid spec", json.load(error)["error"])
            error.close()
        else:
            self.fail("invalid spec unexpectedly accepted")



if __name__ == "__main__":
    unittest.main()
