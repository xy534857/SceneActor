"""Forge custom personas: questionnaire synthesis and library fusion.

Two entry points, both returning a record dict in the same shape as
``data/genomes/records/*.json`` so ChatSession can consume it unchanged:

- ``forge_from_questionnaire``: user answers situational questions that pin
  the 9 trait axes plus identity/style/values; one model call synthesizes the
  qualitative layers (mental models, heuristics, conflicts, triggers, blind
  spots) consistent with those axes.
- ``forge_from_fusion``: weighted causal composition of existing records via
  ``compose_genomes`` — deterministic, no model call.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable, Mapping, Sequence

from sceneactor.genome import (
    TRAIT_AXES,
    CognitiveGenome,
    GenomeError,
    compose_genomes,
)

AXIS_LABELS = {
    "control_need": "控制欲",
    "uncertainty_tolerance": "不确定容忍",
    "trust_propensity": "信任倾向",
    "risk_appetite": "风险偏好",
    "self_efficacy": "自我效能",
    "autonomy_need": "自主需求",
    "intimacy_need": "亲密需求",
    "status_sensitivity": "地位敏感",
    "empathy_reactivity": "共情反应",
}

# Axis order for the adaptive questionnaire: outward behaviors first so early
# answers give the question generator the most signal for later scenes.
QUESTION_ORDER = (
    "control_need",
    "risk_appetite",
    "trust_propensity",
    "status_sensitivity",
    "empathy_reactivity",
    "uncertainty_tolerance",
    "self_efficacy",
    "autonomy_need",
    "intimacy_need",
)

_QUESTION_PROMPT = """你是角色问卷设计师。用户正在通过情境选择题捏一个虚构人物，你负责出下一道题。

人物设定：
- 姓名：{name}
- 身份背景：{background}
- 说话风格：{style}

已答题目（题干 → 用户的选择）：
{history_block}

现在出第 {step} 题（共9题），本题测量维度：{axis}（{axis_label}）。
定义参考：{axis_hint}

出题要求：
1. 情境必须贴合这个人物的身份和生活场景（不要通用职场题），并尽量与已答题目形成连续叙事——可以引用前面选择造成的后果。
2. 给4个选项，从该维度低位到高位排列，行为化描述（他会怎么做/怎么说），不要形容词自评。
3. 每个选项附一个0-1分数，大致均匀分布（如0.15/0.4/0.7/0.9），顺序可以打乱。
4. 选项之间要真正难选——每个都得像"这个人物可能会这么干"，不要有明显的社会期望答案。

