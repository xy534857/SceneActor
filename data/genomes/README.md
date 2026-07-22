# SceneActor Cognitive Genome Database

目标：200 名公开人物的可组合认知基因组，中国人物优先（约 120/200）。

## 目录

```
data/genomes/
├── index.json                 # 200 人名单 + 采集状态
├── schema.json                # 单条记录 schema
├── records/<person_id>.json   # 最终基因组记录
└── research/<person_id>/      # 六维原始调研
    ├── 01-writings.md
    ├── 02-conversations.txt   # 逐字语料，每行 [场合-年份] 原话
    ├── 03-expression.md
    ├── 04-external.md
    ├── 05-decisions.md
    └── 06-timeline.md
```

## 六维证据到基因组字段

| 维度 | 基因组字段 |
|---|---|
| 01 著作/长文 | core_models, heuristics |
| 02 长对话/庭审/听证/直播逐字稿 | trait_axes, emotion_triggers, internal_conflicts |
| 03 碎片表达 | voice 层（不进基因组，编译 Persona 时使用） |
| 04 他者批评/法院认定 | blind_spots（唯一有效证据） |
| 05 决策记录与结果 | value_weights, internal_conflicts |
| 06 时间线 | 轴漂移、冲突成因、时效性 |

规则详见 `skills/persona-forge/SKILL.md`。

## 状态

`planned → collecting → research_complete → distilled → validated`

validated 必须满足：六维齐全、02 ≥150 行/≥3 场合/≥1 压力场合、
CognitiveGenome schema 通过、因果规则可解析、编译 Persona 通过、短场盲审通过。
