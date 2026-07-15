# SceneActor 架构设计

> 状态：Draft for Review  
> 日期：2026-07-15  
> 目标读者：产品、AI/NPC Runtime、游戏集成、视频生成与评测工程师

## 0. 摘要

SceneActor 的核心产品不是 AI 视频生成，而是 **AI NPC 的主观决策与表演运行时**。

系统接收人物设定、客观场景和当前运行状态，使 NPC 在严格知情边界内完成：

1. 感知眼前事实；
2. 形成带人格、关系和情绪偏差的主观理解；
3. 在目标、职责、价值、恐惧和现实阻力之间做选择；
4. 向场景 Host 提交行动意图；
5. 根据 Host 返回的客观结果，生成台词、动作、沉默、视线、声音与身体余震；
6. 将已提交经历沉淀为关系、情绪和记忆连续性。

Seedance 是当前用于展示 NPC 表演的 Presentation Adapter。未来游戏引擎、实时语音、动画状态机和交互式场景是其他 Adapter。任何渲染媒介都不能反向决定 NPC 的人格、选择或情绪弧线。

```text
稳定人格 + 主观状态 + 客观观察 + 可见历史
                         │
                         ▼
                 Appraisal + Policy U
                         │
                         ▼
               Scene Host resolves X
                         │
                         ▼
             Performance Realization Y
                         │
                         ▼
            Commit Event + Continuity State
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
      Seedance Adapter      Game Presentation Adapter
      分镜/音画展示             表情/凝视/语音/UI
```

---

## 1. 产品北极星

### 1.1 核心命题

SceneActor 要回答的问题是：

> 同一个客观事件进入这个 NPC 的主观世界后，会变成什么；这个人因此会做什么；观众如何从可见表演中认出“只能是这个人”。

系统质量首先由 NPC 表现衡量，而不是视频画质衡量。

### 1.2 核心能力

- **主观个性**：人物先注意什么、漏掉什么、如何判断，由稳定人格和当下处境共同决定。
- **知情边界**：人物不能读取作者真相、未来剧情、他人内心或未观察到的事实。
- **情绪连续性**：情绪来自事件评价并跨拍持续；强度体现为原有能力受损，而不是标签或音量。
- **行为一致性**：人物跨场景保持欲望、底线、关系史、表达习惯和身体惯性。
- **真实选择**：人物可以笨、错、迟疑、回避或放弃；不能被优化成总是正确的助手。
- **剧情推动**：人物主动制造条件、承担代价、留下回应钩子，而不只被动回答。
- **表演能力**：台词、动作、视线、距离、声音和沉默来自同一个选择，并留下可观察后果。
- **媒介无关**：同一个已提交 PerformanceBeat 可被视频、游戏、语音或调试 UI 投影。

### 1.3 成功标准

1. 遮掉名字，仍能从注意对象、选择、语言组织和失控方式识别角色。
2. 同一刺激给不同人物时，形成不同主观事件和行动路线。
3. 人物不会自动理解全部、安慰全部、解释全部并关闭冲突。
4. 强烈情绪会损伤某种能力、关系、资格或行动路径，并留下余震。
5. 每个有效场景至少改变关系、知情、资格、义务、资源、位置或下一步之一。
6. 人物选择可以失败；客观世界不会因一段漂亮台词而被改写。
7. Seedance 或游戏引擎替换后，NPC 核心语义不变。

---

## 2. 范围与非目标

### 2.1 第一阶段范围

- 1–3 个主要 NPC 的封闭场景排练；
- 单场景多拍交互；
- 独立角色会话与私有状态；
- 客观场景 affordance 和结果解析；
- 台词、动作、声音、视线、走位和余震的表演输出；
- 完整调试轨迹与盲审；
- Seedance 分镜包输出；
- 可保存、分支、重放的场景排练。

### 2.2 后续范围

- 游戏引擎实时 Host；
- 长期人物记忆与跨场景关系演化；
- 多 NPC 并发和群体场景；
- 玩家实时介入；
- 动画图、表情、凝视和语音驱动；
- 多媒介共享同一 NPC 状态。

### 2.3 非目标

- 不做以 Prompt 模板为核心的 Seedance 工具；
- 不让导演逐拍指定角色该说什么、何时崩溃；
- 不让 NPC 代替作者表达主题或主控思想；
- 不将 MBTI、荣格功能或情绪值当作硬编码反应矩阵；
- 不用台词自证客观动作成功、关系修复或任务完成；
- 不保证视频模型 100% 角色一致、口型准确或精确秒点；
- 不把调试字段、评分和模型推理暴露为成片或游戏表现内容。
- 不以“每拍必须升级”“每拍必须冲突”替代真实人物行为；

---

## 3. 核心设计原则

### 3.1 人物先行动，媒介后呈现

运行时先决定人物做什么以及客观结果，再由 Adapter 决定如何拍摄或动画化。禁止为了镜头需要而改变人物选择。

### 3.2 世界事实与主观理解分离

- Host 是地点、物件、位置、访问权、伤害和任务状态的唯一事实源。
- NPC 可以持有错误 belief，但不能把 belief 直接提交为事实。
- 表演文本不能创造 Host 未返回的结果。

### 3.3 决策 U 与表达 Y 分离

```text
U：注意、理解、意图、披露边界、策略、行动请求
X：Host 解析后的客观结果
Y：台词、动作、声音等可观察表演
```

Y 只接收 U 的公开安全投影、稳定 Voice、已解析 X 和有限可见历史。Y 不重新读取全部私有状态，避免在最后一步被模型默认的高情商人格覆盖。

### 3.4 状态只通过已提交事件改变

模型提出；Host 解析；事件提交；Reducer 更新。未解析或未提交的草稿不能改变 canonical state。

### 3.5 人格是语义偏置，不是动作表

人格决定更可能注意什么、怎样判断、保护什么、什么能力先坏。它不决定“某类型遇到愤怒就有 60% 概率反击”。

### 3.6 情绪是连续干扰，不是表演标签

精确数值只作为 Runtime 控制信号。模型评价事件时使用离散方向和影响等级；Reducer 维护连续值。生成层不把数值或情绪名直接翻译成表情。

### 3.7 安静、失败和未完成可以成立

一个 Beat 不必聪明、完整或激烈。它必须因果回应当前处境，并改变压力、信息、选择、关系或可行动空间；也可以明确保持、错过或关闭一个机会。

---

## 4. 系统分层