只输出JSON：
{{"title":"题干（一句话情境+问题）","options":[{{"text":"选项描述","score":0.15}},{{"text":"","score":0.4}},{{"text":"","score":0.7}},{{"text":"","score":0.9}}]}}"""

_AXIS_HINTS = {
    "control_need": "多大程度要把事情抓在自己手里；放权还是事必躬亲",
    "uncertainty_tolerance": "面对没有答案的处境能否继续行动；要保证还是能边走边看",
    "trust_propensity": "默认把人当可靠还是当风险；授权习惯",
    "risk_appetite": "赔率面前下多大注；求稳还是搏大",
    "self_efficacy": "相信凭自己能不能成事；遇难题先信自己还是先降预期",
    "autonomy_need": "多讨厌被安排；自己的路必须自己选的程度",
    "intimacy_need": "需要多深的私人联结；深关系是安全感还是负担",
    "status_sensitivity": "面子和位置被冒犯时的在意程度",
    "empathy_reactivity": "他人情绪多大程度立刻改变自己的语言和行动",
}


def next_question(
    *,
    name: str,
    background: str,
    style: str,
    history: Sequence[Mapping[str, str]],
    complete: Callable[[list[dict[str, str]], str], str],
) -> dict:
    """Generate the next situational question, conditioned on prior answers.

    ``history`` is a list of {"title": ..., "choice": ...} for answered steps.
    Returns {"axis", "step", "title", "options": [{"text", "score"}, ...]}.
    """
    step = len(history)
    if step >= len(QUESTION_ORDER):
        raise GenomeError("questionnaire already complete")
    axis = QUESTION_ORDER[step]
    history_block = "\n".join(
        f"{i + 1}. {h.get('title', '')} → 选了「{h.get('choice', '')}」"
        for i, h in enumerate(history)
    ) or "（这是第一题）"
    prompt = _QUESTION_PROMPT.format(
        name=name.strip() or "未命名", background=background.strip() or "普通人",
        style=style.strip() or "自然口语", history_block=history_block,
        step=step + 1, axis=axis, axis_label=AXIS_LABELS[axis],
        axis_hint=_AXIS_HINTS[axis],
    )
    for _ in range(3):
        output = complete([{"role": "user", "content": prompt}], "forge-question")
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        options = data.get("options", [])
        if not (isinstance(options, list) and len(options) == 4 and data.get("title")):
            continue
        try:
            clean = [
                {"text": str(o["text"]).strip(), "score": max(0.0, min(1.0, float(o["score"])))}
                for o in options
            ]
        except (KeyError, TypeError, ValueError):
            continue
        if any(not o["text"] for o in clean):
            continue
        return {"axis": axis, "step": step, "total": len(QUESTION_ORDER),
                "title": str(data["title"]).strip(), "options": clean}
    raise GenomeError("question generation failed after 3 attempts")

_SYNTHESIS_PROMPT = """你是角色设计师。用户通过问卷捏了一个虚构人物，你要把问卷结果补全为完整的认知基因组。

人物设定：
- 姓名：{name}
- 身份背景：{background}
- 说话风格/口头禅：{style}
- 核心价值观（按重要度）：{values}
- 最受不了的事：{fear}

九轴分数（0-1，问卷测得，你必须严格遵守，不得改动）：
{axes_block}

问卷答题记录（情境 → 他的选择，这是此人行为方式的第一手样本，qualitative层要与之呼应）：
{history_block}

生成与这些轴分**因果一致**的其余层。硬要求：
- core_models 3-5个：这个人理解世界的筛子，每个有 name 和 rule（一句话规则）
- heuristics 5-8条：具体情境的 if-then 条件反射（condition/action），要能从轴分推出来
- internal_conflicts 2-3个：两股内在力量的对抗 + 可观察的行为特征（force_a/force_b/behavioral_signature）
- emotion_triggers 2-3个：什么话/事点炸他（trigger/reaction/escalation），必须涵盖用户写的"最受不了的事"
- blind_spots 2-4条：这个人看不见自己什么（从高分轴的阴影面推导）
所有文本用中文，具体、口语化、可表演，不要人格测试报告腔。

