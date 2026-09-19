from __future__ import annotations

import unittest

from pc.llm.client import FALLBACK_MODEL, OllamaClient, OllamaError, PREFERRED_MODEL


class FakeListClient(OllamaClient):
    def __init__(self, requested: str, models: tuple[str, ...]) -> None:
        super().__init__(model=requested)
        self.models = models

    def list_models(self) -> tuple[str, ...]:
        return self.models


class ModelResolutionTests(unittest.TestCase):
    def test_auto_prefers_instruct(self) -> None:
        client = FakeListClient("auto", (FALLBACK_MODEL, PREFERRED_MODEL))
        self.assertEqual(client.resolve_model(), PREFERRED_MODEL)

    def test_auto_falls_back_to_original(self) -> None:
        client = FakeListClient("auto", (FALLBACK_MODEL,))
        self.assertEqual(client.resolve_model(), FALLBACK_MODEL)

    def test_explicit_missing_model_is_rejected(self) -> None:
        client = FakeListClient("missing:model", (FALLBACK_MODEL,))
        with self.assertRaises(OllamaError):
            client.resolve_model()


if __name__ == "__main__":
    unittest.main()
