"""Production service: compile external scripts into internal specs, then perform.

Two capabilities, mirroring the public service surface:

1. ``compile_production`` — an LLM translates a caller's free-form script and
   character documents into our internal structures (Persona / SceneSetup /
   ActorSetup). Deterministic validators accept or reject the result; on
   rejection the model gets the validator feedback and repairs its output.
   Semantic interpretation belongs to the model, admission belongs to code.

2. ``perform`` — run the compiled production through the rehearsal runtime
   (independent cognition per actor, standing intents, register licenses,
   speech corpora) and return a performance document, optionally blind-reviewed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from .cognition import CognitionModelError, JsonCognitionPort
from .hosts import InMemorySceneHost
from .performance import JsonPerformancePort, PerformanceModelError
from .persona import Persona
from .rehearsal import ActorSetup, SceneSetup, create_rehearsal
from .review import BlindReviewer, JsonBlindReviewPort


class ProductionCompileError(RuntimeError):
    """The model could not produce a valid production spec from the material."""


@dataclass(frozen=True)
class SceneEpisode:
    """One scene of a production: stage + its own facts and rotation."""

    scene: SceneSetup
    host_facts: dict[str, str] = field(default_factory=dict)
    speaking_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for key in self.host_facts:
            if not key.startswith("O."):
                raise ValueError(f"host fact keys must start with 'O.': {key}")


@dataclass(frozen=True)
class ProductionSpec:
    """Validated internal form of an external script + character docs."""

    actors: tuple[ActorSetup, ...]
    episodes: tuple[SceneEpisode, ...]
    disclosure: str = "AI生成的虚构表演。"

    def __post_init__(self) -> None:
        if len(self.actors) < 2:
            raise ValueError("a production requires at least two actors")
        if not self.episodes:
            raise ValueError("a production requires at least one scene")
        ids = [item.persona.id for item in self.actors]
        if len(set(ids)) != len(ids):
            raise ValueError("actor persona ids must be unique")
        seen_scenes: set[str] = set()
        for episode in self.episodes:
            if episode.scene.scene_id in seen_scenes:
                raise ValueError(f"duplicate scene_id: {episode.scene.scene_id}")
            seen_scenes.add(episode.scene.scene_id)
            unknown = set(episode.speaking_order) - set(ids)
            if unknown:
                raise ValueError(f"speaking_order names unknown actors: {sorted(unknown)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "actors": [
                {
                    "persona": item.persona.to_dict(),
                    "goal": item.goal,
                    "relationship": item.relationship,
                    "private_state": dict(item.private_state),
                    "disclosure": item.disclosure,
                }
                for item in self.actors
            ],
            "scenes": [
                {
                    **asdict(episode.scene),
                    "host_facts": dict(episode.host_facts),
                    "speaking_order": list(episode.speaking_order),
                }
                for episode in self.episodes
            ],
            "disclosure": self.disclosure,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProductionSpec":
        actors_data = data.get("actors")
        if not isinstance(actors_data, list):
            raise ValueError("spec requires an actors array")
        scenes_data = data.get("scenes")
        if not isinstance(scenes_data, list) or not scenes_data:
            single = data.get("scene")
            if not isinstance(single, Mapping):
                raise ValueError("spec requires a scenes array (or a single scene object)")
            scenes_data = [
                {
                    **dict(single),
                    "host_facts": data.get("host_facts", {}),
                    "speaking_order": data.get("speaking_order", []),
                }
            ]
        episodes = []
        for scene_data in scenes_data:
            if not isinstance(scene_data, Mapping):
                raise ValueError("each scene must be an object")
            host_facts = scene_data.get("host_facts", {})
            if not isinstance(host_facts, Mapping):
                raise ValueError("scene host_facts must be an object")
            episodes.append(
                SceneEpisode(
                    scene=SceneSetup(
                        scene_id=str(scene_data.get("scene_id", "")),
                        setting=str(scene_data.get("setting", "")),
                        opening=str(scene_data.get("opening", "")),
                        affordances=tuple(str(item) for item in scene_data.get("affordances", [])),
                        max_turns=int(scene_data.get("max_turns", 8)),
                    ),
                    host_facts={str(key): str(value) for key, value in host_facts.items()},
                    speaking_order=tuple(str(item) for item in scene_data.get("speaking_order", [])),
                )
            )
        actors = []
        for item in actors_data:
            if not isinstance(item, Mapping):
                raise ValueError("each actor must be an object")
            persona_data = item.get("persona")
            if not isinstance(persona_data, Mapping):
                raise ValueError("each actor requires a persona object")
            private_state = item.get("private_state", {})
            if not isinstance(private_state, Mapping):
                raise ValueError("actor private_state must be an object")
            actors.append(
                ActorSetup(
                    Persona.from_dict(persona_data),
                    goal=str(item.get("goal", "")),
                    relationship=str(item.get("relationship", "")),
                    private_state=dict(private_state),
                    disclosure=str(item.get("disclosure", "guarded")),
                )
            )
        return cls(
            actors=tuple(actors),
            episodes=tuple(episodes),
            disclosure=str(data.get("disclosure", "AI生成的虚构表演。")),
        )


_COMPILE_SYSTEM = """You convert a caller's free-form script, scene notes, and character documents into ONE strict JSON production spec for an improvisational NPC runtime. You translate and structure; you never invent plot beats the material does not support, and you never write dialogue.