```text
sceneactor/
├── contracts/      跨层可序列化协议
├── persona/        稳定、Host 无关的人物身份与表达依据
├── cognition/      主观评价、策略 U、情绪、关系、目标与记忆
├── scene/          Host 无关的场景状态、观察与行动合同
├── orchestration/  DecisionFrame 组装、TurnTransaction 与提交协调
├── performance/    PublicPerformanceIntent + X + Voice → PerformanceBeat
├── hosts/
│   ├── rehearsal/  本地客观场景、affordance 与结果解析
│   └── game/       外部游戏世界 ActionExecutor 与结果回执
├── adapters/
│   ├── seedance/   已提交 Beat → 分镜、Prompt 和视频候选
│   └── game/       已提交 Beat → 动画、凝视、语音和 UI
├── review/         事实校验、连续性检查和独立语义盲审
└── authoring/      人设、场景和排练工作台；所有 AI 修改需确认
```

### 4.1 依赖方向

```text
contracts ─────────────────────────► all modules
persona ───────────────────────────► cognition
scene contracts ──────────────────► hosts
persona + cognition + host view ──► orchestration
orchestration ────────────────────► performance
committed performance ────────────► adapters
rehearsal host ───────────────────► review
game host / adapters ─────────────► external engine
```

禁止反向依赖：

- `persona` 不知道场景、Seedance 或游戏引擎；
- `cognition` 不知道分镜、摄影术语或视频时长；
- `hosts` 不读取其他角色的私有认知状态；
- `performance` 不 commit 世界状态，也不接触原始 U；
- Presentation Adapter 只消费已提交 Beat，不产生 X；
- `seedance` 不修改人格、目标、情绪或关系；
- 游戏引擎专属动画节点不能成为人物动机或 Cognition 输入。

---

## 5. 核心领域模型

以下为语义合同，不是最终语言绑定。

### 5.1 Persona：稳定人物身份

```text
Persona
├── id, name
├── embodiment
│   ├── gender / pronoun anchor
│   ├── age / life stage
│   ├── appearance
│   └── physical constraints
├── lived_context
│   ├── role
│   ├── background
│   ├── values
│   ├── preferences
│   └── competencies
├── dramatic_lenses            optional
│   ├── desire
│   ├── line
│   ├── contradiction
│   ├── feared_truth
│   ├── soft_spot
│   └── secret
├── cognition_lens             optional semantic profile
├── voice_profile
└── extensions
```

规则：

- 只有 `id`、`name` 必填；薄人设不被系统静默补成创伤型人物。
- 性别、年龄、秘密、创伤和人格类型不得从姓名、职业或外表推断。
- Persona 不保存当前情绪、位置、库存、任务或关系状态。
- `voice_profile` 描述组织语言的方式，不是口头禅清单。

### 5.2 RuntimeState：可变主观状态

```text
RuntimeState
├── emotions                 internal control values
├── relationships
├── goals
├── commitments
├── beliefs
├── memories
├── recent_visible_history
├── last_appraisal
├── participation
├── event_revision
└── committed_turns
```

规则：

- 主观 belief 可以错误；其来源必须可追溯。
- Goal 是希望的结果；Commitment 是持续义务，不能互相偷换。
- 一项未完成的义务只能履行、重谈或明确违背并付出后果。
- 普通记忆不会自动注入每场；必须经过检索决策。

### 5.3 DecisionFrame：Orchestrator 组装的决策输入

```text
DecisionFrame
├── scene_id, turn_id
├── S: actor private_state
├── R: actor one-way relationship views
├── E: actor emotion projection
├── O: Host-projected observable facts
├── H: recent committed visible history
├── L: durable subjective continuity
├── capabilities
├── public_affordances
├── available_targets
└── decision_contract
```

`TurnOrchestrator` 负责组装 DecisionFrame：Cognition/RuntimeState 只提供当前 actor 的 S/R/E/H/L；Scene Host 只提供 O、capabilities、affordances 和 targets。其他角色的 private state、作者目标、评价分数和未来事件不得进入。

### 5.4 Appraisal：主观事件评价

```text
Appraisal
├── subjective_observation
├── emotion_changes[]       rise/fall + minor/moderate/major
└── grounded_refs[]
```

Appraisal 不是心理分析报告。它只表达当前刺激对该人物意味着什么，并引用输入证据。

### 5.5 PerformancePolicy U：私有决策瓶颈

```text
PerformancePolicy
├── attention[]
├── interpretation
├── current_intent
├── chosen_strategy
├── action_request: ActionIntent
├── disclose[]
├── withhold[]
├── expected_response
├── response_hook
├── surface_action_intent
├── accepted_cost
├── relationship_transition
├── continuation_disposition
└── grounded_refs[]
```

`ActionIntent` 是结构化对象，不是自由文本：

```text
ActionIntent
├── action_kind
├── target
├── arguments
├── required_capabilities[]
└── grounded_refs[]
```

`ActionCommand` 是所有 Host（Rehearsal、Game 或未来外部世界）共享的执行合同：

```text
ActionCommand
├── command_id
├── idempotency_key
├── branch_id, turn_id, actor_id
├── action_kind
├── target
├── arguments
├── required_capabilities[]
├── expected_scene_revision
├── interrupt_policy
└── immutable_command_hash
```

`ActionCommand` 由 Orchestrator 从已验证的 `ActionIntent` 和 Host capabilities 生成。Rehearsal Host 直接执行该合同；Game SceneHost 将其映射为引擎命令，但不得改变 action kind、target、arguments 或 expected revision。所有 Host 必须返回同一 `HostReceipt` 形状：

```text
HostReceipt
├── command_id
├── status                 pending / completed / failed / cancelled
├── host_operation_id
├── scene_revision_before
├── scene_revision_after
├── resolved_outcome_ref
├── observable_facts[]
└── error
```

Host receipt 是外部副作用的查询和对账权威；它不是 NPC 事实本身，只有经过 TurnEventBatch 提交后才进入 SceneActor canonical ledger。

规则：

- U 不包含 chain-of-thought；字段是可验证决策摘要。
- 每个非沉默决策必须引用 S/R/E/O/H/L 证据。
- 一拍只有一个主要公开变化：行动、信息、立场、关系或关闭。
- 继续必须留下具体回应钩子；关闭和退出不得再制造新钩子。
- 失败后若重试，必须说明因果机制发生了什么变化；同义改写不是新路线。

### 5.6 PublicPerformanceIntent：U 的公开安全投影