只输出JSON：
{{"core_models":[{{"name":"","rule":""}}],"heuristics":[{{"condition":"","action":""}}],"internal_conflicts":[{{"force_a":"","force_b":"","behavioral_signature":""}}],"emotion_triggers":[{{"trigger":"","reaction":"","escalation":""}}],"blind_spots":[""]}}"""


def _validate_record(record: dict) -> None:
    CognitiveGenome.from_dict(
        record["genome"], genome_id=record["person_id"],
        source=record["genome"].get("source", record["person_id"]),
    )


def forge_from_questionnaire(
    *,
    name: str,
    background: str,
    style: str,
    values: Sequence[str],
    fear: str,
    axes: Mapping[str, float],
    complete: Callable[[list[dict[str, str]], str], str],
    history: Sequence[Mapping[str, str]] = (),
) -> dict:
    """One model call fills the qualitative genome layers around fixed axes."""
    if not name.strip():
        raise GenomeError("name is required")
    clean_axes: dict[str, float] = {}
    for axis in TRAIT_AXES:
        try:
            score = float(axes[axis])
        except (KeyError, TypeError, ValueError) as exc:
            raise GenomeError(f"axis {axis} missing or invalid") from exc
        if not 0.0 <= score <= 1.0:
            raise GenomeError(f"axis {axis}={score} out of [0,1]")
        clean_axes[axis] = round(score, 3)

    value_list = [v.strip() for v in values if v.strip()][:4] or ["真实"]
    # Rank-weighted, normalized: first value matters most.
    raw = {v: 1.0 / (i + 1) for i, v in enumerate(value_list)}
    total = sum(raw.values())
    value_weights = {k: round(w / total, 4) for k, w in raw.items()}

    axes_block = "\n".join(
        f"- {axis} {AXIS_LABELS[axis]}: {score}" for axis, score in clean_axes.items()
    )
    history_block = "\n".join(
        f"{i + 1}. {h.get('title', '')} → 「{h.get('choice', '')}」"
        for i, h in enumerate(history)
    ) or "（未提供）"
    prompt = _SYNTHESIS_PROMPT.format(
        name=name.strip(), background=background.strip() or "不详",
        style=style.strip() or "自然口语", values="、".join(value_list),
        fear=fear.strip() or "未提供", axes_block=axes_block,
        history_block=history_block,
    )

    data = None
    for _ in range(3):
        output = complete([{"role": "user", "content": prompt}], "forge")
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            continue
        try:
            data = json.loads(match.group(0))
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        raise GenomeError("model returned no parseable synthesis JSON")

    person_id = f"custom-{uuid.uuid4().hex[:8]}"
    genome = {
        "source": f"{person_id}-questionnaire",
        "trait_axes": {
            axis: {"score": score, "evidence": "问卷自评"}
            for axis, score in clean_axes.items()
        },
        "core_models": data.get("core_models", [])[:5],
        "heuristics": data.get("heuristics", [])[:8],
        "value_weights": value_weights,
        "internal_conflicts": data.get("internal_conflicts", [])[:3],
        "emotion_triggers": data.get("emotion_triggers", [])[:3],
        "blind_spots": data.get("blind_spots", [])[:4],
        "causal_rules": [],
    }
    record = {
        "person_id": person_id,
        "display_name": name.strip(),
        "name_en": "",
        "region": "CN",
        "industries": [background.strip()[:24] or "自定义角色"],
        "gender": "unknown",
        "living": True,
        "status": "custom",
        "library": "custom",
        "forge": {"mode": "questionnaire", "style": style.strip(), "fear": fear.strip()},
        "genome": genome,
    }
    _validate_record(record)
    return record


_ABSTRACT_PROMPT = """你是网络抽象角色设计师。参照系不是情景喜剧，是直播事故、精神状态离谱的博主、
民间怪人访谈——那种"这人是真的吗"的观感。你要造的不是"有怪癖的正常人"，是一个
**活在自己那套完整平行系统里的人**。他在自己的系统里逻辑严密、极度认真、甚至有尊严；
好笑是旁人撞进他的系统时产生的，他本人永远不觉得哪里好笑。

人物设定：
- 姓名：{name}
- 身份外壳：{background}
- 执念原料：{obsession}
- 触发词：{trigger_hint}

第一步【执念升维】——用户给的执念原料通常太老实，你必须先把它推到荒诞级：
不是"爱干净"，是"认为自己负责的区域每样东西都有编制，挪动=编制变动，需要走流程"；
不是"怕吵"，是"用分贝给人分种姓"；不是"节俭"，是"认为花出去的每一分钱都在外面流浪，要接它们回家"。
升维标准：这个执念必须蕴含一整套**平行制度**（有单位、有等级、有程序、有奖惩），
而不只是一种偏好。把升维后的版本写进 obsession_statement。

第二步【语言殖民】——执念必须占领他的说话方式，这是抽象感的主要来源：
- **私有度量衡**：他用自己发明的单位/等级量化一切（"这个动静，四级半""你这属于编外物品"）
- **强制翻译**：别人说的正常话，他当场翻译进自己的系统再回应——他不是没听懂，
  是他的系统里没有你那个说法
