"""Genome chat sessions: one human seat opposite one compiled genome persona.

Used by both the CLI (scripts/chat_with_genome.py) and the HTTP service.
The runtime is the same independent-actor stack used for performances; the
genome supplies cognition, a light scene shell and voice are synthesized here.

Optional web grounding: a cheap aux-model triage decides whether the user's
line needs post-training-cutoff facts; if so, a search briefing is injected
as world facts the actor absorbed — the actor still answers in character.
"""

from __future__ import annotations
from datetime import date

import json
from dataclasses import dataclass, field
from typing import Callable

from sceneactor.cognition import CognitionModelError, JsonCognitionPort
from sceneactor.genome import CognitiveGenome, compile_persona, compose_genomes, rules_from_specs
from sceneactor.hosts import InMemorySceneHost
from sceneactor.performance import JsonPerformancePort, PerformanceModelError
from sceneactor.persona import Persona
from sceneactor.rehearsal import ActorSetup, SceneSetup, create_rehearsal
from sceneactor.search import briefing, parse_triage, triage_prompt, web_search

USER_ID = "visitor"
CUTOFF_HINT = "模型知识可能滞后于当下，日期敏感的事实需要检索确认。"


_TURN_ECONOMY = {
    "brief": {
        "zh-CN": "每回合1-3句，短促过招，像街头闲聊。",
        "en-US": "1-3 sentences per turn; quick back-and-forth, like street banter.",
    },
    "interview": {
        "zh-CN": (
            "这是长谈访谈，不是快问快答：每回合3-8句。先给立场，再展开你的推理过程——"
            "讲一个具体例子、一段亲历、或一次你改变想法的经过。可以自己岔开话题聊到兴头上。"
            "不列清单，不做总结陈词。"
        ),
        "en-US": (
            "This is a long-form conversation, not rapid-fire Q&A: 3-8 sentences per turn. "
            "Give your position, then unpack your actual reasoning — a concrete example, "
            "a first-hand story, or a time you changed your mind. Feel free to digress when "
            "something excites you. No lists, no closing summaries."
        ),
    },
    "deep": {
        "zh-CN": (
            "对方想听你把问题真正讲透：每回合可以到一整段乃至两段。"
            "从你的第一性框架出发层层推演，引用你亲历的决策与代价，"
            "主动暴露你的不确定和内部矛盾。依然是说话不是写作：允许口语碎片和现场修正。"
        ),
        "en-US": (
            "The other person wants the full picture: one to two full paragraphs per turn is fine. "
            "Reason from your first-principles framework step by step, cite decisions you actually "
            "lived through and what they cost, and volunteer your genuine uncertainty and internal "
            "tensions. Still speech, not writing: fragments and mid-sentence corrections are fine."
        ),
    },
}


def _turn_economy(verbosity: str, lang: str) -> str:
    styles = _TURN_ECONOMY.get(verbosity, _TURN_ECONOMY["interview"])
    return styles["zh-CN"] if lang == "zh-CN" else styles["en-US"]

class _HumanSeat:
    """Cognition port for the human seat; never advanced by a chat session."""

    def decide(self, frame):  # pragma: no cover — guard only
        raise RuntimeError("human seat should never be advanced")


@dataclass
class ChatTurn:
    actor_id: str
    speech: str
    action: str = ""
    search: dict | None = None

    def to_dict(self) -> dict:
        data = {"actor_id": self.actor_id, "speech": self.speech, "action": self.action}
        if self.search:
            data["search"] = self.search
        return data