```text
PublicPerformanceIntent
├── actor_id
├── target
├── interaction_move
├── authorized_action
├── speech_atoms[]
├── delivery_mode
├── visible_cost_signal
├── response_hook
├── disposition
└── evidence_anchors[]
```

Kernel 通过 allowlist 从 U 和 canonical public evidence 派生此 DTO。映射必须满足：`target = ActionIntent.target`；`authorized_action` 只能来自 ActionIntent 且通过当前 decision contract；`speech_atoms` 只能来自 U.disclose 中经过 evidence 校验的内容；`delivery_mode` 只能由 U.chosen_strategy 经固定 allowlist 映射产生；`evidence_anchors` 必须是 supplied O/H/S.identity 的可回溯引用。不存在映射的字段必须为空，不得由 Performance 或 Adapter 补造。

它不包含 `attention`、`interpretation`、`current_intent`、`withhold`、`expected_response`、私有关系判断或自由事实草稿。Performance 和 Presentation Adapter 不得读取原始 U：Performance 按 §6.2 的 allowlist 读取 PPI、Voice、X 和有限公开历史；Presentation Adapter 按各自 Adapter 合同读取已提交 PerformanceBeat 及允许的公开场景/连续性投影。私有字段只能影响有限的 `interaction_move`、`delivery_mode` 和非事实性的行为方向；所有台词事实必须来自 `speech_atoms` 和 `evidence_anchors`，所有世界结果必须来自 X。

PPI 的读取边界按模块分别固定：Performance 只能读取 `PublicPerformanceIntent`、Voice、X 的可观察结果、最近 2–3 个已提交表面 Beat 和当前公开场景锚点；Seedance 与 Game Presentation Adapter 只能读取已提交 `PerformanceBeat`，以及各自 Adapter 合同允许的公开 `SceneState`、`PerformanceContinuity`、`AdapterProjectionState` 和素材/引擎配置。任何 Presentation Adapter 都不能读取原始 U、私有状态、Appraisal 或未提交候选。


### 5.7 ResolvedOutcome X：Host 客观结果

```text
ResolvedOutcome
├── status                  succeeded / failed / partial / deferred
├── action_kind
├── observable_facts[]
├── changed_refs[]
├── error
├── costs[]
└── evidence_refs[]
```

只有 X 能确认门是否打开、物件是否转移、角色是否移动、任务是否完成或伤害是否发生。

### 5.8 PerformanceBeat Y：媒介无关的已提交表演

```text
PerformanceBeat
├── beat_id, actor_id
├── action
├── speech
├── addressee
├── attention_target
├── gaze
├── blocking
├── posture_change
├── delivery
│   ├── pace
│   ├── volume
│   ├── breath
│   ├── articulation
│   ├── pause
│   └── vocal_target
├── physical_residue
├── observable_outcome
├── response_hook
├── continuity_out
└── source_event_ids[]
```

PerformanceBeat 只保存外部可表现结果。`withhold`、私有解释和作者目标不进入公开 Beat。

生成和校验阶段的 Y 称为 `PerformanceDraft`；只有 TurnEventBatch 成功提交后，系统才为其分配 `beat_id/source_event_ids` 并称为 `PerformanceBeat`。Presentation Adapter 只能消费后者。

### 5.9 状态权威与连续性分层

```text
SceneState                         Host-owned objective truth
├── actor locations
├── entity/prop ownership
├── injuries and physical effects
├── access and participation
├── task/commitment evidence
└── equipment/wardrobe state

SubjectiveResidue                  RuntimeState-derived
├── emotion residue
├── relationship residue
├── beliefs
└── unfinished subjective concerns

PerformanceContinuity              Core, derived from committed Y
├── posture
├── gaze target
├── unfinished visible gesture
├── vocal residue
└── unresolved response hooks

AdapterProjectionState             Adapter-private, non-canonical
├── screen position/direction
├── camera axis and shot eyelines
├── rendered prop hand
├── exact accepted visual/audio end state
└── provider candidate lineage
```

AdapterProjectionState 只能约束同一媒介的后续呈现。Seedance 成片与计划不一致时，该偏差不能反写 SceneState；只有 Host 将其接受为新的观察或世界事件后，才能进入下一轮客观事实。

---

## 6. 单拍事务与失败恢复

### 6.1 TurnTransaction 状态机

```text
prepared → host_pending → host_completed → performance_pending → committed → projected
     │            │              │                  │                │
 U/command 已验证  外部 Host 未定案  X+receipt 已持久化    X 已落账，Y 可重试   事件批已提交
```

`prepared` 到 `host_pending` 前必须先写入 `HostCommandJournal`，因此进程崩溃后仍可用同一 command 查询外部 Host：

```text
HostCommandJournal
├── command_id, idempotency_key
├── branch_id, turn_id, actor_id
├── immutable_command_hash
├── expected_scene_revision
├── status             prepared / sent / pending / completed / failed
├── host_receipt
└── last_query_at
```

Host pending/timeout 与 Performance pending 是不同状态；前者没有可用 X，后者已有 X 但尚未完成 Y。两者都必须持久化，恢复时先查询 journal，不得创建新 command。

事务流程：

```text
1. Orchestrator asks Host for actor-visible ObservationView
2. Orchestrator builds DecisionFrame from Host view + actor RuntimeState
3. Cognition generates Appraisal + U
4. Kernel validates evidence, capability and expected revisions
5. Kernel derives PublicPerformanceIntent and prepares idempotent ActionCommand
6. Host resolves locally, or external ActionExecutor returns pending/completed X
7. Completed X is durably receipted before any retryable presentation work
8. Performance realizes Y from PublicPerformanceIntent + Voice + X
9. Validators check Y facts, ownership and PerformanceContinuity
10. TurnEventBatch commits with X and Y, or records X + performance_pending; a later follow-up commits Y without replaying Host
11. Pure Reducers rebuild RuntimeState, SceneState projection and continuity
12. Presentation Adapters project the committed Beat independently
```

外部 Host 规则：

- 每个 ActionCommand 带稳定 `command_id/idempotency_key`；发送前先持久化 HostCommandJournal，超时后查询同一 command，禁止用新 ID 盲目重放世界副作用。
- 外部游戏世界已经改变而 Y 失败时，X 以 `performance_pending` 状态落账；重试 Y 不得再次执行 ActionCommand。
- Host pending/timeout 没有 X，恢复流程只能查询 journal 或明确标记 command_failed；不能生成依赖 X 的成功/失败表演。
- Event append 失败时，Orchestrator 使用 Host receipt 和同一 command ID 对账并恢复提交。
- Reducer 是纯函数，可重复执行；Adapter 投影失败只重试投影，不回滚 X/Y，也不重放 Host 动作。
- 本地 Rehearsal Host 可在同一存储事务中原子提交 X 与事件批；外部 Host 使用幂等命令、持久回执和对账保证最终一致。

