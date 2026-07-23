---
name: script-to-video
description: |
  剧本+人设图 → 长视频（seedance/VS 2.0 链路）。声明式 project.json 驱动：
  五维一致性合同（人物/场景/空间/物品/站位）+ 链式衔接 + 两阶段审查。
  触发词：「剧本转视频」「生成长视频」「短剧制作」「seedance 流程」。
  执行工具：scripts/run_skit_video.py；schema 与 prompt 组装在
  src/sceneactor/skit_pipeline.py（加载即校验，违法输入直接拒绝）。
---

# Script-to-Video · 剧本到长视频

> 一句话架构：**一致性是资产问题，连贯性是分镜问题，质量是审查问题。**
> 三者分开治理，不要指望一段 prompt 同时解决三件事。

## 五维一致性合同（本 skill 的核心，全部落在 `skit_pipeline.py`）

一致性不是"写得细"，是**每一维都有声明处、注入处、校验处**。声明了才检查，
违反了可判定——这是把三个参考 skill 的做法收敛后再硬化的结果：

| 维度 | 声明处（project.json） | 注入 prompt 段 | 硬校验 |
|---|---|---|---|
| 人物 | `characters[].portrait`（单 model sheet 裁切） | `IDENTITY:` 只引用附图 | 每在场角色一张 `identity_anchor`，speaker 必在画内 |
| 场景 | `stage`（全片一段文字）+ `scene_ref` 图 | `STAGE:` 逐字相同 + `scene_style` 引用 | — |
| 空间结构 | `continuity.spatial_layout/environment/character_states`（**逐条事实，禁散文**） | `STABLE FACTS (must hold):` 逐条列出 | 与 `shot_delta` 冲突的事实自动挂起，防一条 prompt 自相矛盾 |
| 物品 | `props[]` 实体注册表：`count`/`holder`(精确到手)/`location`/`persistent_state` | `PROPS (unique entities): exactly 1x …, held by …, never duplicated, never teleported` | 未注册物品被镜头引用 → 加载即报错；holder 必须是角色 |
| 站位 | 每镜 `pose_contract`（只写物理支撑关系）+ `gaze_target` + `shot_delta` | `POSE CONTRACT:` / `GAZE:` / `SHOT DELTA (the ONLY things allowed to change):` | 链式镜的前镜必须有 `end_state`（它就是下镜首帧），否则加载即报错 |

**shot_delta 是站位一致性的钥匙**：默认一切继承 STABLE FACTS；本镜要动谁，
就把变化写进 `shot_delta`——引擎自动把与 delta 同主语的稳定事实从该镜 prompt
里挂起（"b 始终在右讲台"遇到"b 走到中央"会被移除，"a 始终在左讲台"保留）。
没写进 delta 的变化 = 审查阶段可判定的违规。

**props 注册表治两类经典翻车**：同一把伞被画成两把（`count: 1` + never
duplicated）；托盘从左讲台瞬移到右讲台（`location` 固定 + never teleported）。

**pose_contract 治姿态漂移**：只写重心/支撑点/承重关系（"上身持续斜靠墙面，
支撑点不变"），不写情绪词。缺这层，"倚靠"会被随机画成坐/蹲/站。

## 分镜字段速查（写 project.json 时逐镜决策）

每镜必填四件套：`camera` / `action` / `line` / `duration`([4,15]整数)。
以下按"什么情况必须写"给出决策规则：

| 字段 | 什么情况必须写 | 写法要点 |
|---|---|---|
| `end_state` | 该镜是段落收口、情绪落点、或**下一镜要链它** | 写"已到达的状态"不写"可能发生的事"；被链镜缺它会拒绝加载 |
| `chain_from_previous` | 同场景同角色连续动作 | 跨场景/换角色/景别大跳禁用；渲染时自动抽前镜真实尾帧挂为末位参考图 |
| `time_beats` | 时长>8s 或镜内有多阶段动作 | `"0-3秒：…"` 只写谁在动、动哪里；禁导演抒情 |
| `props_in_shot` | 画面出现注册过的道具 | 引用 `props[]` 里的 prop_id；未注册直接报错 |
| `pose_contract` | 角色姿态整镜不许漂移（倚/蹲/扶） | 只写重心/支撑点/承重关系；禁情绪词 |
| `gaze_target` | "看向谁"本身是戏（对视/回头/发现） | 写目标+方位："对面讲台的水獭" |
| `shot_delta` | 本镜要改变 STABLE FACTS 里的任何事实 | 只列允许的变化；同主语稳定事实自动挂起，没列的变化=审查违规 |
| `extra_constraints` | 上一 take 的 fail 原因 | 重生成时把审查结论翻成新约束（如"无贴纸描边"） |
| `anchor_stills` | 构图必须精确（开场定调镜/复杂多人站位/关键落点） | `first` 或 `first_last`；引擎按 `start_state`/`end_state` 先生成**单帧剧照**（gpt-image-2 + 立绘/场景参考），作为 scene_style 挂给视频任务锁构图。与 `chain_from_previous` 互斥 |
| `start_state` | 写了 `anchor_stills` 就必填 | 与 `end_state` 同语法：写"画面开场已是什么状态" |
| `transition_in` | 时空跳跃（换场景/时间流逝）需要软转场 | `cut`(默认硬切)/`dissolve`(叠化=时间流逝)/`flash`(白闪=情绪重音)；组装时 xfade/fadewhite 执行 |
| `transition_duration` | 非 cut 转场想调节奏 | 秒，默认 0.5，组装时 clamp 到 [0.1,1.5] |

