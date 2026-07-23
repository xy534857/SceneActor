"""Meme ammo: arm any persona record with TemplateStudio meme grammars.

The forge builds WHO the character is (genome: how they think; style pack:
how they talk). This module supplies WHAT BITS they are licensed to play:
each ``meme_grammar.json`` (semantic-meme/1.1) compiles into one
``register_license`` entry — a PERMISSION with trigger and budget, never a
script. The rehearsal engine already knows the register_license contract
(cognition prompt: fire only on genuine trigger, stay in budget, regenerate
in this moment's words), so an armed record needs no engine change.

Directory layout is TemplateStudio's ``data/memes``: one folder per meme
containing ``meme_grammar.json``. Point the service at a synced snapshot
via ``--meme-dir`` / ``SCENEACTOR_MEME_DIR``; grammars are text-only
research derivatives (no reference media travels with them).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

MAX_AMMO = 3  # a 30s skit cannot carry an arsenal

_BUILTIN_BUDGET = "一场最多发动一次，发动后本场此梗关闭；只在触发情境真实出现时发动，绝不为发动而制造情境"


class MemeAmmoError(ValueError):
    """Missing or malformed meme grammar."""


def default_ammo_root() -> Path:
    env = os.environ.get("SCENEACTOR_MEME_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path("data/memes")


def _texts(value: Any) -> list[str]:
    return [str(x).strip() for x in (value if isinstance(value, (list, tuple)) else []) if str(x).strip()]


def _read_grammar(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MemeAmmoError(f"unreadable grammar {path.parent.name}: {exc}") from exc
    if not isinstance(value, dict) or not str(value.get("meme_id", "")).strip():
        raise MemeAmmoError(f"grammar {path.parent.name} has no meme_id")
    return value


def catalog(root: Path | None = None) -> list[dict]:
    """List armable memes: id, name, one-line core, tone register."""
    base = (root or default_ammo_root()).expanduser()
    if not base.is_dir():
        return []
    items: list[dict] = []
    for grammar_path in sorted(base.glob("*/meme_grammar.json")):
        try:
            g = _read_grammar(grammar_path)
        except MemeAmmoError:
            continue  # a broken package must not hide the rest
        tone = g.get("tone") if isinstance(g.get("tone"), Mapping) else {}
        items.append({
            "meme_id": str(g["meme_id"]),
            "dir": grammar_path.parent.name,
            "name": str(g.get("name", grammar_path.parent.name)),
            "aliases": _texts(g.get("aliases"))[:3],
            "category": str(g.get("category", "")),
            "core_text": str(g.get("core_text", ""))[:120],
            "register": str(tone.get("register", "")),
            "usage": str(g.get("usage_context", ""))[:160],
        })
    return items


def load_grammar(meme_id: str, root: Path | None = None) -> dict:
    """Find a grammar by meme_id or directory name."""
    base = (root or default_ammo_root()).expanduser()
    direct = base / meme_id / "meme_grammar.json"
    if direct.is_file():
        return _read_grammar(direct)
    for grammar_path in base.glob("*/meme_grammar.json"):
        try:
            g = _read_grammar(grammar_path)
        except MemeAmmoError:
            continue
        if str(g.get("meme_id", "")) == meme_id:
            return g
    raise MemeAmmoError(f"unknown meme {meme_id!r} under {base}")


def license_from_meme(grammar: Mapping[str, Any]) -> dict:
    """Compile one semantic-meme/1.1 grammar into a register_license dict.

    shape = the skeleton a variation must keep (core text, plot beats,
    anchors) plus the slots the character's own cognition refills;
    trigger = when it may fire; budget = per-scene cap plus hard bans.
    """
    meme_id = str(grammar["meme_id"]).strip()
    name = str(grammar.get("name", meme_id)).strip()
    core = str(grammar.get("core_text", "")).strip()
    plot = str(grammar.get("plot_pattern", "")).strip()
    anchors = [
        str(a.get("rule", "")).strip()
        for a in (grammar.get("fixed_anchors") or [])
        if isinstance(a, Mapping) and str(a.get("rule", "")).strip()
    ]
    slots = _texts(grammar.get("reuse_slots"))
    position = grammar.get("dialogue_position") if isinstance(grammar.get("dialogue_position"), Mapping) else {}
    turn_shape = str(position.get("turn_shape", "")).strip()

    shape_parts = [f"梗「{name}」"]
    if core:
        shape_parts.append(f"母式：{core}")
    if plot:
        shape_parts.append(f"剧构：{plot[:200]}")
    if anchors:
        shape_parts.append("不可丢的锚点：" + "；".join(anchors[:4]))
    if turn_shape:
        shape_parts.append(f"回合形状：{turn_shape}")
    if slots:
        shape_parts.append("自由槽位（用这个角色自己的认知透镜去填，绝不照抄原梗对象）：" + "、".join(slots[:4]))

    triggers = _texts(grammar.get("activation_conditions"))[:3]
    entry = str(position.get("entry", "")).strip()
    if entry:
        triggers.append(f"入场时机：{entry}")
    trigger = "；".join(triggers) if triggers else "当前情境自然满足此梗的母式时"

    bans = (_texts(grammar.get("do_not_use_when")) + _texts(grammar.get("invalid_variants")))[:4]
    budget = _BUILTIN_BUDGET + (("。硬禁：" + "；".join(bans)) if bans else "")

    return {
        "form": f"meme:{meme_id}",
        "shape": "。".join(shape_parts),
        "trigger": trigger,
        "budget": budget,
        "evidence": f"meme_grammar.json ({meme_id}, semantic_grammar {grammar.get('semantic_grammar_version', '?')})",
    }


def arm_record(record: dict, meme_ids: list[str], root: Path | None = None) -> dict:
    """Attach compiled meme licenses to a persona record (returns same dict).

    Stored under ``record["meme_ammo"]`` so the record stays a plain persona
    pack; ChatSession projects it into the runtime persona's
    ``register_license`` extension.
    """
    ids = [str(x).strip() for x in meme_ids if str(x).strip()]
    if not ids:
        return record
    if len(ids) > MAX_AMMO:
        raise MemeAmmoError(f"at most {MAX_AMMO} memes per character — a short skit cannot carry an arsenal")
    seen: set[str] = set()
    licenses: list[dict] = []
    names: list[str] = []
    for meme_id in ids:
        grammar = load_grammar(meme_id, root)
        lic = license_from_meme(grammar)
        if lic["form"] in seen:
            continue
        seen.add(lic["form"])
        licenses.append(lic)
        names.append(str(grammar.get("name", meme_id)))
    record["meme_ammo"] = {"names": names, "register_license": licenses}
    return record