- **公文腔错位**：用极正式/专业/程序性的语言处理鸡毛蒜皮（立案、审批、公示、追责）
  或者反过来用市井话处理宏大概念——错位方向选一种，贯彻到底
- speech_dna 字段：8-12条他的原话样本，每条都必须带上述特征，禁止普通人说得出的句子

第三步【三秒可读】——短视频法则，陌生观众三秒内必须察觉这人系统有问题：
- opening_moves 字段：3-5个"开场即故障"动作（他出场第一件事干什么），每个都要怪得
  具体且无需上下文（掏出卷尺量客人和柜台的距离并记录；对着空椅子点名）
- 所有 heuristics 的 action 必须是**一眼可见的行为**，其中至少3条纯身体动作

第四步【结构公理】（沿用，必须全部满足）：
1. 范畴故障：执念系统和他的正业在他脑内是同一件事，core_models 写成他的世界观
2. 单一秩序：value_weights 执念项 ≥0.5
3. 压力向内：emotion_triggers 的 escalation 是更深钻进系统（启动更高级的程序），绝不爆发
4. 绝不自知：blind_spots 必含"意识不到系统在别人眼里是发疯"
5. 九轴极端化：至少3轴 ≥0.9 或 ≤0.1，方向从执念推导

禁令：禁止人格报告腔；禁止解释笑点；禁止"温和的怪癖"（可爱化=报废）；
禁止让他说任何一句正常人际客套话——客套话也要过他的系统。

数量：core_models 3-5；heuristics 5-8；internal_conflicts 2-3；emotion_triggers 2-3；
blind_spots 2-4；speech_dna 8-12；opening_moves 3-5。

