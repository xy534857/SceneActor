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
class ProductionSpec:
    """Validated internal form of an external script + character docs."""

    scene: SceneSetup
    actors: tuple[ActorSetup, ...]
    host_facts: dict[str, str] = field(default_factory=dict)
    disclosure: str = "AI生成的虚构表演。"
    speaking_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.actors) < 2:
            raise ValueError("a production requires at least two actors")
        ids = [item.persona.id for item in self.actors]
        if len(set(ids)) != len(ids):
            raise ValueError("actor persona ids must be unique")
        unknown = set(self.speaking_order) - set(ids)
        if unknown:
            raise ValueError(f"speaking_order names unknown actors: {sorted(unknown)}")
        for key in self.host_facts:
            if not key.startswith("O."):
                raise ValueError(f"host fact keys must start with 'O.': {key}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": asdict(self.scene),
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
            "host_facts": dict(self.host_facts),
            "disclosure": self.disclosure,
            "speaking_order": list(self.speaking_order),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProductionSpec":
        scene_data = data.get("scene")
        actors_data = data.get("actors")
        if not isinstance(scene_data, Mapping) or not isinstance(actors_data, list):
            raise ValueError("spec requires a scene object and an actors array")
        scene = SceneSetup(
            scene_id=str(scene_data.get("scene_id", "")),
            setting=str(scene_data.get("setting", "")),
            opening=str(scene_data.get("opening", "")),
            affordances=tuple(str(item) for item in scene_data.get("affordances", [])),
            max_turns=int(scene_data.get("max_turns", 8)),
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
        host_facts = data.get("host_facts", {})
        if not isinstance(host_facts, Mapping):
            raise ValueError("host_facts must be an object")
        return cls(
            scene=scene,
            actors=tuple(actors),
            host_facts={str(key): str(value) for key, value in host_facts.items()},
            disclosure=str(data.get("disclosure", "AI生成的虚构表演。")),
            speaking_order=tuple(str(item) for item in data.get("speaking_order", [])),
        )


_COMPILE_SYSTEM = """You convert a caller's free-form script, scene notes, and character documents into ONE strict JSON production spec for an improvisational NPC runtime. You translate and structure; you never invent plot beats the material does not support, and you never write dialogue.

Return exactly one JSON object:
{
  "scene": {"scene_id": "kebab-case", "setting": "...", "opening": "...", "affordances": ["..."], "max_turns": 8-24},
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
  "host_facts": {"O.<name>": "objective, publicly observable scene facts only"},
  "disclosure": "one-line AI-generated-fiction disclosure users will see",
  "speaking_order": ["actor-id", "..."]
}

Rules:
- Every field is filled from the caller's material or left as an empty string; missing identity is NOT inferred or invented.
- Persona voice fields describe HOW the person organizes speech (evidence from the material), never catchphrase lists to recite.
- goal is what the character would say they want; dramatic instructions like "conflict must escalate" belong nowhere.
- host_facts hold only what any observer could see or verify at scene start; secrets stay in persona.secret or private_state.
- speaking_order lists actor ids in the order turns should rotate; leave [] for simple alternation.
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
    on_turn: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Run one full performance of the spec and return the public document."""
    host = InMemorySceneHost(
        spec.scene.scene_id,
        facts={"O.current": spec.scene.opening, **spec.host_facts},
        targets=tuple(item.persona.id for item in spec.actors),
        capabilities=("speak", "wait", "interact"),
    )
    run = create_rehearsal(
        spec.scene,
        spec.actors,
        host=host,
        cognition={item.persona.id: cognition_factory(generation) for item in spec.actors},
        performance=performance_factory(generation),
    )
    order = list(spec.speaking_order) or [item.persona.id for item in spec.actors]
    transcript: list[dict[str, Any]] = []
    protocol_failure = ""
    for index in range(spec.scene.max_turns):
        actor_id = order[index % len(order)]
        try:
            result = run.advance(actor_id)
        except (CognitionModelError, PerformanceModelError) as exc:
            protocol_failure = str(exc)[:500]
            break
        if result.draft is None:
            protocol_failure = "turn produced no performance draft"
            break
        entry = asdict(result.draft)
        transcript.append(entry)
        host.facts["O.current"] = (
            f"上一位刚才：{(entry.get('speech') or entry.get('action', ''))[:180]}"
        )
        if on_turn is not None:
            on_turn(index + 1, actor_id)

    public_scene = {
        "setting": spec.scene.setting,
        "opening": spec.scene.opening,
        "disclosure": spec.disclosure,
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
    document: dict[str, Any] = {
        "schema_version": "sceneactor-performance-service/1.0",
        "performance_id": f"perf:{uuid4().hex[:12]}",
        "disclosure": spec.disclosure,
        "scene": public_scene,
        "turns": transcript,
        "completed": not protocol_failure and len(transcript) == spec.scene.max_turns,
        "protocol_failure": protocol_failure,
    }
    if review is not None and transcript:
        reviewer = BlindReviewer(JsonBlindReviewPort(review), lenses=review_lenses)
        document["blind_review"] = reviewer.review(public_scene, transcript)
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
