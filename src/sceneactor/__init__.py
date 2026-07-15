"""SceneActor public contracts.

The package root exposes only host-independent NPC and transaction contracts.
Presentation adapters and concrete hosts are imported explicitly.
"""

from .contracts import (
    ActionCommand,
    ActionIntent,
    Appraisal,
    DecisionFrame,
    Delivery,
    EmotionChange,
    HostReceipt,
    ObservationView,
    PerformanceBeat,
    PerformanceDraft,
    PerformancePolicy,
    PublicPerformanceIntent,
    ResolvedOutcome,
)
from .events import EventLedger, JsonlEventStore, RuntimeEvent, TurnEventBatch, TurnEventFollowUp
from .persona import Persona, VoiceProfile
from .reducers import RuntimeState, SceneState, reduce_events
from .hosts import InMemorySceneHost
from .runtime import ActionCommandPort, CognitionPort, PerformancePort, TurnOrchestrator, TurnResult
from .model import FallbackModel, ModelAttempt, configured_fallback
from .performance import JsonPerformancePort, PerformanceModelError
from .governance import GovernanceFinding, scan_semantic_hardcode
from .rehearsal import ActorSetup, RehearsalRun, SceneSetup, create_rehearsal
from .review import BlindReviewer, BlindReview, StructuralIssue, StructuralValidator

__all__ = [
    "ActionCommand",
    "ActionIntent",
    "Appraisal",
    "DecisionFrame",
    "Delivery",
    "EmotionChange",
    "EventLedger",
    "HostReceipt",
    "JsonlEventStore",
    "ObservationView",
    "PerformanceBeat",
    "PerformanceDraft",
    "PerformancePolicy",
    "Persona",
    "PublicPerformanceIntent",
    "ResolvedOutcome",
    "RuntimeEvent",
    "RuntimeState",
    "SceneState",
    "TurnEventBatch",
    "TurnEventFollowUp",
    "VoiceProfile",
    "reduce_events",
    "ActionCommandPort",
    "ActorSetup",
    "BlindReviewer",
    "BlindReview",
    "CognitionPort",
    "FallbackModel",
    "GovernanceFinding",
    "InMemorySceneHost",
    "JsonPerformancePort",
    "ModelAttempt",
    "PerformanceModelError",
    "PerformancePort",
    "RehearsalRun",
    "SceneSetup",
    "StructuralIssue",
    "StructuralValidator",
    "TurnOrchestrator",
    "TurnResult",
    "configured_fallback",
    "create_rehearsal",
    "scan_semantic_hardcode",
]