失败边界：

- Cognition 失败：没有 ActionCommand，不改变世界；可重试 Cognition。
- Host pending/timeout：保持 pending，查询同一 command；不能编造 X 或角色等待台词。
- Host 明确失败：以失败 X 继续生成可见失败表演。
- Y 失败：保存 X，创建 `performance_pending` 的 TurnEventBatch；只重试 Y，完成后追加同一 batch 的 follow-up performance event，不重放 Host。
- Validation 失败：不重放 Host；修复或重生 Y，仍引用同一 X。
- Event store 失败：从 Host receipt 对账；同一 batch/command 幂等提交。
- Reducer 失败：修复后从已提交事件重建。
- Adapter 失败：不影响 canonical turn，只重试该 Adapter。

### 6.2 表达封口

Y 可读取：PublicPerformanceIntent、Voice、X 的可观察结果、最近 2–3 个已提交表面 Beat 和当前公开场景锚点。

Y 不可读取：原始 U、完整 private state、`withhold`、他人内心、作者目标、质量评分、Host 隐藏完成条件、未提交候选或未来场景。


---

## 7. 情绪、人格与表演模型

### 7.1 主观处理链

```text
客观发生
→ 人物实际注意到什么、漏掉什么
→ 人物如何理解或误解
→ 刺中哪个欲望、价值、羞耻、关系或职责
→ 当前可行的反应集合
→ 人物选择一个策略和行动
→ Host 返回结果
→ 身体、语言和关系留下余震
```

### 7.2 情绪连续线

每场为每个主要人物维护一段短工作描述：

- 进场时正在完成什么现实事务；
- 已积累什么压力；
- 平时靠什么维持正常；
- 什么刺激可能击穿防线；
- 防线失效后哪项能力先坏；
- 场末留下什么未恢复状态。

不要求逐句填满心理表。普通工作交接和生活动作无需每拍心理编译。

### 7.3 允许的反应缺口

- **延迟**：当下继续原动作，后续选择才显出影响；
- **置换**：不回应伤口，改问更安全具体的事情；
- **误名**：用自己能承受的理由解释反应；
- **残留**：本场不解释完整意义，让后续行为证明。

### 7.4 防线失效

强烈节点必须至少造成一个不可无成本恢复的结果：

- 原本不承认的依赖被暴露；
- 伤害正在争取的人；
- 放弃仍可走的路线；
- 手上工作无法继续；
- 失去资格、资源或关系安全；
- 说出或做出无法收回的事情。

爆发后不能下一句立刻恢复成善于总结和调解的助手。

### 7.5 声线模型

VoiceProfile 至少描述：

- 从什么信息进入一句话；
- 先给结论、证据、条件还是关系；
- 一轮通常容纳多少动作；
- 被打断后如何续；
- 如何回避和改口；
- 压力上升时句法、速度和音量如何改变；
- 真正破闸时哪项语言能力先坏；
- 对不同关系对象如何切换。

禁止用固定口头禅、方言或统一短句代替人物差异。

---

## 8. Scene Host

### 8.1 职责

Scene Host 拥有：

- 当前地点、时间和在场角色；
- 公共物件与 affordance；
- 位置、访问权、参与状态；
- 行动合法性；
- 客观效果；
- 可查询的 Action receipt 与客观场景 revision；
- 公开任务效果和可验证 world facts。

Scene Host 不拥有：

- NPC 的最终选择；
- 台词；
- 私有关系理解、情绪含义和私有完成条件；
- 逐拍戏剧走向；
- 人物必须抵达的预定结局；
- 场景自然结束判定。


### 8.2 场景合同

```text
SceneContract
├── setting
├── opening_public_state
├── cast
├── public_affordances
├── allowlisted_effects
├── per_actor_private_setup
│   ├── initial_goal
│   ├── commitments
│   ├── relationship_view
│   ├── private_resources
│   ├── private_facts
│   └── completion_evidence
├── safety_cap
└── author_test_objective      review-only, never enters actors
```

`SceneContract` 是 Authoring/Orchestration 的初始化输入，不是 Host 的私有状态仓库。Orchestrator 在场景启动时把 `per_actor_private_setup` 导入各自 RuntimeState；Host 只接收 setting、opening、cast、public affordances、allowlisted effects 和 safety cap 的公开投影。`CompletionEvaluator` 由 Orchestrator 持有，读取各 actor 自己的目标、义务和关系机会状态，以及 Host 提供的公开完成证据；它不把私有完成条件发送给其他 actor。

### 8.3 结束条件

`CompletionEvaluator` 针对场景的完整 participant ledger 计算结束，不只检查仍在场 actor：

```text
natural = 所有 participant 的目标、义务和关系机会均已结算
          AND 没有 unresolved response hook

participants_finished = 所有 participant 已客观离场或终止参与
                        AND 每个未结目标、义务和 hook 已记录为
                            carry-over / abandonment / breach

forced = 达到 safety cap；明确标记非自然结束并保留全部未结状态
```

角色离场不会删除其未结状态；它只能产生显式 carry-over、abandonment 或 breach 事件。目标结算不能越过公开回应钩子；没有回应钩子也不能单独证明场景已经完成。结束必须返回明确 `completion_reason`。

---

## 9. 事件、分支与记忆

### 9.1 Event Ledger

```text
RuntimeEvent
├── id
├── batch_id, branch_id
├── turn_id, command_id
├── kind
├── actor_id, scene_id
├── payload
├── visible_to[]
├── evidence_refs[]
├── caused_by[]
└── timestamp/order

TurnEventBatch
├── batch_id, branch_id, turn_id
├── command_id, idempotency_key
├── expected_actor_revisions{}
├── expected_scene_revision
├── host_receipt
├── lifecycle              prepared / host_pending / host_completed / performance_pending / committed
├── performance_status     pending / complete / failed
├── completion_reason
├── immutable_content_hash
└── events[]

TurnEventFollowUp
├── follow_up_id
├── parent_batch_id
├── idempotency_key
├── expected_batch_status
├── performance_status     complete / failed
├── immutable_content_hash
└── events[]
```