Return exactly one JSON object:
{
  "scenes": [{"scene_id": "kebab-case", "setting": "...", "opening": "...", "affordances": ["..."], "max_turns": 8-24, "host_facts": {"O.<name>": "objective, publicly observable facts at THIS scene's start"}, "speaking_order": ["actor-id", "..."]}],
  "actors": [
    {
      "persona": {
        "id": "kebab-case", "name": "...", "gender": "", "age": "", "role": "",
        "background": "", "values": "", "preferences": "", "competencies": "",
        "desire": "", "line": "", "contradiction": "", "feared_truth": "",
        "soft_spot": "", "secret": "", "cognition_lens": "",
        "voice": {"entry_point": "", "ordering": "", "turn_shape": "", "interruption_recovery": "", "avoidance_pattern": "", "pressure_change": "", "failure_mode": "", "relationship_shifts": "", "signature_texture": "", "turn_economy": "", "output_language": "", "localization_rule": ""},
        "extensions": {}
      },
      "goal": "the character's OWN motive in their own terms — never the author's dramatic function",
      "relationship": "how this actor initially frames the others",
      "private_state": {},
      "disclosure": "closed|guarded|open"
    }
  ],
  "disclosure": "one-line AI-generated-fiction disclosure users will see"
}

Rules:
- Every field is filled from the caller's material or left as an empty string; missing identity is NOT inferred or invented.
- Persona voice fields describe HOW the person organizes speech (evidence from the material), never catchphrase lists to recite.
- goal is what the character would say they want; dramatic instructions like "conflict must escalate" belong nowhere.
- host_facts hold only what any observer could see or verify at the scene's start; secrets stay in persona.secret or private_state. Fact keys are semantic (O.visit_purpose, O.time_of_day), never actor names.
- speaking_order lists actor ids in rotation order for that scene; [] means simple alternation.
- One scene in the material = one entry in scenes, in story order. A single-scene script yields a one-element array. Later scenes' opening states what changed since the previous scene ended (time passed, location shift), without predetermining outcomes the actors have not played yet.
- If the material includes real-person register evidence (transcripts), place it under persona.extensions verbatim keys the caller used (register_license, speech_corpus, performance_reference); otherwise leave extensions {}.
- Output the JSON object only: no prose, no markdown."""


def compile_production(
    material: str,
    complete: Callable[[list[dict[str, str]], str], str],
    *,
    max_attempts: int = 3,
) -> ProductionSpec:
    """LLM translates caller material into a spec; validators gate admission."""
    if not material.strip():
        raise ValueError("material is required")
    feedback = ""
    for _ in range(max_attempts):
        messages = [
            {"role": "system", "content": _COMPILE_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {"material": material, "validation_feedback": feedback},
                    ensure_ascii=False,
                ),
            },
        ]
        raw = complete(messages, "production_compile")
        try:
            data = _extract_object(raw)
            return ProductionSpec.from_dict(data)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            feedback = f"{type(exc).__name__}: {exc}"
    raise ProductionCompileError(feedback or "compile produced no valid spec")


def perform(
    spec: ProductionSpec,
    generation: Callable[[list[dict[str, str]], str], str],
    *,
    review: Callable[[list[dict[str, str]], str], str] | None = None,
    review_lenses: tuple[str, ...] = ("dialogue", "character", "dramaturgy"),
    cognition_factory: Callable[[Any], Any] = JsonCognitionPort,
    performance_factory: Callable[[Any], Any] = JsonPerformancePort,
    on_turn: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    """Run every episode in order; actors carry standing intents and case notes across scenes.

    A protocol failure inside one scene ends THAT scene early (partial
    transcript kept, failure recorded) — remaining scenes still run. A
    multi-scene production must never silently collapse to its first scene.
    """
    carried_intents: dict[str, dict[str, Any]] = {}
    previous_close = ""
    performed_scenes: list[dict[str, Any]] = []
    scene_failures: list[str] = []
    for episode in spec.episodes:
        actors = spec.actors
        if previous_close:
            actors = tuple(
                ActorSetup(
                    item.persona,
                    item.goal,
                    item.relationship,
                    {**dict(item.private_state), "previous_scene": previous_close},
                    item.disclosure,
                )
                for item in spec.actors
            )
        host = InMemorySceneHost(
            episode.scene.scene_id,
            facts={"O.current": episode.scene.opening, **episode.host_facts},
            targets=tuple(item.persona.id for item in actors),
            capabilities=("speak", "wait", "interact", "exit"),
        )
        run = create_rehearsal(
            episode.scene,
            actors,
            host=host,
            cognition={item.persona.id: cognition_factory(generation) for item in actors},
            performance=performance_factory(generation),
        )
        run.standing_intents.update(
            {actor_id: dict(intent) for actor_id, intent in carried_intents.items()}
        )
        order = list(episode.speaking_order) or [item.persona.id for item in actors]
        transcript: list[dict[str, Any]] = []
        scene_failure = ""
        closed_early = ""
        for index in range(episode.scene.max_turns):
            actor_id = order[index % len(order)]
            try:
                result = run.advance(actor_id)
            except (CognitionModelError, PerformanceModelError) as exc:
                scene_failure = f"{episode.scene.scene_id}: {str(exc)[:400]}"
                break
            if result.draft is None:
                scene_failure = f"{episode.scene.scene_id}: turn produced no performance draft"
                break
            entry = asdict(result.draft)
            transcript.append(entry)
            host.facts["O.current"] = (
                f"上一位刚才：{(entry.get('speech') or entry.get('action', ''))[:180]}"
            )
            if on_turn is not None:
                on_turn(episode.scene.scene_id, index + 1, actor_id)
            if result.policy is not None and result.policy.disposition == "withdraw":
                closed_early = f"{actor_id} exited the scene (withdraw)"
                break
        performed_scenes.append(
            {
                "scene_id": episode.scene.scene_id,
                "setting": episode.scene.setting,
                "opening": episode.scene.opening,
                "turns": transcript,
                "protocol_failure": scene_failure,
                "closed_early": closed_early,
                "max_turns": episode.scene.max_turns,
            }
        )
        if scene_failure:
            scene_failures.append(scene_failure)
        carried_intents = {
            actor_id: dict(intent) for actor_id, intent in run.standing_intents.items()
        }
        spoken = [item for item in transcript if item.get("speech")]
        last = spoken[-1] if spoken else (transcript[-1] if transcript else {})
        if transcript:
            previous_close = (
                f"上一场（{episode.scene.setting[:60]}）结束时："
                f"{(last.get('speech') or last.get('action', '无人说话'))[:120]}"
            )

    public_scene = {
        "disclosure": spec.disclosure,
        "scenes": [
            {"scene_id": item["scene_id"], "setting": item["setting"], "opening": item["opening"]}
            for item in performed_scenes
        ],
        "characters": [
            {
                "anonymous_actor": item.persona.id,
                "role": item.persona.role,
                "voice": item.persona.voice.to_dict(),
                **{
                    key: item.persona.extensions[key]
                    for key in ("register_license", "speech_corpus")
                    if item.persona.extensions.get(key)
                },
            }
            for item in spec.actors
        ],
    }
    all_turns = [turn for item in performed_scenes for turn in item["turns"]]
    scenes_ok = all(
        not item["protocol_failure"]
        and (item["closed_early"] or len(item["turns"]) == item["max_turns"])
        for item in performed_scenes
    )
    document: dict[str, Any] = {
        "schema_version": "sceneactor-performance-service/1.1",
        "performance_id": f"perf:{uuid4().hex[:12]}",
        "disclosure": spec.disclosure,
        "scene": public_scene,
        "scenes": performed_scenes,
        "turns": all_turns,
        "completed": scenes_ok and len(performed_scenes) == len(spec.episodes),
        "protocol_failure": "; ".join(scene_failures),
        "scene_failures": scene_failures,
    }
    if review is not None and all_turns:
        reviewer = BlindReviewer(JsonBlindReviewPort(review), lenses=review_lenses)
        document["blind_review"] = reviewer.review(public_scene, all_turns)
    return document


def _extract_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("output contained no JSON object")
