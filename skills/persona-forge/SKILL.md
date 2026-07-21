---
name: persona-forge
description: |
  从一手语料蒸馏"认知基因组"，并在因果约束下组合出新角色。
  两种用法：(1) 蒸馏——输入某人的一手语料（视频转录/访谈/直播逐字稿），产出经过
  schema 校验的 CognitiveGenome JSON；(2) 组合——从基因组库挑选多个基因组，
  按轴权重合成虚构新角色并编译为 SceneActor runtime Persona。
  触发词：「蒸馏XX的基因组」「抓一个XX」「合成角色」「组合人设」「persona forge」。
  不在用户只要普通人设文案时触发——那直接手写 persona pack 更快。
---

# Persona Forge · 认知基因组蒸馏与组合

> 原子能力不是随便拼接的，而是有机结合：性状是输入，行为定势是**推导结果**。

## 核心理念

借鉴 nuwa-skill 的蒸馏流程（多维一手调研→三重验证→诚实边界），但产物不是给
聊天助手的 SKILL.md，而是给 SceneActor runtime 的**结构化基因组**
（`src/sceneactor/genome.py::CognitiveGenome`）。差异：

| nuwa | persona-forge |
|---|---|
| 产出散文式 SKILL.md | 产出 schema 校验的 JSON 基因组 |
| 心智模型给人读 | 心智模型进 `cognition_lens` 驱动演员决策 |
| 单人蒸馏，不可组合 | 基因组可在因果约束下跨人组合 |
| Agentic Protocol（搜索工作流） | 剥离——演员在场景里不搜网页 |

**关键区分**：基因组只装"怎么想、怎么判断、怎么崩"。角色外壳（职业/年龄/背景）
和说话方式（voice）是场景相关的，蒸馏时不进基因组，组合时由使用者提供。

## 基因组 Schema（每个角色必抽满）

| 字段 | 数量 | 说明 |
|---|---|---|
| `trait_axes` | 9 轴 | 0-1 打分：control_need / uncertainty_tolerance / trust_propensity / risk_appetite / self_efficacy / autonomy_need / intimacy_need / status_sensitivity / empathy_reactivity。**每轴必须引语料原句作证据** |
| `core_models` | 3-7 | 心智模型：他看任何问题先过的筛子，每个 ≥2 条原句证据 |
| `heuristics` | 5-10 | 情境启发式：如果 X 则 Y，带证据 |
| `value_weights` | 权重和=1 | 价值排序，从行为反推而非从口号照抄 |
| `internal_conflicts` | 2-4 | 两股相反的力 + **外显行为特征**（冲突必须可演） |
| `emotion_triggers` | ≥1 | 触发 → 反应 → 升级路径（火气阶梯的原料） |
| `blind_spots` | ≥1 | 他看不见自己的什么。没有盲区的蒸馏不合格 |

## 执行流程

### Phase 1 · 语料准备
- **只收一手语料**：视频/直播逐字转录、访谈原文、本人长文。二手转述与网络金句
  一律拒收（编辑过的句子是海报，不是人）。
- 语料不足 400 行时降档：core_models 减至 3 个并标注"基于有限语料推测"。
- 中国人物信息源黑名单沿用 nuwa：知乎/公众号/百度百科永不使用。

### Phase 2 · 盲抽
把语料（带情绪节拍标注更佳）交给强模型，用 `genome.py` 顶部注释里的 schema
要求严格 JSON 输出。**盲抽原则：不给蒸馏模型任何已有人设**，抓出来的才是
语料里真有的东西。已验证案例：张老师 569 行转录盲抽，9 轴证据齐全，
`micromanager / gatekeeper / savior_burnout / brittle_performer` 四条因果规则
全部命中且与人工人设互相印证。

### Phase 3 · 校验入库
```python
from sceneactor.genome import CognitiveGenome
g = CognitiveGenome.from_dict(blind_json, genome_id="...", source="...",
                              source_real_person=True)  # 抛错即返工
```
- 计数不满足 schema → 回 Phase 2 补抽，不许手填凑数。
- 真人语料必须 `source_real_person=True`——组合期的披露义务靠它触发。

### Phase 4 · 组合（可选）
```python
from sceneactor.genome import compose_genomes, compile_persona
comp = compose_genomes("new-character", [
    (genome_a, {"self_efficacy": 2.0}),   # 轴权重：这个人的自信占两倍话语权
    (genome_b, {"status_sensitivity": 2.0}),
])
persona = compile_persona(comp, persona_id="...", name="...",
                          shell={"role": "...", "background": "..."},
                          voice={"turn_shape": "..."})
```
组合语义（不是拼贴）：
- **轴融合后重跑因果规则**——行为定势是推导出来的。两个"事必躬亲"的人
  组合后如果信任度被拉高，事必躬亲会**消失**；这正是有机结合的含义。
- **轴分歧 ≥0.45 不取平均**，升级为新角色的内在冲突（情境摇摆：压力下走
  高位反应，事后滑回低位）。人可以同时装下两股力，数字不行。
- 模型/启发式按规则文本去重，携带出处（`from: genome_id`），绝不改写。
- 价值权重归一化；盲区取并集（合成人继承所有源的看不见）。
- **≥2 个真人源 → 强制虚构合成披露**，编译进 persona extensions。

### Phase 5 · 验收
- 编译出的 Persona 必须过 `Persona.from_dict` 校验。
- 用一场短景排练 + 盲审十条生死题验收；重点看 `pressure_change`（来自
  emotion_triggers 的升级路径）是否演得出阶梯，而不是一步到位爆炸。

## 内置因果规则（`DEFAULT_RULES`）

| 规则 | 条件 | 推导定势 |
|---|---|---|
| micromanager | 高控制 + 低不确定容忍 + 低信任 | 事必躬亲 |
| expansionist | 高控制 + 高风险 + 高自我效能 | 扩张冒险 |
| approach_avoidance | 高自主 + 高亲密 | 接近-逃避关系张力 |
| gatekeeper | 高控制 + 低风险 + 高地位敏感 | 守门人心态 |
| savior_burnout | 高共情 + 高自我效能 + 低信任 | 救世主式包办 |
| brittle_performer | 高地位敏感 + 低不确定容忍 | 面子脆化 |

规则表可扩展：新增规则时给出条件、定势与行为注记（可演的动作，不是形容词）。

## 反模式黑名单

1. **随机抽标签拼人设**——没有因果推导的组合是塑料人。
2. 用二手金句喂蒸馏——抓到的是编辑的品味，不是本人。
3. 轴分歧取平均——把矛盾抹掉等于把戏剧性抹掉。
4. 把 voice/外壳塞进基因组——认知可迁移，职业头衔不可。
5. 多真人源合成不加披露——法律与伦理红线。
6. 盲区留空——所有人都有看不见的东西，抽不出来是蒸馏失败。