SceneActor 的 canonical record 和 Reducer state 只能由已提交事件重建。`batch_id` 在首次 command journal/X receipt 阶段固定；`TurnEventFollowUp` 只能把同一 batch 从 `performance_pending` 推进到 `committed`，不能重新执行 command 或改变 X。Event store 必须按 `(branch_id, batch_id)`、`(branch_id, command_id)`、follow-up idempotency key 和 immutable content hash 去重；同一 key 的不同内容必须拒绝。外部游戏世界仍由 Game SceneHost 权威维护，通过 `command_id + host_receipt` 与事件账本对账。Prompt history 是投影，不是存储。

### 9.2 排练分支

每次排练是一个可丢弃分支：

- 分支保存 persona revision 和 state revision；
- 重试生成新的候选，不覆盖旧候选；
- 只有完成且通过确认的分支可晋升长期状态；
- revision 不匹配时禁止静默覆盖；
- 新场景开始时，旧目标和义务必须显式结算或违背。

### 9.3 记忆

记忆分为：

- 已提交公共经历；
- 角色自己的主观解释；
- 对某人的关系记忆；
- 持续目标和承诺；
- 普通生活记忆。

只有与当前场景相关并经过检索的记忆进入 DecisionFrame。原始场景全文不跨场景自动重放。

---

## 10. Performance 层

### 10.1 职责

Performance 层将已决定、已解析的语义变成演员可执行内容：

- 一次可见动作；
- 一次自然发言或沉默；
- 说话对象；
- 视线与注意对象；
- 靠近、后退、占位、阻挡等走位意图；
- 语速、音量、气息、停顿和发声对象；
- 动作后仍留在身体或事务上的余震。

### 10.2 约束

- 不补充未授权道具和动作；
- 不替另一角色说话；
- 不复述运行时字段；
- 不把潜台词直接说破；
- 不把 X 翻译成任务状态播报；
- 不同时完成解释、安慰、安排、道歉和关闭；
- 不为每句话机械配一个表意动作；
- 不使用“眼神复杂”“悲伤掠过”等不可执行标签；
- 动作必须有现实起点，并与此前身体状态连续。

### 10.3 生活与戏剧动作

行为可以是：

- 工作行为；
- 习惯行为；
- 摩擦行为；
- 决定性行为。

只有决定性行为必须承担明确状态后果。不是每个物件、视线和环境声都要表达主题。

---

## 11. Seedance Adapter

### 11.1 定位

Seedance Adapter 是表演展示和离线视频生产出口，不是 NPC 核心的一部分。

输入：

- 已提交 PerformanceBeat；
- 公共 SceneState；
- Core PerformanceContinuity；
- 当前 Seedance AdapterProjectionState；
- 已确认视觉/声音素材；
- provider capability snapshot。

输出：

- ShotPlan；
- 素材绑定；
- Provider-specific Prompt；
- 视频候选及其血缘；
- 连续性与素材校验报告。

### 11.2 生产对象

```text
Episode
└── Scene
    └── Beat
        └── ShotSpec
            ├── Keyframe
            ├── AssetBindings
            ├── PromptVariant
            └── RenderVariant
```

### 11.3 ShotSpec

```text
ShotSpec
├── shot_id
├── beat_refs[]
├── dramatic_function
├── visible_subjects[]
├── visual_prompt
├── camera_instruction
├── blocking
├── eyelines
├── spatial_axis
├── audio_plan
├── soft_time_hint
├── continuity_in/out
├── asset_bindings[]
└── validation_status
```

规则：

- 一镜一个主动作；
- 一镜一个主运镜；
- Shot 顺序是硬结构，精确秒点只是软提示；
- 对话镜头明确 speaker、addressee、听者位置和 reaction coverage；
- 复杂动作拆镜；
- 下一镜依据已验收成片的实际末态，而不是计划末态；
- Prompt 不接触角色私有解释和未披露信息。

### 11.4 ReferenceBinding

```text
ReferenceBinding
├── asset_id
├── entity_id
├── role
├── allowed_transfer[]
├── excluded_transfer[]
├── required
└── status
```

角色示例：`identity_anchor`、`face_anchor`、`scene_style`、`prop`、`camera_motion`、`action_motion`、`voice_timbre`、`music`。

每个素材必须有明确主职责。素材数量和格式由 `provider + model_id + capability_snapshot` 决定，不写死在核心合同。

### 11.5 候选版本

```text
RenderVariant
├── variant_id
├── parent_variant_id
├── model/provider/version
├── prompt_snapshot
├── asset_snapshot
├── output
├── review
├── failure_reasons
└── accepted
```

重试不覆盖旧结果。后续镜头只能依赖已接受候选。

### 11.6 Seedance 已知概率风险

- 人物 ID 漂移；
- 多角色重复或混淆；
- 精确时间不稳定；
- 字幕无法完全禁止；
- 多人口型和音频可能失真；
- 多次延长导致画质下降；
- 拼接点跳帧或回退；
- 参考素材过多造成优先级冲突。

这些只能产生 warning 或生产策略，不能伪装成可完全消除的硬保证。

---

## 12. Game SceneHost 与 Game Presentation Adapter

### 12.1 Game SceneHost / ActionExecutor

Game SceneHost 是 Host 边界的实现，负责观察投影、世界行动和客观回执：

```text
validated ActionCommand
→ Game SceneHost / authoritative engine systems
→ pending progress or completed ResolvedOutcome X
→ durable HostReceipt
```

```text
GameActionCommand extends ActionCommand
├── engine_entity_ref
├── engine_arguments
└── engine_interrupt_policy
```

Game SceneHost 的映射必须可追溯：`engine_entity_ref` 对应 `ActionCommand.target`，`engine_arguments` 只能是 `ActionCommand.arguments` 的引擎编码，且必须携带原始 `command_id/idempotency_key`。引擎专属字段不得回流为 Cognition 输入。

导航、碰撞、库存、伤害、交互、权威位移和物件操纵属于 Game SceneHost。它可在 pending 期间产生可查询进度，但动作完成前不能返回成功 X；超时后必须允许用同一 command ID 查询结果。

### 12.2 Game Presentation Adapter

Game Presentation Adapter 只消费已提交 PerformanceBeat，生成不改变世界事实的表达投影：

```text
GamePresentationCommand
├── beat_id
├── cosmetic_animation_tags[]
├── gaze_target
├── posture_overlay
├── facial_intent
├── speech
├── voice_delivery
├── subtitle
└── projection_idempotency_key
```

