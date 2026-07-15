from __future__ import annotations

import unittest

from sceneactor.model import FallbackModel


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


if __name__ == "__main__":
    unittest.main()