### 三级构图锚定（按需选择，勿全上）

| 层级 | 用什么 | 什么时候 |
|---|---|---|
| 文本 | `time_beats` + `end_state` | 默认。单人、构图简单、动作线性的镜 |
| 剧照 | `anchor_stills: first`(或 `first_last`) | 构图是戏的镜：多人站位、开场定调、精确落点。**单帧剧照，绝不是多格 storyboard 面板**——面板内格间头身比漂移会被视频模型放大（v2 教训） |
| 链帧 | `chain_from_previous` | 同场景同角色连续动作。抽前镜**真实尾帧**（不是剧照）挂末位参考 |

剧照挂的是 scene_style 参考而非 first_frame：tencent-vod 的 first_frame 不能与
其他参考混用，且像素级续接会继承压缩伪影（v1 画质退化教训）。

## 资产阶段（一致性的根，最重要）

**做错这一步，后面全是补丁。** 顺序固定：

1. **Model sheet**：一张图上画全所有角色（gpt-image-2，输入官方立绘/照片参考），
   同一张纸 = 同一画风、同一头身比。绝不各自单独生成。
2. **裁单人立绘**：从 model sheet 按角色裁切，无背景。这是每个分镜的
   `identity_anchor`。**多人同框的镜头挂多张立绘**。
3. **场景主图**：以 model sheet 为风格参考生成舞台/场景全景一张，
   作为所有镜头的 `scene_style`。多场景则每场景一张 + 多角度表。
4. **干声音色**：角色语音参考必须 demucs 分离人声 → 去静音 → loudnorm -16LUFS。
   带 BGM 的原片剪辑会让音色克隆随机失效（实测 5/15 镜翻车）。
5. **上传归档**：所有资产传公网桶，`uploads.json` 留档，可复跑。

## 分镜阶段（连贯性的根）

从过审剧本切分镜，规则按优先级：

1. **一镜一说话人一动作**：台词按说话人切；同一人长台词按 15s 上限再切
   （中文 ~3.5 字/秒 × 0.85 余量）。VS 2.0 时长必须是 [4,15] 整数。
2. **每镜四件套**：`camera`（镜头）/ `action`（动作+表情，物理可见的）/
   `line`（台词）/ `end_state`（结束时画面落点——没有它模型随机漫游）。
3. **时间节拍**：>8s 或多阶段动作的镜头写 `time_beats`
   （"0-3秒：…；4-8秒：…"），只写谁在动、动哪里，禁止导演抒情。
4. **单向运动**：一镜内朝向/位移/景别只许单向变化。需要折返 = 拆镜。
5. **链式衔接**：同场景同角色连续动作 → 后镜标 `chain_from_previous`，
   渲染时抽前镜真实尾帧作参考图。跨场景/换角色/景别大跳 = 断链。
6. **镜头语言变化**：全片不许超过连续两镜同景别同机位；
   爆发拍近景、铺垫拍中景、收场拍全景。
7. **强动作镜**（扑/摔/打/抢）：`action` 必须写节拍链
   （起势→爆发→落点），不许写剧情摘要。

分镜自检（写完必查）：
```
□ 相邻镜 end_state → 下镜起始状态连续（位置/持物/湿干/人数）
□ 一镜内动作无折返（朝向/位移/景别只许单向变化）
□ 每镜 end_state 非空且是"已到达的状态"不是"可能发生的事"
□ 台词字数 ≤ 时长×3.5×0.85
□ 角色要移动的镜写了 shot_delta；没动的镜没写
□ 道具出场都引用了 props_in_shot；没有画面外道具凭空出现
□ pose_contract 只有物理支撑关系，没有情绪词
□ 加载检验通过：python3 -c "from sceneactor.skit_pipeline import SkitProject; SkitProject.load('project.json')"
```

## Prompt 组装（每镜结构固定，由 skit_pipeline.py 生成，顺序即权威）

