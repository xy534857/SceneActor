---
name: script-to-video
description: |
  剧本+人设图 → 长视频的完整制作工作流（seedance/VS 2.0 链路）。
  综合三个公开 skill 的精华（songguoxs/seedance-prompt-skill 的时间戳分镜与
  多段衔接、zhaihao118/Micro-Drama-Skills 的角色圣经与分镜图参考、
  huangserva/xyz-video-skill 的单一真相源/尾帧锚定/链式衔接/双阶段审查），
  结合我们自己踩过的坑（体型漂移、音色失效、台词裁断、人类乱入）。
  触发词：「剧本转视频」「生成长视频」「短剧制作」「seedance 流程」。
  执行工具：scripts/run_skit_video.py（生成+验证+拼接）+ 本文档的工作流决策。
---

# Script-to-Video · 剧本到长视频

> 一句话架构：**一致性是资产问题，连贯性是分镜问题，质量是审查问题。**
> 三者分开治理，不要指望一段 prompt 同时解决三件事。

## 与三个参考 skill 的关系

| 借鉴 | 来自 | 我们的落地 |
|---|---|---|
| 时间戳分镜（0-3s/4-8s 逐段控制） | seedance-prompt-skill | shot 内 `time_beats`，写进视频 prompt |
| >15s 多段拼接 + 衔接点声明 | seedance-prompt-skill | 分镜表 + 链式尾帧衔接 |
| 角色圣经 + 每角色参考图索引 | Micro-Drama-Skills | 单张 model sheet 裁成单人立绘（同风格保证） |
| 6宫格分镜图作为视频参考 | Micro-Drama-Skills | 分镜图 = 构图权威；**体型不归分镜管**（我们的教训） |
| 单一真相源（外貌只定义一处） | xyz-video-skill | prompt 只说"以附图为准"，禁止散文重述外形 |
| 尾帧锚定（end_frame 必填防漫游） | xyz-video-skill | 每镜 `end_state`；末镜+被链镜强制 |
| chain_from_previous（真实尾帧接首帧） | xyz-video-skill | 同场景连续动作镜从前镜视频抽尾帧作参考 |
| 单向运动法则（一镜内禁折返） | xyz-video-skill | 分镜自检清单第 3 条 |
| 两阶段质量审查（粗筛+母模型裁定） | xyz-video-skill | whisper/声纹粗筛 + gemini 逐帧裁定 |

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
□ 一镜内动作无折返
□ 每镜 end_state 非空且是"已到达的状态"不是"可能发生的事"
□ 台词字数 ≤ 时长×3.5×0.85
□ 链式镜的前镜 end_state 精确（它会变成下镜首帧）
```

## Prompt 组装（每镜结构固定，由 skit_pipeline.py 生成）

```
IDENTITY: 附图立绘定义每个角色的确切外形——只引用，不描述。
STAGE: 场景描述（全片共享一段文字，逐字相同）
SHOT: 镜头
SPEAKING CHARACTER: 说话人（+ 其他在场角色，标注 NOT speaking）
ACTION: 动作 + 口型同步 + time_beats（如有）
END STATE: 结束落点
DIALOGUE (语言, 声线描述, cloned from reference audio): 「台词」
STRICT: NO humans; NO subtitles/captions/text overlays
STYLE: 风格句（全片逐字相同）+ 只要人声和房间底噪，无音乐
```

引用挂载（tencent-vod 路线）：
- 每个**在场**角色一张立绘 `identity_anchor`
- 场景主图 `scene_style`
- 说话人干声 `reference`（Audio）
- 链式镜：前镜尾帧再挂一张 `identity_anchor`
- ⚠️ `first_frame` 不能与其他 reference 混用（API 限制）

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
- 拼接用 take 表：`--assemble takes.json`，每镜选最佳 take

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