权威移动或拿取动作已由 Game SceneHost 执行；Presentation Adapter 可以增加表情、凝视、语音和非权威姿态层，但不得再次移动角色、转移道具或改变 X。投影失败只重试该 projection。

### 12.3 实时约束

- Game SceneHost 支持玩家和环境中断，并用 completed/failed/deferred X 回报；
- 表情和动画标签是已提交表演的实现，不是人物心理源；
- Presentation Adapter 可降级表达质量，但不得改变选择含义或世界结果；
- 网络和模型超时进入明确 pending/failed 状态，不伪造角色台词；
- 目标引擎和最小动作协议确定前，核心只冻结 GameSceneHost Port，不固化引擎专属动画图。

---

## 13. Authoring 与排练工作台

工作台围绕人物能力验证，而不是视频制作步骤组织：

```text
1. 人设编辑
2. 场景设置
3. NPC 排练
4. Beat 检查
5. 表演盲审
6. Presentation 预览
```

Seedance 生产可提供三个子页面：

- **剧本**：Scene、Beat、角色表演和状态变化；
- **分镜包**：Beat → Shot、关键帧、镜头指令和候选；
- **素材确认**：缺失素材、断链、连续性和导出阻断。

AI 对人设、场景或素材的修改均为待确认 patch。只有用户接受后改变草稿；只有显式发布后进入人物库或场景库。

---

## 14. 验证与质量门

### 14.1 硬验证

任何一项失败都拒绝 commit 或导出：

- 读心或知情越界；
- 使用未来事实；
- 无证据客观断言；
- 请求非法 action 或目标；
- 角色/说话人/道具所有权错误；
- PerformanceDraft 超出 PublicPerformanceIntent/X 授权；
- 目标由台词自证完成；
- 未结回应钩子却宣称自然结束；
- 连续性状态冲突；
- revision 冲突；
- Seedance 必需角色、场景、道具或关键帧未绑定；
- 游戏动作尚未完成却提交成功。

### 14.2 语义盲审

评审只看干净的场景设定、人物公开卡和可观察剧本，不看 U、作者目标、分数和隐藏完成条件。

独立视角：

1. **Reader**：人类可信度、因果倾听、潜台词、无聊与重复；
2. **Character**：角色可辨识性、压力选择、声音与人设一致性；
3. **Dramaturgy**：拍间变化、策略变化、场景使用和结束状态；
4. **Performance**：动作、台词、声音、走位和余震是否合成一场表演。

核心维度：

- human
- causality
- character
- subjectivity
- emotion_continuity
- subtext
- movement
- performance
- world_grounding
- continuity

### 14.3 关键测试

- **角色互换测试**：换人后仅改名字仍成立，则失败。
- **匿名声线测试**：遮掉姓名和职业词，仍应能识别角色。
- **同刺激分离测试**：不同角色必须注意、误读和保护不同内容。
- **知识边界测试**：每项台词事实可回溯到角色证据。
- **失败路线测试**：行动失败后不能只换措辞重复。
- **防线失效测试**：高压后必须损伤人物特有能力，而不是套用通用哭喊。
- **余震测试**：爆发后一拍不能恢复默认助手状态。
- **Adapter 等价测试**：Seedance 与游戏投影不得改变 Beat 的语义行动和关系后果。

### 14.4 AI 味审查

负面句式库只进入独立 evaluator，不整段注入生成 Prompt，避免为生成模型提供坏模板。

重点拒绝：

- 完整外交式回答；
- 一次讲清所有规则、情绪和后果；
- 角色主动总结自己的创伤和弧光；
- 工整对仗、主题金句和客服式分号；
- 每句配一个精准表意动作；
- 同义策略反复；
- 用职业术语替代具体的人、门、纸和物件；
- 用“克制”回避人物真正受损。

---

## 15. 可观测性与调试

每拍调试视图展示：

- 输入证据 S/R/E/O/H/L；
- Appraisal；
- U；
- PublicPerformanceIntent；
- Host 结果 X；
- 已提交 PerformanceBeat Y；
- 事件 IDs；
- 情绪和关系 Reducer 前后差异；
- 连续性状态；
- 验证和盲审结果；
- 模型、延迟和重试信息。

调试数据不得进入下轮公开历史，除非它本身是角色可观察事件。

日志必须区分：

- canonical events；
- model traces；
- presentation candidates；
- blind reviews；
- authoring patches。

---

## 16. 安全、权限与数据边界

- 人设发布、长期状态晋升和分支覆盖必须显式授权；
- 不从名字、头像或职业推断敏感身份；
- 真人视觉/声音素材遵循 provider 的授权与身份校验要求；
- 私有角色状态不进入其他角色、公开剧本或第三方渲染 Prompt；
- 外部 Provider 只接收完成当前任务所需的最小投影；
- 所有导出记录 provider、model、素材授权和 Prompt 快照；
- 删除一条生成结果不得破坏 canonical NPC 经历，除非同时回滚对应事件分支。

---

## 17. 第一阶段实现顺序

第一阶段按可运行垂直切片推进，不按目录独立“完成”模块。

### Phase A：冻结跨层合同

- Persona、RuntimeState；
- ObservationView、DecisionFrame；
- Appraisal、U、PublicPerformanceIntent、X、Y；
- ActionCommand、HostReceipt、TurnEventBatch；
- SceneState、PerformanceContinuity、AdapterProjectionState 权威边界。

验收：所有合同 JSON 可序列化，公开投影不含私有字段，状态权威无重叠。

### Phase B：事件存储与纯 Reducer

- 幂等 append；
- expected revision 校验；
- branch-aware event ledger；
- RuntimeState/SceneState/continuity 确定性重建；
- Host receipt 对账入口。

验收：同一事件流重复重放得到完全相同状态；重复 batch 不产生副作用。

### Phase C：单 Actor 垂直链路

- 一个受限但真实的 Rehearsal Host action；
- `DecisionFrame → U → PublicPerformanceIntent → X → Y → commit → replay`；
- Y 失败后保存 X 并独立恢复；
- 基础调试视图。

验收：成功、Host 失败、Y 失败、event append 重试四条路径均不重复世界动作。

### Phase D：双 NPC 场景排练

- 独立私有状态和单向关系；
- alternating turns；
- 公共 affordance 与私有资源；
- 目标、义务、回应钩子和结束公式；
- branch snapshot 与晋升。

