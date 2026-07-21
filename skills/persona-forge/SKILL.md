---
name: persona-forge
description: |
  nuwa-skill 的 runtime 升级版：完整继承其六维一手调研流程，把产物从散文式
  SKILL.md 升级为 schema 校验的"认知基因组"（CognitiveGenome JSON），
  并新增两层 nuwa 没有的能力：(1) 因果约束下的跨人组合——合成虚构新角色；
  (2) 编译为 SceneActor runtime Persona 直接上场表演。
  触发词：「蒸馏XX的基因组」「抓一个XX」「合成角色」「组合人设」「persona forge」。
  不在用户只要普通人设文案时触发——那直接手写 persona pack 更快。
---

# Persona Forge · 认知基因组蒸馏与组合

> nuwa 造镜子（用别人的眼睛看你的问题），forge 造演员（让别人的认知在场景里活动）。
> 原子能力不是随便拼接的，而是有机结合：性状是输入，行为定势是**推导结果**。

## 与 nuwa-skill 的关系：超集，不是替代

**继承（照单全收）**：六维调研、一手>二手的信息源权重、中文源黑名单
（知乎/公众号/百科永不使用）、"长文>金句、争议>共识、变化>固定"品味守则、
诚实边界（宁可 60 分诚实不要 90 分编造）、检查点不阻塞交付。

**升级（nuwa 没有的）**：

| nuwa | persona-forge |
|---|---|
| 产出散文式 SKILL.md，给人读 | 产出 schema 校验的 JSON 基因组，程序可组合 |
| 心智模型是文档章节 | 心智模型进 `cognition_lens` 驱动演员每回合决策 |
| 单人蒸馏，产物不可组合 | 基因组在因果规则下跨人组合出虚构新角色 |
| 表达 DNA 描述精修后的书面风格 | 另设口语层：逐字转录抓未删改的碎句/口癖/升级阶梯 |
| Agentic Protocol（回答前先搜索） | 剥离——演员在场景里不搜网页；认知封装进基因组 |

**关键区分**：基因组只装"怎么想、怎么判断、怎么崩"。角色外壳（职业/年龄/背景）
和说话方式（voice）是场景相关的，蒸馏时不进基因组，组合时由使用者提供。

## 基因组 Schema（每个角色必抽满）

| 字段 | 数量 | 说明 | 证据来源维度 |
|---|---|---|---|
| `trait_axes` | 9 轴 | 0-1 打分 + 引原句证据：control_need / uncertainty_tolerance / trust_propensity / risk_appetite / self_efficacy / autonomy_need / intimacy_need / status_sensitivity / empathy_reactivity | **02 对话**（压力场合优先） |
| `core_models` | 3-7 | 心智模型：看任何问题先过的筛子，每个 ≥2 条证据 | 01 著作 + 02 对话 |
| `heuristics` | 5-10 | 情境启发式：如果 X 则 Y | 01 + 02 + 05 决策 |
| `value_weights` | 权重和=1 | **从行为反推**，不从口号照抄 | **05 决策记录** |
| `internal_conflicts` | 2-4 | 两股相反的力 + 外显行为特征（必须可演） | **02×05 言行对照** |
| `emotion_triggers` | ≥1 | 触发 → 反应 → 升级路径 | 02 对话（即兴/被追问段） |
| `blind_spots` | ≥1 | 他看不见自己的什么 | **04 他者批评**（本人语料只能猜，外部批评才是证据） |
| `causal_rules`(可选) | 0-N | 此人特有的轴间因果链（声明式 spec） | 综合推导 |

## 执行流程

### Phase 1 · 六维采集（继承 nuwa，按基因组字段定向）

六个维度并行采集，每维注明喂哪个字段：

| 维度 | 内容 | 喂什么 | 硬要求 |
|---|---|---|---|
| 01 著作 | 书、长文、论文、长博客 | core_models, heuristics | 反复出现 ≥3 次的论点才算真信念 |
| 02 对话 | 播客/庭审/听证/辩论/财报会**逐字转录** | trait_axes, emotion_triggers, conflicts | ≥150 行原话、≥3 场合、**至少一个对抗性场合**（人被追问时才露真轴） |
| 03 碎片 | 推文/微博/短评 | voice 层（场景期用） | 区分精修输出与即兴输出 |
| 04 他者 | 严肃批评、传记、对手评价 | **blind_spots** | 负面占比不足 = 调研不合格（nuwa 原则） |
| 05 决策 | 关键决策的背景/结果、言行不一致案例 | **value_weights, conflicts** | 每个决策要有可验证的结果记录 |
| 06 时间线 | 转折点、立场变化、最近 12 个月 | 冲突成因、轴漂移标注 | 防过时 |

信息源纪律（继承 nuwa 全部黑名单与权重表）：用户提供的一手素材 > 本人著作/
逐字转录/决策记录 > 社交媒体/他人评价 > 二手转述。**网络金句合集一律拒收**
——编辑过的句子是海报，不是人（张老师案例的教训）。