@dataclass
class ChatSession:
    """A live one-on-one conversation with a compiled genome persona."""

    record: dict
    model: Callable  # FallbackModel for cognition+performance
    aux_model: Callable | None = None  # cheap model for search triage
    scene: str = ""
    lang: str = ""
    search_enabled: bool = False
    verbosity: str = "interview"  # brief | interview | deep
    max_turns: int = 24
    fused: bool = True  # one model call per turn (chat has no failable actions)

    display: str = field(init=False)
    disclosure: str = field(init=False)
    scene_text: str = field(init=False)
    transcript: list[ChatTurn] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        record = self.record
        genome = CognitiveGenome.from_dict(
            record["genome"],
            genome_id=record["person_id"],
            source=f"{record['person_id']}-genome-db",
            source_real_person=True,
        )
        self.composite = compose_genomes(
            record["person_id"],
            [(genome, None)],
            rules=rules_from_specs(record["genome"].get("causal_rules", [])),
        )
        self.display = record.get("display_name") or record["person_id"]
        region = record.get("region", "INTL")
        self.lang = self.lang or ("zh-CN" if region == "CN" else "en-US")
        industries = "、".join(record.get("industries", [])) or "公众人物"
        default_scene = (
            f"一场轻松的一对一长谈。{self.display}状态放松，对面是一位好奇但功课做得不错的访谈者。"
            if self.lang == "zh-CN"
            else f"A relaxed one-on-one long-form conversation. {self.display} is at ease; "
            f"the interviewer opposite is curious and well-prepared."
        )
        self.scene_text = self.scene or default_scene
        self.disclosure = f"AI生成的虚构角色演绎，基于{self.display}的公开语料蒸馏，不代表本人真实观点。"

        persona = compile_persona(
            self.composite,
            persona_id=record["person_id"],
            name=f"{self.display}（AI虚构演绎）",
            shell={
                "role": f"{industries}领域公众人物的非欺骗性AI演绎",
                "gender": record.get("gender", ""),
                "background": f"基于公开语料蒸馏的认知基因组驱动。场景：{self.scene_text}",
            },
            voice={
                "output_language": self.lang,
                "turn_economy": _turn_economy(self.verbosity, self.lang),
                "localization_rule": (
                    "说人话：口语、具体、允许不完整句和现场修正；绝不用客服腔和总结腔。"
                    if self.lang == "zh-CN"
                    else "Talk like a person: colloquial, concrete, self-corrections allowed; "
                    "never sound like support staff or a summary."
                ),
                **(
                    {"speech_style": (
                        f"说话风格与口头禅（创建者设定，贯穿每次发言，但按语境自然出现，"
                        f"不要每句都用）：{record['forge']['style']}"
                    )}
                    if (record.get("forge") or {}).get("style") else {}
                ),
            },
        )
        ammo = (record.get("meme_ammo") or {}).get("register_license") or []
        if ammo:
            persona.extensions["register_license"] = list(ammo)
        moves = (record.get("forge") or {}).get("opening_moves") or []
        if moves:
            persona.extensions["performance_reference"] = {
                "opening_moves": list(moves),
                "rule": "开场即故障：第一回合必须从这些动作中选一个（或同系统的新变体）作为出场动作，"
                        "不解释、不铺垫——陌生人三秒内要能察觉这人系统有问题。",
            }
        self.persona = persona
        scene_id = f"genome-chat-{record['person_id']}"
        self.host = InMemorySceneHost(
            scene_id,
            facts={
                "O.current": (
                    "对话刚开始，对面的人还没说话。"
                    if self.lang == "zh-CN"
                    else "The conversation just started; the visitor hasn't spoken yet."
                ),
                "W.disclosure": self.disclosure,
                "W.format": self.scene_text,
            },
            targets=(persona.id, USER_ID),
            capabilities=("speak", "wait", "interact"),
        )
        setup = SceneSetup(scene_id, self.scene_text, "访谈者刚坐下。", ("seat", "table"), max_turns=self.max_turns)
        visitor = Persona(USER_ID, "访谈者", role="真人输入", background="不详，随对话展开")
        self.run = create_rehearsal(
            setup,
            (
                ActorSetup(
                    persona,
                    "像本人一样接住对话：有自己的议程和边界，不迎合，不表演金句"
                    if self.lang == "zh-CN"
                    else "Hold the conversation like the real person: own agenda, own boundaries, no pandering.",
                    "对访谈者：初次见面" if self.lang == "zh-CN" else "First meeting with the visitor.",
                    {"portrayal_mode": "explicit_fictional_parody", "disclosure": self.disclosure},
                    "open",
                ),
                ActorSetup(visitor, "聊天", "初次见面", {}, "open"),
            ),
            host=self.host,
            cognition={persona.id: self._actor_port(), USER_ID: _HumanSeat()},
            performance=self._performance_port(),
        )

    def _actor_port(self):
        if self.fused:
            from .chat_fused import FusedChatPort
            self._fused_port = FusedChatPort(self.model)
            return self._fused_port
        return JsonCognitionPort(self.model)

    def _performance_port(self):
        if self.fused:
            return self._fused_port
        return JsonPerformancePort(self.model)

    # -- turns -----------------------------------------------------------

    def _advance(self) -> ChatTurn:
        try:
            result = self.run.advance(self.persona.id)
        except (CognitionModelError, PerformanceModelError):
            result = self.run.advance(self.persona.id)  # one retry, then propagate
        draft = result.draft
        turn = ChatTurn(
            actor_id=self.persona.id,
            speech=draft.speech if draft else "",
            action=(draft.action if draft and draft.action != "speak" else ""),
        )
        self.transcript.append(turn)
        return turn

    def open(self) -> ChatTurn:
        """The persona speaks first (greeting / settling into the scene)."""
        return self._advance()

    def _maybe_search(self, line: str) -> dict | None:
        if not (self.search_enabled and self.aux_model):
            return None
        try:
            cutoff = f"{CUTOFF_HINT} 当前日期/current date: {date.today().isoformat()}"
            raw = self.aux_model(triage_prompt(line, cutoff, self.lang), "search-triage")
        except Exception:
            return None
        need, query = parse_triage(raw)
        if not need:
            return None
        hits = web_search(query, count=5)
        if not hits:
            return {"query": query, "hits": []}
        self.host.facts["K.fresh_info"] = briefing(query, hits, self.lang)
        return {"query": query, "hits": [hit.to_dict() for hit in hits]}

    def say(self, line: str) -> ChatTurn:
        """Human speaks; persona replies. Returns the persona's turn."""
        line = line.strip() or ("（沉默）" if self.lang == "zh-CN" else "(silence)")
        self.transcript.append(ChatTurn(actor_id=USER_ID, speech=line))
        self.host.facts.pop("K.fresh_info", None)
        search_note = self._maybe_search(line)
        self.host.facts["O.current"] = (
            f"对面的人刚说：{line}" if self.lang == "zh-CN" else f"The visitor just said: {line}"
        )
        if search_note and search_note.get("hits"):
            self.host.facts["O.current"] += (
                " 旁边的 K.fresh_info 是与这个问题直接相关的实时检索简报；涉及新近事实时以它为依据，不要凭记忆补细节。"
                if self.lang == "zh-CN"
                else " K.fresh_info is a live search briefing directly relevant to this question; "
                "use it for recent facts and do not fill gaps from memory."
            )
        self.host.facts["H.recent_dialogue"] = json.dumps(
            [t.to_dict() for t in self.transcript[-6:]], ensure_ascii=False
        )
        turn = self._advance()
        turn.search = search_note
        return turn

    # -- introspection -----------------------------------------------------

    def genome_card(self) -> dict:
        gd = self.record["genome"]
        return {
            "trait_axes": {k: v["score"] for k, v in gd["trait_axes"].items()},
            "core_models": [m["name"] for m in gd["core_models"]],
            "dispositions": [d["rule_id"] for d in self.composite["derived_dispositions"]],
            "blind_spots": gd["blind_spots"][:3],
        }

    def to_dict(self) -> dict:
        return {
            "person_id": self.record["person_id"],
            "display_name": self.display,
            "disclosure": self.disclosure,
            "scene": self.scene_text,
            "lang": self.lang,
            "search_enabled": self.search_enabled,
            "turns": [t.to_dict() for t in self.transcript],
        }


def validate_record(record: dict) -> None:
    """Minimal gate for uploaded persona packs before a session is built."""
    if not isinstance(record, dict):
        raise ValueError("record must be a JSON object")
    if not str(record.get("person_id", "")).strip():
        raise ValueError("record.person_id is required")
    genome = record.get("genome")
    if not isinstance(genome, dict):
        raise ValueError("record.genome (object) is required")
    for key in ("trait_axes", "core_models", "blind_spots"):
        if key not in genome:
            raise ValueError(f"record.genome.{key} is required")