```
IDENTITY: 附图立绘定义每个角色的确切外形——只引用，不描述。
STAGE: 场景描述（全片共享一段文字，逐字相同）
STABLE FACTS (must hold): 逐条空间/环境/角色稳定事实（与本镜 delta 冲突的自动挂起）
PROPS (unique entities): exactly 1x …, held by …, never duplicated, never teleported
SHOT: 镜头
SPEAKING CHARACTER: 说话人（+ 其他在场角色，标注 NOT speaking）
ACTION: 动作 + 口型同步
POSE CONTRACT: 支撑关系（如有）
GAZE: 视线目标（如有）
SHOT DELTA (the ONLY things allowed to change): 本镜许可变化（如有）
COMPOSITION: 附图剧照定义本镜开场(/收尾)构图——外形仍归立绘（anchor_stills 镜）
START STATE: 开场已达状态（如有）
TIMELINE: 0-3秒：…（如有）
END STATE: 结束落点（如有）
CONTINUITY: 最后一张附图是前镜末帧（链式镜）
DIALOGUE (语言, 声线描述, cloned from reference audio): 「台词」
STRICT: NO humans; NO subtitles/captions/text overlays
STYLE: 风格句（全片逐字相同）+ 只要人声和房间底噪，无音乐
```

引用挂载（tencent-vod 路线）：
- 每个**在场**角色一张立绘 `identity_anchor`
- 场景主图 `scene_style`
- anchor_stills 镜：首(/尾)帧剧照再挂 `scene_style`（锁构图不锁像素）
- 说话人干声 `reference`（Audio）
- 链式镜：前镜尾帧再挂一张 `identity_anchor`
- ⚠️ `first_frame` 不能与其他 reference 混用（API 限制）——所以构图锚
  一律走 scene_style 参考，绝不走 first_frame

## 生成与审查（质量的根：两阶段，全自动粗筛+模型裁定）

用 `scripts/run_skit_video.py`（upload→plan→submit→poll→verify→assemble）：

**阶段1 粗筛（脚本自动）**
- whisper 转写 vs 台词：相似度 ≥0.75；**尾部核对**（末5字必须出现，防裁断）
- resemblyzer 声纹 vs 干声锚：说话人判定
- 时长/文件完整性

**阶段2 裁定（母模型执行,不许跳过）**
- 每镜抽 3 帧（首/中/尾）→ gemini/自看，核对：
  角色=立绘？领带/疤痕/标记对？体型头身比稳定？无人类乱入？无字幕烧入？
  end_state 达成？
- 粗筛可疑但可能是口音误判的（同音字），用 gemini 精听仲裁
  （"逐字转写+最后一个字是否说完整"），**不确定就不裁、不重生成**

**迭代规则**
- 只重生成 fail 的镜，`--shots z11,z12 --tag v2`
- 重生成时把 fail 原因翻成新约束加进 `extra_constraints`
  （如"角色与背景自然融合，无贴纸描边"）
- 拼接用 take 表：`--assemble takes.json`，每镜选最佳 take。
  全片纯 cut → concat 无重编码；任一镜声明 dissolve/flash → 自动切换
  xfade/fadewhite 滤镜链（acrossfade 同步过渡音频）

## 已踩过的坑（不要再踩）

| 症状 | 根因 | 解法 |
|---|---|---|
| 人物忽大忽小 | 各镜引用了不同参考图/分镜图各格头身比不稳 | 单一 model sheet 裁立绘；prompt 声明"分镜只定构图不定体型" |
| 音色时灵时不灵 | 音频参考带 BGM | demucs 干声 + loudnorm |
| 台词说一半被切 | 信 gemini 报的结束秒数 | 尾部核对+语速下限双守卫；不确定不裁 |
| 画面乱入真人 | 未显式禁止 | STRICT: NO humans（每镜必带） |
| 分镜图页间画风漂移 | 每页独立生成 | 链式：上一页成品作下一页参考图1 |
| 面板台词错别字 | 图像模型写不了长中文 | 面板只写镜头号，台词靠表演 |
| 3镜页面板拉成超宽横幅 | 模型填满页面 | 面板一律16:9，空格画斜叉 |
| 声线像但不是本人 | seedance 是参考风格化不是频谱克隆 | 接受，或后期 TTS 克隆换音轨 |

## 反模式

- ❌ 靠一段超长 prompt 描述角色外形 → 永远用图片锚
- ❌ 一次生成 >15s → 必须分镜+拼接
- ❌ 全片一次全过审才拼 → 逐镜验证逐镜换 take
- ❌ 跳过粗筛直接人眼看全片 → 15 镜逐个人看不现实且漏检
- ❌ 分镜图当体型权威 → 分镜只管构图；体型永远归立绘