### Phase 2 · 分维盲抽

把六维素材交给强模型抽基因组，**盲抽原则：不给任何已有人设**。输出规则：

- `trait_axes`/`emotion_triggers` 证据**只准引 02 的逐字原话**
- `blind_spots` 必须引 04 的外部批评（标注来源）
- `value_weights`/`internal_conflicts` 需 02×05 交叉印证（说的 vs 做的）
- 每条证据标注来源维度与等级：verbatim（逐字）/ documented（有记录）/ inferred（推断）
- 额外产出 `causal_rules`：此人身上观察到的轴间因果链，声明式 spec 格式
  `{"rule_id","description","when":{"axis":">=0.65",...},"disposition","behavioral_notes"}`

已验证案例：张雪峰 569 行转录盲抽，9 轴证据齐全，四条内置规则命中且与
人工人设互证。

### Phase 3 · 校验入库

```python
from sceneactor.genome import CognitiveGenome
g = CognitiveGenome.from_dict(blind_json, genome_id="...", source="...",
                              source_real_person=True)  # 抛错即返工
```
- 计数不满足 schema → 回 Phase 2 补抽，不许手填凑数。
- 真人语料必须 `source_real_person=True`——组合期的披露义务靠它触发。
- 语料 <400 行降档：core_models 减至 3 个并标注"基于有限语料推测"（nuwa 冷门人物策略）。

### Phase 4 · 组合（可选）

```python
from sceneactor.genome import compose_genomes, compile_persona, rules_from_specs
rules = rules_from_specs(person_specific_specs)   # 内置规则只是例子，蒸馏可自带新规则
comp = compose_genomes("new-character", [
    (genome_a, {"self_efficacy": 2.0}),   # 轴权重：这个人的自信占两倍话语权
    (genome_b, {"status_sensitivity": 2.0}),
], rules=rules)
persona = compile_persona(comp, persona_id="...", name="...",
                          shell={"role": "...", "background": "..."},
                          voice={"turn_shape": "..."})
```

组合语义（不是拼贴）：
- **轴融合后重跑因果规则**——行为定势是推导出来的。低信任者×高信任者合成后
  如果信任被拉高，"事必躬亲"会**消失**；这正是有机结合的含义。
- **规则库开放**：`DEFAULT_RULES` 六条只是示例；每次蒸馏都应产出此人特有的
  `causal_rules`，用 `rules_from_specs` 并入（同 id 覆盖内置）。
- **轴分歧 ≥0.45 不取平均**，升级为合成人的内在冲突（情境摇摆：压力下走
  高位反应，事后滑回低位）。人可以同时装下两股力，数字不行。
- 模型/启发式按规则文本去重，携带出处（`from: genome_id`），绝不改写。
- 价值权重归一化；盲区取并集（合成人继承所有源的看不见）。
- **≥2 个真人源 → 强制虚构合成披露**，编译进 persona extensions。

### Phase 5 · 验收

- 编译出的 Persona 必须过 `Persona.from_dict` 校验。
- 一场短景排练 + 盲审十条生死题验收；重点看 `pressure_change`（来自
  emotion_triggers 的升级路径）是否演得出阶梯，而不是一步到位爆炸。

## 内置因果规则（示例库，非全集）

| 规则 | 条件 | 推导定势 |
|---|---|---|
| micromanager | 高控制 + 低不确定容忍 + 低信任 | 事必躬亲 |
| expansionist | 高控制 + 高风险 + 高自我效能 | 扩张冒险 |
| approach_avoidance | 高自主 + 高亲密 | 接近-逃避关系张力 |
| gatekeeper | 高控制 + 低风险 + 高地位敏感 | 守门人心态 |
| savior_burnout | 高共情 + 高自我效能 + 低信任 | 救世主式包办 |
| brittle_performer | 高地位敏感 + 低不确定容忍 | 面子脆化 |

**这张表是种子不是天花板**：每次蒸馏产出的人物专属 causal_rules 经
`rules_from_specs` 并入；积累多了，规则库本身就是跨人物的行为科学资产。

## 反模式黑名单

1. **随机抽标签拼人设**——没有因果推导的组合是塑料人。
2. 用二手金句喂蒸馏——抓到的是编辑的品味，不是本人。
3. 从本人语料猜盲区——盲区的证据只能来自 04 他者批评。
4. 从口号抄价值权重——权重的证据只能来自 05 决策记录。
5. 轴分歧取平均——把矛盾抹掉等于把戏剧性抹掉。
6. 把 voice/外壳塞进基因组——认知可迁移，职业头衔不可。
7. 多真人源合成不加披露——法律与伦理红线。
8. 跳过对抗性场合语料——演讲稿是公关部的轴，被追问时的话才是真轴。