只输出JSON：
{{"trait_axes":{{"control_need":0.0,"uncertainty_tolerance":0.0,"trust_propensity":0.0,"risk_appetite":0.0,"self_efficacy":0.0,"autonomy_need":0.0,"intimacy_need":0.0,"status_sensitivity":0.0,"empathy_reactivity":0.0}},
"core_models":[{{"name":"","rule":""}}],
"heuristics":[{{"condition":"","action":""}}],
"value_weights":{{"执念系统":0.6,"次要":0.25,"再次":0.15}},
"internal_conflicts":[{{"force_a":"","force_b":"","behavioral_signature":""}}],
"emotion_triggers":[{{"trigger":"","reaction":"","escalation":""}}],
"blind_spots":[""],
"speech_dna":["他的原话样本，带私有度量衡/强制翻译/公文腔错位"],
"opening_moves":["开场即故障的动作"],
"obsession_statement":"升维后的执念，用他自己的口吻说出来（他觉得天经地义的版本）"}}"""


def forge_from_obsession(
    *,
    name: str,
    background: str,
    obsession: str,
    trigger_hint: str = "",
    style: str = "",
    complete: Callable[[list[dict[str, str]], str], str],
) -> dict:
    """Forge an abstract comedy persona organized around one private order.

    Unlike questionnaire mode (realistic, axes measured first), the obsession
    IS the organizing principle: axes, models, heuristics, conflicts and blind
    spots are all derived from it, deliberately extreme and category-broken.
    The character never knows it is funny — that contract lives in the
    genome's structure (blind spots + escalation direction), not in tone.
    """
    if not name.strip():
        raise GenomeError("name is required")
    if not obsession.strip():
        raise GenomeError("obsession is required — an abstract persona without one is just a persona")
    prompt = _ABSTRACT_PROMPT.format(
        name=name.strip(), background=background.strip() or "普通人",
        obsession=obsession.strip(), trigger_hint=trigger_hint.strip() or "由你从执念推导",
    )

    data = None
    for _ in range(3):
        output = complete([{"role": "user", "content": prompt}], "forge-abstract")
        match = re.search(r"\{.*\}", output, re.DOTALL)
        if not match:
            continue
        try:
            candidate = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        axes = candidate.get("trait_axes", {})
        try:
            clean_axes = {a: max(0.0, min(1.0, float(axes[a]))) for a in TRAIT_AXES}
        except (KeyError, TypeError, ValueError):
            continue
        extreme = sum(1 for s in clean_axes.values() if s >= 0.88 or s <= 0.12)
        weights = candidate.get("value_weights", {})
        try:
            top_weight = max(float(w) for w in weights.values()) if weights else 0.0
        except (TypeError, ValueError):
            continue
        dna = [str(x).strip() for x in candidate.get("speech_dna", []) if str(x).strip()]
        moves = [str(x).strip() for x in candidate.get("opening_moves", []) if str(x).strip()]
        # Reject tame outputs: abstraction is a structural property, not a vibe.
        # A fault machine without colonized speech or cold-open faults is just a quirk.
        if extreme < 3 or top_weight < 0.5 or len(dna) < 6 or len(moves) < 3:
            continue
        data = candidate
        clean = clean_axes
        break
    if data is None:
        raise GenomeError("model kept returning a realistic persona; abstract forge needs extreme axes and one dominant value")

    total = sum(float(w) for w in data["value_weights"].values())
    value_weights = {str(k): round(float(w) / total, 4) for k, w in data["value_weights"].items()}

    person_id = f"custom-{uuid.uuid4().hex[:8]}"
    obsession_line = str(data.get("obsession_statement", "")).strip() or obsession.strip()
    speech_dna = dna[:12]
    opening_moves = moves[:5]
    # The obsession must colonize the voice: unless the user hand-wrote a style,
    # compile speech_dna + statement into the speech_style directive ChatSession reads.
    final_style = style.strip() or (
        f"执念系统总纲：「{obsession_line}」。所有话都从这套系统内部说出——"
        f"正常话题也要强制翻译成系统术语，绝不说系统外的客套话。原话样本（学它们的构词法和"
        f"度量衡，按语境自造新句，不逐字复读）：" + "；".join(f"「{s}」" for s in speech_dna)
    )
    genome = {
        "source": f"{person_id}-obsession",
        "trait_axes": {
            axis: {"score": round(score, 3), "evidence": f"执念推导：{obsession.strip()[:60]}"}
            for axis, score in clean.items()
        },
        "core_models": data.get("core_models", [])[:5],
        "heuristics": data.get("heuristics", [])[:8],
        "value_weights": value_weights,
        "internal_conflicts": data.get("internal_conflicts", [])[:3],
        "emotion_triggers": data.get("emotion_triggers", [])[:3],
        "blind_spots": data.get("blind_spots", [])[:4],
        "causal_rules": [],
    }
    record = {
        "person_id": person_id,
        "display_name": name.strip(),
        "name_en": "",
        "region": "CN",
        "industries": [background.strip()[:24] or "抽象角色"],
        "gender": "unknown",
        "living": True,
        "status": "custom",
        "library": "custom",
        "forge": {
            "mode": "obsession",
            "style": final_style,
            "obsession": obsession.strip(),
            "obsession_statement": obsession_line,
            "trigger_hint": trigger_hint.strip(),
            "speech_dna": speech_dna,
            "opening_moves": opening_moves,
        },
        "genome": genome,
    }
    _validate_record(record)
    return record


_STYLE_PACK_FORMAT = """只输出JSON，结构：
{{"tone":"整体语气与节奏（语速/停顿/音量起伏，40字内）",
"catchphrases":[{{"phrase":"口头禅原文","when":"什么情境下说"}}],
"sentence_patterns":["标志性句式，如：他以为…其实我…"],
"metaphor_domains":["打比方的素材域，如：老游戏、修车、做饭"],
"registers":{{"得意时":"怎么说话","被质疑时":"","尴尬时":"","认真讲道理时":"","跟熟人闲扯时":""}},
"taboos":["这个人绝不会有的说话方式"]}}

