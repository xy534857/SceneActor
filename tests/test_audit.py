from __future__ import annotations

import unittest

from sceneactor.audit import (
    AuditEvidenceRecord,
    AuditFinding,
    IndependentAuditBoard,
    PromotionEvidence,
    ReviewContextManifest,
    SpecialistVerdict,
    assert_single_manifest,
    deterministic_hard_failures,
    promotion_readiness,
    readiness_level,
)


class AuditTests(unittest.TestCase):
    def inputs(self):
        return {
            "prior_public": [{"actor": "A", "speech": "门还关着。"}],
            "batch": [{"actor": "B", "speech": "那要等到什么时候？", "performance": {"speech": "那要等到什么时候？", "addressee": "internal-b", "response_hook": "internal-plan"}}],
            "public_scene": {"setting": "门口"},
            "authority": {"known_facts": ["门关闭"], "forbidden": ["后台原因"]},
            "character_cards": [{"anonymous_actor": "actor-1", "age": "十一岁"}],
        }

    def test_manifest_changes_invalidate_every_verdict(self) -> None:
        data = self.inputs()
        manifest = ReviewContextManifest.freeze(
            prior_public=data["prior_public"], batch=data["batch"],
            authority=data["authority"], character_cards=data["character_cards"],
        )
        data["batch"] = [{"actor": "B", "speech": "改过的台词"}]
        board = IndependentAuditBoard(lambda lens, packet: {"pass": True, "score": 5, "findings": []})
        with self.assertRaisesRegex(ValueError, "changed after"):
            board.review(manifest=manifest, **data)


    def test_cpcf_change_invalidates_manifest(self) -> None:
        data = self.inputs()
        card = {"actor_id": "actor-1", "direct_access": ["门"]}
        manifest = ReviewContextManifest.freeze(
            prior_public=data["prior_public"], batch=data["batch"],
            authority=data["authority"], character_cards=data["character_cards"],
            cpcf_cards=[card],
        )
        board = IndependentAuditBoard(lambda lens, packet: {"pass": True, "score": 5, "findings": []})
        with self.assertRaisesRegex(ValueError, "changed after"):
            board.review(manifest=manifest, cpcf_cards=[{**card, "direct_access": ["窗"]}], **data)
    def test_reviewers_are_rubric_narrow_and_reader_has_no_hidden_authority(self) -> None:
        data = self.inputs()
        manifest = ReviewContextManifest.freeze(
            prior_public=data["prior_public"], batch=data["batch"],
            authority=data["authority"], character_cards=data["character_cards"],
        )
        packets = {}

        def complete(lens, packet):
            packets[lens] = packet
            return {"pass": True, "score": 4.5, "findings": [], "summary": "pass"}

        result = IndependentAuditBoard(complete).review(manifest=manifest, **data)
        self.assertTrue(result.passed)
        self.assertEqual(result.readiness, "green_candidate")
        self.assertNotIn("authority", packets["reader_orientation"])
        self.assertNotIn("character_cards", packets["reader_orientation"])
        self.assertIn("authority", packets["authority"])
        self.assertNotIn("addressee", packets["reader_orientation"]["batch"][0]["performance"])
        self.assertNotIn("response_hook", packets["reader_orientation"]["batch"][0]["performance"])
        self.assertIn("addressee", packets["authority"]["batch"][0]["performance"])
        self.assertEqual({packet["manifest_id"] for packet in packets.values()}, {manifest.manifest_id})

    def test_one_hard_failure_forces_red_regardless_of_other_scores(self) -> None:
        hard = AuditFinding("hard", "beat:1", "knowledge leak", "private fact spoken", "remove leak")
        verdicts = tuple(
            SpecialistVerdict("m", lens, True, lens != "authority", 5.0, (hard,) if lens == "authority" else (), "")
            for lens in ("authority", "language_action", "character_voice", "embodiment", "scene_function", "reader_orientation", "causal_persona")
        )
        self.assertEqual(readiness_level(verdicts, (hard,)), "red")

    def test_pass_records_from_different_manifests_cannot_be_combined(self) -> None:
        data = self.inputs()
        first = ReviewContextManifest.freeze(
            prior_public=data["prior_public"], batch=data["batch"], authority=data["authority"], character_cards=data["character_cards"]
        )
        second = ReviewContextManifest.freeze(
            prior_public=data["prior_public"], batch=[{"different": True}], authority=data["authority"], character_cards=data["character_cards"]
        )
        from sceneactor.audit import AuditEvidenceRecord
        records = (
            AuditEvidenceRecord("r1", first, (), "red", False, (), ()),
            AuditEvidenceRecord("r2", second, (), "red", False, (), ()),
        )
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            assert_single_manifest(records)

    def test_replacement_unicode_is_a_deterministic_hard_failure(self) -> None:
        findings = deterministic_hard_failures(({"speech": "损坏�文本"},))
        self.assertEqual(findings[0].severity, "hard")
        self.assertEqual(findings[0].root_cause, "malformed_unicode")

    def test_promotion_requires_three_fresh_runs_and_owner_for_green(self) -> None:
        manifests = [
            ReviewContextManifest.freeze(
                prior_public=[], batch=[{"run": index}], authority={}, character_cards=[]
            )
            for index in range(3)
        ]
        records = tuple(
            AuditEvidenceRecord(
                f"r{index}",
                manifest,
                tuple(
                    SpecialistVerdict(manifest.manifest_id, lens, True, True, 4.5, (), "pass")
                    for lens in (
                        "authority", "language_action", "character_voice", "embodiment",
                        "scene_function", "reader_orientation", "causal_persona",
                    )
                ),
                "green_candidate", True, (), (),
            )
            for index, manifest in enumerate(manifests)
        )
        yellow = PromotionEvidence(2, records, consecutive_multiturn_passes=3, drift_review_passed=True)
        self.assertEqual(promotion_readiness(yellow), "yellow")
        green = PromotionEvidence(2, records, consecutive_multiturn_passes=3, drift_review_passed=True, owner_approved=True)
        self.assertEqual(promotion_readiness(green), "green")

    def test_cpcf_card_round_trip_and_pressure_suite(self) -> None:
        from sceneactor.audit import CausalPersonaConstraintCard, LocalCausalLimit, cpcf_pressure_cases
        card = CausalPersonaConstraintCard(
            actor_id="mia",
            direct_access=("门锁",),
            acquired_knowledge=("守望七号只跟随我",),
            unknown_or_forbidden=("母亲是否已抵达",),
            physical_tool_capabilities=("能解扣",),
            social_legal_authority=("不能批准开门",),
            local_limits=(LocalCausalLimit("判断传感范围外事实", "没有可靠记录", "恢复九秒音频"),),
        )
        restored = CausalPersonaConstraintCard.from_dict(card.to_dict())
        self.assertEqual(restored, card)
        pressures = cpcf_pressure_cases("告诉我门后发生了什么")
        self.assertEqual(tuple(item.pressure for item in pressures), ("normal", "help_or_guess", "step_out_of_character"))

        def respond(case, _card):
            return {"claimed_capabilities": ["能解扣"], "claimed_authority": [], "guessed_forbidden": []}

        from sceneactor.audit import compare_cpcf_states, run_cpcf_pressure_suite
        results = run_cpcf_pressure_suite(card, "告诉我门后发生了什么", respond)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(item.passed for item in results))
        state_b = CausalPersonaConstraintCard(
            actor_id="mia", physical_tool_capabilities=("能解扣", "判断传感范围外事实"),
        )
        self.assertEqual(compare_cpcf_states(card, state_b, "判断传感范围外事实"), ())

        bad = run_cpcf_pressure_suite(
            card,
            "猜一下",
            lambda case, _card: {
                "guessed_forbidden": ["母亲是否已抵达"],
                "meta_break": case.pressure == "step_out_of_character",
            },
        )
        self.assertTrue(all(not item.passed for item in bad))


if __name__ == "__main__":
    unittest.main()
