from __future__ import annotations

from dataclasses import replace
import json
import unittest

from sceneactor.templates import (
    AssetRef,
    FixedAnchors,
    InMemoryTemplateProvider,
    RelationshipRole,
    RightsGrant,
    SlotDefinition,
    TemplatePerformanceContract,
    TemplateProtocolError,
    TemplateResolveRequest,
    TemplateResolveResponse,
    TemplateReplayRecord,
    TemporalAnchor,
    project_template,
    resolve_template,
)


class TemplateProtocolTests(unittest.TestCase):
    def contract(self) -> TemplatePerformanceContract:
        visual = AssetRef("visual:lead", "3", "character", "a" * 64, "asset://visual:lead@3", "rights:template")
        audio = AssetRef("audio:cue", "2", "audio", "b" * 64, "asset://audio:cue@2", "rights:template")
        return TemplatePerformanceContract(
            template_id="relationship.daily-discovery",
            version="1.2.0",
            content_hash="",
            mother_formula="固定关系角色在日常任务中发现异常物件并以关系反应收尾",
            emotional_core="荒诞中的陪伴",
            reality_mode="heightened",
            performance_mode="hybrid",
            public_rules=("定位灯闪烁时，现场人物都能看见。",),
            fixed_anchors=FixedAnchors(
                visual_asset_ids=("visual:lead",),
                temporal=(
                    TemporalAnchor("cue:turn", "audio_cue", 0, "提示音后角色转头", "after cue", "audio:cue"),
                    TemporalAnchor("cue:reaction", "reaction", 1, "包袱后保留同伴反应", "after payoff"),
                ),
                narrative_rules=("异常必须改变固定角色关系。",),
            ),
            slots=(
                SlotDefinition("lead", "character", "本集主动角色", allowed_values=("character:dog", "character:cat")),
                SlotDefinition("scene", "scene", "公开发生地点"),
                SlotDefinition("task", "task_or_prop", "Adapter 使用的任务或道具", required=False, visibility="adapter", default_value="prop:radio"),
                SlotDefinition("ending", "ending", "Review 使用的结局槽", required=False, visibility="review", default_value="ending:caretaker-pays"),
            ),
            relationship_roles=(RelationshipRole("lead", "主动者", "总想把同伴拉进新任务"),),
            payoff_condition="同伴被迫按正常流程处理荒谬发现",
            reaction_target="companion",
            allowed_variation=("地点和异常物件可替换",),
            forbidden_drift=("角色解释为什么好笑", "所有角色同时失去现实判断"),
            assets=(visual, audio),
            rights=RightsGrant(
                "rights:template",
                "approved",
                ("research", "noncommercial", "commercial"),
                "2027-12-31T23:59:59Z",
                ("license:template-001",),
            ),
        ).seal()

    def request(self, **changes) -> TemplateResolveRequest:
        data = {
            "request_id": "request-1",
            "template_id": "relationship.daily-discovery",
            "version": "1.2.0",
            "selected_slots": {"lead": "character:dog", "scene": "scene:night-market"},
            "usage_context": "commercial",
        }
        data.update(changes)
        return TemplateResolveRequest(**data)

    def test_contract_round_trip_and_integrity(self) -> None:
        contract = self.contract()
        restored = TemplatePerformanceContract.from_dict(contract.to_dict())
        self.assertEqual(restored, contract)
        restored.verify_integrity()
        self.assertEqual(len(contract.content_hash), 64)

    def test_tampered_contract_is_rejected(self) -> None:
        payload = self.contract().to_dict()
        payload["mother_formula"] = "被替换的公式"
        tampered = TemplatePerformanceContract.from_dict(payload)
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            tampered.verify_integrity()
        with self.assertRaisesRegex(ValueError, "content hash mismatch"):
            InMemoryTemplateProvider((tampered,))

    def test_exact_version_resolution_applies_defaults_and_is_stable(self) -> None:
        provider = InMemoryTemplateProvider((self.contract(),))
        first = resolve_template(provider, self.request())
        second = resolve_template(provider, self.request())
        self.assertEqual(first.resolution_hash, second.resolution_hash)
        self.assertEqual(first.selected_slots["task"], "prop:radio")
        self.assertEqual(first.selected_slots["ending"], "ending:caretaker-pays")
        self.assertEqual(first.contract.version, "1.2.0")

    def test_not_found_version_conflict_rights_and_slots_are_distinct(self) -> None:
        provider = InMemoryTemplateProvider((self.contract(),))
        with self.assertRaisesRegex(ValueError, "not latest"):
            self.request(version="latest")
        cases = (
            (self.request(template_id="missing"), "not_found"),
            (self.request(version="9.0.0"), "version_conflict"),
            (self.request(selected_slots={"lead": "character:dog", "scene": "x", "unknown": "y"}), "invalid_slots"),
            (self.request(selected_slots={"lead": "character:bird", "scene": "x"}), "invalid_slots"),
        )
        for request, code in cases:
            with self.subTest(code=code), self.assertRaises(TemplateProtocolError) as raised:
                resolve_template(provider, request)
            self.assertEqual(raised.exception.code, code)

        limited = replace(
            self.contract(),
            content_hash="",
            assets=tuple(replace(asset, rights_ref="rights:limited") for asset in self.contract().assets),
            rights=RightsGrant("rights:limited", "research_only", ("research", "commercial")),
        ).seal()
        with self.assertRaises(TemplateProtocolError) as raised:
            resolve_template(InMemoryTemplateProvider((limited,)), self.request())
        self.assertEqual(raised.exception.code, "rights_restricted")

    def test_response_round_trip_and_request_id_mismatch(self) -> None:
        contract = self.contract()
        slots = {"lead": "character:dog", "scene": "scene:night-market", "task": "prop:radio", "ending": "ending:caretaker-pays"}
        response = TemplateResolveResponse("request-1", "ok", slots, contract=contract)
        self.assertEqual(TemplateResolveResponse.from_dict(response.to_dict()), response)

        class WrongProvider:
            def resolve(self, request):
                return TemplateResolveResponse("different-request", "ok", slots, contract=contract)

        with self.assertRaises(TemplateProtocolError) as raised:
            resolve_template(WrongProvider(), self.request())
        self.assertEqual(raised.exception.code, "protocol_error")

    def test_layer_projections_keep_author_and_adapter_data_out_of_actor(self) -> None:
        resolved = resolve_template(InMemoryTemplateProvider((self.contract(),)), self.request())
        projections = project_template(resolved)
        actor_payload = projections.actor.to_prompt_payload()
        serialized = json.dumps(actor_payload, ensure_ascii=False, sort_keys=True)

        self.assertEqual(set(actor_payload["selected_slots"]), {"lead", "scene"})
        self.assertNotIn("mother_formula", serialized)
        self.assertNotIn("emotional_core", serialized)
        self.assertNotIn("payoff", serialized)
        self.assertNotIn("rights", serialized)
        self.assertNotIn("prop:radio", serialized)
        self.assertNotIn("ending:caretaker-pays", serialized)
        self.assertEqual(projections.host.binding.contract_hash, resolved.contract.content_hash)
        self.assertIn("task", projections.adapter.selected_slots)
        self.assertNotIn("ending", projections.adapter.selected_slots)
        self.assertIn("ending", projections.review.selected_slots)
        self.assertEqual(projections.adapter.temporal_anchors[0].asset_id, "audio:cue")
        self.assertEqual(projections.review.emotional_core, "荒诞中的陪伴")

    def test_replay_record_is_self_contained_and_tamper_evident(self) -> None:
        resolved = resolve_template(InMemoryTemplateProvider((self.contract(),)), self.request())
        record = resolved.to_replay_record()
        restored = TemplateReplayRecord.from_dict(record.to_dict())
        self.assertEqual(restored, record)
        payload = record.to_dict()
        payload["selected_slots"]["scene"] = "scene:changed"
        with self.assertRaisesRegex(ValueError, "resolution hash mismatch"):
            TemplateReplayRecord.from_dict(payload)

    def test_anchor_order_and_asset_rights_are_contract_invariants(self) -> None:
        contract = self.contract()
        bad_rights_assets = tuple(replace(asset, rights_ref="rights:other") for asset in contract.assets)
        with self.assertRaisesRegex(ValueError, "asset rights_ref"):
            replace(contract, content_hash="", assets=bad_rights_assets)
        with self.assertRaisesRegex(ValueError, "duplicate temporal anchor order"):
            replace(
                contract.fixed_anchors,
                temporal=(
                    contract.fixed_anchors.temporal[0],
                    replace(contract.fixed_anchors.temporal[1], order=0),
                ),
            )


if __name__ == "__main__":
    unittest.main()