数量要求：catchphrases 12-20条（每条注明使用情境，这是风格的骨架，宁多勿少）；sentence_patterns 5-8条；metaphor_domains 3-5个；registers 五个场合都要写；taboos 2-4条。"""

_EXPAND_STYLE_PROMPT = """你是台词风格设计师。用户用自然语言描述了想要的角色说话风格，你把它展开成完整的结构化风格包。

用户的描述：{description}
角色设定：{name}，{background}。

要求：
1. 如果描述里引用了公众人物（如"像大司马"、"郭德纲那种"），充分调用你对此人说话习惯的了解：把他的口头禅库尽量完整地列出来（化用不照搬——保留结构和味道，替换专属指纹词），句式、语气节奏、招牌梗、分场合的说话方式都要覆盖。
2. 严格执行用户的修正语（如"但没那么否定性"、"更温和"）——被点名去掉的特质一条都不能留，涉及的口头禅要么删掉要么改写成符合修正后人格的版本。

{format_block}"""


def render_style_pack(pack: Mapping[str, Any]) -> str:
    """Render a structured style pack into a voice directive for the actor."""
    lines: list[str] = []
    if pack.get("tone"):
        lines.append(f"语气节奏：{pack['tone']}")
    phrases = pack.get("catchphrases") or []
    if phrases:
        lines.append("口头禅库（按情境自然取用，一次发言最多1-2条，不要堆砌）：")
        lines += [f"·「{p.get('phrase', '')}」—— {p.get('when', '')}" for p in phrases]
    if pack.get("sentence_patterns"):
        lines.append("标志性句式：" + "；".join(pack["sentence_patterns"]))
    if pack.get("metaphor_domains"):
        lines.append("打比方素材域：" + "、".join(pack["metaphor_domains"]))
    registers = pack.get("registers") or {}
    if registers:
        lines.append("分场合：" + "；".join(f"{k}→{v}" for k, v in registers.items() if v))
    if pack.get("taboos"):
        lines.append("绝不：" + "；".join(pack["taboos"]))
    return "\n".join(lines)


def _parse_style_pack(output: str) -> dict | None:
    match = re.search(r"\{.*\}", output, re.DOTALL)
    if not match:
        return None
    try:
        pack = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(pack, dict) or len(pack.get("catchphrases") or []) < 8:
        return None
    pack["catchphrases"] = pack["catchphrases"][:20]
    return pack


def expand_style(
    *,
    description: str,
    name: str,
    background: str,
    complete: Callable[[list[dict[str, str]], str], str],
) -> dict:
    """Expand a natural-language style wish into a structured style pack."""
    if not description.strip():
        raise GenomeError("style description is required")
    prompt = _EXPAND_STYLE_PROMPT.format(
        description=description.strip(),
        name=name.strip() or "未命名", background=background.strip() or "不详",
        format_block=_STYLE_PACK_FORMAT,
    )
    for _ in range(3):
        pack = _parse_style_pack(complete([{"role": "user", "content": prompt}], "forge-style-expand"))
        if pack:
            return {"pack": pack, "text": render_style_pack(pack)}
    raise GenomeError("style expansion failed after 3 attempts")


_STYLE_PROMPT = """你是台词风格设计师。一个虚构角色由以下人物按权重融合而成：
{sources_block}

角色设定：{name}，{background}。

按权重合成这个角色的完整风格包：
1. 口头禅库按权重从各源继承——权重60%的源贡献约60%的条目；化用不照搬（保留结构和味道，替换专属指纹词）。
2. 句式、比喻域、分场合说话方式同样按权重混合；两个源冲突时高权重者胜，但低权重源至少留下可辨认的痕迹。

