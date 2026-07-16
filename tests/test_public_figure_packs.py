from __future__ import annotations

import json
from pathlib import Path
import unittest

from sceneactor.persona import Persona


PACKS = (
    Path("examples/public_figure_packs/donald_trump_2020_2024.json"),
    Path("examples/public_figure_packs/joe_biden_2020_2024.json"),
)


class PublicFigurePackTests(unittest.TestCase):
    def test_packs_have_sources_boundaries_and_loadable_personas(self) -> None:
        for path in PACKS:
            with self.subTest(path=path):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["schema_version"], "public-figure-evidence/1.0")
                self.assertEqual(data["fact_cutoff"], "2024-06-27")
                self.assertEqual(data["portrayal_contract"]["mode"], "explicit_fictional_parody")
                self.assertIn("不代表本人真实言论", data["portrayal_contract"]["required_disclosure"])
                self.assertIn("声纹克隆", data["portrayal_contract"]["voice_policy"])
                sources = {item["source_id"] for item in data["source_registry"]}
                self.assertGreaterEqual(len(sources), 4)
                self.assertTrue(all(item["url"].startswith("https://") for item in data["source_registry"]))
                for pattern in data["observed_rhetorical_patterns"]:
                    self.assertTrue(set(pattern["evidence_refs"]) <= sources)
                persona = Persona.from_dict(data["runtime_persona"])
                self.assertTrue(persona.voice.entry_point)
                self.assertEqual(persona.extensions["portrayal_mode"], "explicit_fictional_parody")
                persona_payload = json.dumps(data["runtime_persona"], ensure_ascii=False)
                for quote in data["evidence_only_short_quotes"]:
                    self.assertNotIn(quote["text"], persona_payload)
                self.assertIn("任何人真实的死后归宿", data["causal_boundaries"]["unknown_or_forbidden"])

    def test_demo_is_disclosed_and_bound_to_evidence_packs(self) -> None:
        demo = json.loads(Path("examples/public_figure_packs/hell_debate_parody_demo.json").read_text(encoding="utf-8"))
        self.assertIn("不代表Donald Trump或Joe Biden", demo["disclosure"])
        self.assertEqual(
            set(demo["source_packs"]),
            {"donald-trump-debate-2020-2024-v1", "joe-biden-debate-2020-2024-v1"},
        )
        self.assertEqual(len(demo["turns"]), 4)
        self.assertEqual(demo["independent_review"]["adjacency_and_listening"], 5)
        self.assertTrue(all(turn["speech"] for turn in demo["turns"]))


if __name__ == "__main__":
    unittest.main()