验收：无读心、无跨角色状态泄漏，场景能自然或强制结束且原因可解释。

### Phase E：Quality System

- 硬验证；
- 四视角盲审；
- 角色互换、匿名声线和知识边界评测；
- 失败分类与局部返修。

### Phase F：Seedance Presentation Adapter

- ShotSpec、ReferenceBinding；
- provider capability；
- Prompt renderer 和候选版本；
- 素材确认、AdapterProjectionState 与连续性检查。

Game SceneHost 只在 Phase A–C 的事务和回执协议验证后接入；Game Presentation Adapter 可与 Seedance Adapter 并行实现。

## 18. 防跑偏治理与实验晋升制度

架构边界不能保证人物质量；它只能使错误路线更难进入生产。SceneActor 采用“内核红线 + 隔离实验 + 冻结评测 + 可删除晋升”的治理制度，防止主观模拟退化为词表、字数、正则和人格标签工程。

### 18.1 三类校验必须分开

#### A. 允许进入 Runtime 的确定性校验

只校验机器可证明的结构与权威：

- schema、枚举和必填字段；
- action capability、target、argument 和 ownership；
- evidence ref 是否存在、角色是否有权观察；
- 事实 claim 是否来自 canonical evidence；
- revision、幂等、事务和事件因果；
- Host 效果、位置、资源、权限和完成证据；
- 私有数据是否越过公开投影边界。

这些规则回答“是否合法、是否有证据、是否会破坏世界”，不回答“像不像人”。

#### B. 禁止进入 Runtime 的语义质量硬编码

以下机制不得决定 U/Y 通过、拒绝、重试、情绪、人格或场景完成：

- 关键词、停用词、人格词或“generic word”名单；
- 正则命中某个词就判定角色化、情绪、关系、重复或推进；
- 最少/最多单词数、句数、speech unit 数决定人类感；
- 要求输出必须出现人设中的某个具体词；
- 用字符串重合证明人物身份已表现在台词中；
- 用固定短语识别离场、和解、信任、崩溃或目标完成；
- 用语义相似度阈值代替 Host 结果或盲审；
- 把人格类型映射成动作概率、固定策略或台词模板；
- 为一个失败案例新增只针对该案例的例外分支。

精确 span 只允许用于**事实授权**：证明一句事实来自哪个公开证据。它不得用于证明台词有个性、够具体、够长或够像人。

#### C. 只允许进入 Review 的语义判断

以下问题由看不到 U、分数和作者目标的独立语义评审或人工读本判断：

- 是否像活人；
- 是否是这个角色而非可替换角色；
- 是否真的听见上一拍；
- 是否重复同一策略；
- 情绪是否连续并产生代价；
- 表演是否写在鼻子上；
- 场景是否推进、空转或虚假收束。

Review 产出诊断和实验指标，默认不成为逐拍 Runtime governor。

#### D. Causal Persona Constraint Card（CPCF）

每个角色、每个场景冻结一张因果边界卡，作为 ReviewContextManifest 的输入，而不是台词模板：

- `direct_access`：角色此刻直接感知到的对象和事件；
- `acquired_knowledge`：角色已通过合法经历获得的知识；
- `beliefs_and_inferences`：角色自己的信念与推断，不能冒充事实；
- `unknown_or_forbidden`：未知、禁止读取或作者不可见的信息；
- `memory_relationship_state`：可影响当前反应的关系与记忆状态；
- `physical_tool_capabilities`、`social_legal_authority`：身体、工具和社会/法律权限；
- `conceptual_vocabulary`、`local_limits`：概念边界，以及可由明确上游事件解除的局部限制。

CPCF 只约束“能知道、能做、能授权什么”，不规定性格词、情绪词、台词内容或必然选择。每次独立审核必须使用冻结卡片，并运行三种压力：正常请求、`help_or_guess`（帮忙或直接猜）和 `step_out_of_character`（跳出角色回答）。压力测试要求角色在保持可用性的同时拒绝猜测禁区、拒绝元话语劫持；State A/B 对照还要证明局部限制不会扩大成全局无能，解除上游限制后新能力确实可用。

### 18.2 Semantic Hardcode Linter

CI 对 `cognition/`、`performance/` 和核心 validator 执行 AST/文本审计，标记：

- 用 `split/regex/casefold/token set` 的结果返回语义质量错误；
- 以文本长度控制角色行为或表演通过；
- 人格、情绪和关系专用词表；
- 测试断言生成文本必须包含某个“角色词”；
- 对特定人物、场景、职业、道具和语言的内核分支；
- 质量 evaluator 的结论被回灌为永久通用行为规则。

命中不等于所有字符串处理都非法：协议解析、ID 规范化和安全过滤可以存在，但必须标注用途。任何进入核心的例外需要 `semantic-hardcode-waiver`：写明安全/协议理由、owner、失效条件和删除测试；“提升角色质量”不能作为 waiver 理由。

### 18.3 Research 与 Production 双轨

- 新主观模拟方法只进入带 experiment ID 的 Research 分支或 feature flag；
- 实验不得直接修改生产 baseline；
- 同一实验只验证一个主要因果假设；
- 未通过晋升门的实验到期删除，不保留“也许以后有用”的兼容路径；
- 生产只保留当前胜出的单一路径，不叠加历次补丁；
- 失败方法、消融结果和反例保留在实验报告，不留在 Runtime 条件分支中。

### 18.4 每项改动的最小实验合同

提出修改前必须提交：

```text
ExperimentProposal
├── hypothesis                 为什么会改善人物能力
├── observed_failure           未修改 baseline 的真实失败证据
├── causal_layer               persona / cognition / host / performance / review
├── intervention               只改变什么
├── forbidden_shortcuts        不允许使用哪些词法/案例特判
├── primary_metrics
├── hard_invariants
├── frozen_holdout
├── kill_criteria
└── rollback_plan
```

没有可证伪假设时，不允许以“再加一个 validator 试试”开始修改。

### 18.5 晋升门

实验进入生产必须依次满足：

