from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from sceneactor.model import FallbackModel, OmpCliCompletion


class ModelTests(unittest.TestCase):
    def test_fallback_is_used_only_after_transport_failure(self) -> None:
        calls = []

        def complete(messages, purpose, model):
            del messages
            calls.append((purpose, model))
            if model.endswith("sol"):
                raise ConnectionError("primary unavailable")
            return '{"ok":true}'

        client = FallbackModel(complete)
        self.assertEqual(client([], "cognition"), '{"ok":true}')
        self.assertEqual(calls, [("cognition", "owtr/gpt-5.6-sol"), ("cognition", "owtr/gpt-5.6-luna")])
        self.assertEqual(client.attempts[0].error, "primary unavailable")

    def test_success_does_not_call_fallback(self) -> None:
        calls = []
        client = FallbackModel(lambda messages, purpose, model: calls.append(model) or "ok")
        self.assertEqual(client([], "realization"), "ok")
        self.assertEqual(calls, ["owtr/gpt-5.6-sol"])

    @patch("sceneactor.model.subprocess.run")
    def test_omp_cli_uses_selected_model_and_high_thinking(self, run) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, stdout='{"ok":true}\n', stderr="")
        client = OmpCliCompletion()
        result = client(
            [{"role": "system", "content": "Return JSON"}, {"role": "user", "content": "hello"}],
            "cognition",
            "owtr/gpt-5.6-sol",
        )
        command = run.call_args.args[0]
        self.assertEqual(result, '{"ok":true}')
        self.assertIn("owtr/gpt-5.6-sol", command)
        self.assertIn("high", command)
        self.assertIn("--no-tools", command)


if __name__ == "__main__":
    unittest.main()