{format_block}"""


def _fuse_style(
    parts: Sequence[tuple[dict, float]],
    name: str,
    background: str,
    complete: Callable[[list[dict[str, str]], str], str],
) -> tuple[dict | None, str]:
    sources_block = "\n".join(
        f"- {r['display_name']}（权重{w:.0%}）"
        + (f"，已知风格：{json.dumps((r.get('forge') or {}).get('style_pack'), ensure_ascii=False)[:800]}"
           if (r.get('forge') or {}).get('style_pack') else "")
        for r, w in parts
    )
    prompt = _STYLE_PROMPT.format(
        sources_block=sources_block, name=name, background=background or "不详",
        format_block=_STYLE_PACK_FORMAT,
    )
    try:
        for _ in range(2):
            pack = _parse_style_pack(complete([{"role": "user", "content": prompt}], "forge-style"))
            if pack:
                return pack, render_style_pack(pack)
    except Exception:  # noqa: BLE001 — style is enhancement, never fatal
        pass
    return None, ""


def forge_from_fusion(
    *,
    name: str,
    parts: Sequence[tuple[dict, float]],
    background: str = "",
    style: str = "",
    complete: Callable[[list[dict[str, str]], str], str] | None = None,
) -> dict:
    """Compose 2-3 existing records into a fictional composite record."""
    if not name.strip():
        raise GenomeError("name is required")
    if not 2 <= len(parts) <= 3:
        raise GenomeError("fusion needs 2-3 source personas")

    genome_parts = []
    for record, weight in parts:
        genome = CognitiveGenome.from_dict(
            record["genome"], genome_id=record["person_id"],
            source=record.get("display_name", record["person_id"]),
            source_real_person=record.get("library") != "custom",
        )
        axis_weights = {axis: max(0.05, float(weight)) for axis in TRAIT_AXES}
        genome_parts.append((genome, axis_weights))

    person_id = f"custom-{uuid.uuid4().hex[:8]}"
    composite = compose_genomes(person_id, genome_parts)

    genome = {
        "source": f"{person_id}-fusion",
        "trait_axes": {
            axis: {
                "score": score,
                "evidence": "融合自 " + " + ".join(r["display_name"] for r, _ in parts),
            }
            for axis, score in composite["trait_axes"].items()
        },
        "core_models": [
            {"name": m["name"], "rule": m["rule"], "evidence": [f"承自{m['from']}"]}
            for m in composite["core_models"]
        ][:7],
        "heuristics": [
            {"condition": h["if"], "action": h["then"], "evidence": f"承自{h['from']}"}
            for h in composite["heuristics"]
        ][:10],
        "value_weights": composite["value_weights"],
        "internal_conflicts": [
            {"force_a": c["force_a"], "force_b": c["force_b"],
             "behavioral_signature": c["behavioral_signature"]}
            for c in composite["internal_conflicts"]
        ][:4],
        "emotion_triggers": [
            {"trigger": t["trigger"], "reaction": t["reaction"],
             "escalation": t.get("escalation", "")}
            for t in composite["emotion_triggers"]
        ],
        "blind_spots": composite["blind_spots"],
        "causal_rules": [],
    }
    sources = [r["display_name"] for r, _ in parts]
    final_style = style.strip()
    style_pack = None
    if not final_style and complete is not None:
        style_pack, final_style = _fuse_style(parts, name.strip(), background.strip(), complete)
    record = {
        "person_id": person_id,
        "display_name": name.strip(),
        "name_en": "",
        "region": "CN",
        "industries": [background.strip()[:24] or ("融合：" + "×".join(sources))[:24]],
        "gender": "unknown",
        "living": True,
        "status": "custom",
        "library": "custom",
        "forge": {
            "mode": "fusion",
            "style": final_style,
            "style_pack": style_pack,
            "sources": [
                {"person_id": r["person_id"], "display_name": r["display_name"], "weight": w}
                for r, w in parts
            ],
            "disclosure": composite["provenance"].get("required_disclosure", ""),
            "derived_dispositions": composite["derived_dispositions"],
        },
        "genome": genome,
    }
    _validate_record(record)
    return record