1. **复现**：保存 baseline 的完整失败轨迹，证明问题真实存在；
2. **最小干预**：只改一个主要变量，必要的依赖变更单独记录；
3. **消融**：证明收益来自目标机制，而不是更长 Prompt、更多 token、不同模型或重试次数；
4. **硬不变量**：事实、知情、ownership、Host 权威、事务和隐私零回归；
5. **配对盲审**：评审只看匿名 clean performance，以同一人物、场景、模型和采样条件比较 baseline/candidate；
6. **多样覆盖**：至少覆盖安静合作、冲突、失败、误解、任务场景、关系场景、薄人设和强人设；
7. **冻结 holdout**：调参期间不可查看或修改；最终只运行一次，失败则退回研究；
8. **反事实测试**：角色互换、同刺激分离和人设字段删除，验证系统不是靠关键词认人；
9. **成本门**：记录延迟、token、失败率和重试率；质量收益不能由不可接受的调用膨胀伪造；
10. **可回滚**：保留上一 accepted baseline 和精确源码/模型/Prompt/数据快照。

不能只用平均分晋升。严重事实错误、读心、人格互换或世界越权任一回归都直接否决；评审分歧必须保留，不能用平均值抹掉。

### 18.6 评测集治理

- `development`：可见，用于定位和调试；
- `regression`：冻结，覆盖已知失败类别；
- `holdout`：隐藏，只用于晋升；
- `challenge`：定期新增反事实、跨语言、薄人设和陌生题材。

场景作者、生成模型和主要 judge 不应全部使用同一个模型族。评审必须保存原始意见，并定期用人工双盲样本校准“human/character/causality/performance”等维度。测试不得断言某句固定台词、口头禅或关键词必然出现；只断言可观察合同、事实边界和配对质量结论。

### 18.7 Stop Rules

遇到以下信号立即停止加补丁，回到表示层或实验设计：

- 同一质量问题连续加入第二套词表/正则/阈值；
- 修一个样例后，相邻样例换一种方式失败；
- Prompt schema 字段持续增加但盲审不升；
- 需要知道角色名字或测试场景文本才能写 validator；
- validator 在判断文学意义而不是协议合法性；
- 候选只有在看过 holdout 后才成立；
- 解释不清收益来自 Cognition、Performance 还是 Judge 偏好。

此时允许的动作是：删除最近补丁、还原 accepted baseline、重新定位权威边界、改变中间表示、换模型或补真实数据；不允许再叠一层规则。

### 18.8 变更记录与回滚

每个 accepted change 保存：

- experiment ID、hypothesis 和 owner；
- baseline/candidate 源码 hash；
- 模型、Prompt、采样和评测集 hash；
- 配对输出、盲审原文、人工校准结果；
- hard invariant 结果；
- 成本与延迟；
- 晋升日期和一键回滚 flag。

新 baseline 胜出后删除旧实验实现，只保留可恢复版本标签和证据。线上出现人格质量退化时先切回 accepted baseline，再分析；禁止在故障路径上临时增加语义特判。

---


## 19. 架构决策记录

### ADR-001：NPC Runtime 是核心，Seedance 是 Adapter

**决定**：所有人物选择和表演语义在 Seedance 之前完成。  
**原因**：未来需要游戏场景；视频约束不能污染人物。  
**代价**：需要维护媒介无关 PerformanceBeat 和多个 Adapter。

### ADR-002：显式 U → X → Y

**决定**：决策、客观解析和表达分层。  
**原因**：防止助手人格覆盖人物，防止台词篡改世界事实。  
**代价**：调用链更长，需要结构化验证和缓存。

### ADR-003：Host 是客观事实源

**决定**：NPC 只提出意图，世界效果由 Host 解析。  
**原因**：游戏集成和可重放状态需要确定事实权威。  
**代价**：场景必须提供明确 affordance 和 action contract。

### ADR-004：人格语义化，不使用反应概率矩阵

**决定**：人格只塑造注意、判断和表达。  
**原因**：硬映射会把人物变成类型模板。  
**代价**：需要通过评测而不是规则表验证人物差异。

### ADR-005：事件溯源与分支晋升

**决定**：SceneActor canonical record 由已提交事件构成，排练状态由事件重建；外部游戏世界通过 Host receipt 对账。  
**原因**：支持重放、游戏状态、多人排练和安全晋升。  
**代价**：需要 revision、幂等和事件迁移策略。

### ADR-006：质量评审后置且盲化

**决定**：评审只看干净表演；默认不逐拍阻塞创作。  
**原因**：避免评审器成为逐拍导演并增加延迟。  
**代价**：需要支持局部定位和候选返修。

---

## 20. 待 Review 的开放问题

1. 第一阶段是严格限定双 NPC，还是从合同上直接允许 3 NPC、实现先限制为 2？
2. NPC 的 `dramatic_lenses` 是否完全可选，还是 Demo 人物必须具备欲望、底线和命门？
3. 表演层是否允许生成短暂第一人称即时念头，还是第一阶段只输出外部可观察表演？
4. Scene Host 的 affordance 是作者显式提供，还是允许 Scene Author AI 提案后确认？
5. 每拍默认使用一次融合 Appraisal+U 调用和一次 Y 调用，是否接受该延迟目标？
6. Seedance 展示应逐 Beat 生成，还是允许多个连续 Beat 合并成一个 Shot？
7. Game SceneHost 首个目标引擎和最小 ActionCommand 协议是什么？
8. 长期记忆何时进入第一阶段：排练完成后晋升，还是暂时只保留场内状态？
9. 盲审失败默认局部重做 Y、重做 U，还是只给人工诊断？
10. 产品首页应该以“选人物开始排练”还是“创建场景测试人物”为主要入口？

---

## 21. 参考依据

- Tellwright `src/npc_world`：主观感知、U/Y 分离、场景压力和表演实验经验；
- Tellwright《无主之名》人物主观性与表达控制：有限视点、情绪连续线、身体连续性、反应缺口、声线波形和 AI 味质量门；
- Agentopia `npc_runtime`：Host 无关 Persona/Cognition、U→X→Y、事件溯源、排练分支、客观效果和盲审；
- [Seedance 2.0 官方发布](https://seed.bytedance.com/en/blog/official-launch-of-seedance-2-0)；
- [Seedance 2.0 技术报告](https://arxiv.org/abs/2604.14148)；
- [BytePlus Seedance 2.0 Prompt Guide](https://docs.byteplus.com/en/docs/ModelArk/2222480)；
- [BytePlus Video Generation API](https://docs.byteplus.com/en/docs/ModelArk/1520757)；
- [Adobe Camera Shots and Angles](https://www.adobe.com/sg/creativecloud/video/production/cinematography/camera-shots-and-angles.html)；
- [DreamShot: Personalized Storyboard Synthesis with Video Diffusion Prior](https://arxiv.org/abs/2604.17195)。
