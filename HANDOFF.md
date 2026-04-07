# HANDOFF

- Updated: 2026-04-07
- Scope: `/home/lwz/rfl-dev/c2saferust-kernel-tooling`
- Purpose: 新开 agent / 新会话时的快速接力入口
- Companion document: `task_context.md`

## 这份文件是做什么的

`HANDOFF.md` 是给“新会话快速接手当前工具仓”用的短版说明书。

它不替代 `task_context.md`：

- `HANDOFF.md`
  - 保持短、稳定、面向执行
  - 用于新 agent 在上下文紧张时快速进入状态
- `task_context.md`
  - 保持长、细、面向历史与推理
  - 用于完整复盘、审计和追踪关键结论来源

如果只能先读一个文件，先读本文件；开始正式工作前，再补读 `task_context.md`。

## 当前主工作目录

- canonical tool repo：
  - `/home/lwz/rfl-dev/c2saferust-kernel-tooling`

不要把以下目录当成工具主仓：

- `/home/lwz/rfl-dev/archive/tool-flow-history/*`
- `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/*`
- `/home/lwz/rfl-dev/archive/rfc-history/*`
- `goldfish-*` RFC / runtime / baseline worktree

这些目录现在应视为：

- 外部 Linux 集成树
- 历史 benchmark 样本
- driver / abstraction 验证环境

而不是工具主仓本身。

## 当前总目标

当前主目标是继续推进一个面向 Linux kernel / Rust-for-Linux 的
`C2SafeRust` 工具，而不是继续围绕单个驱动或单封 RFC 邮件工作。

更具体地说，当前目标是让工具能够：

- 从内核 C 模块或外部 source tree 出发
- 经过 profile / intake / patch-plan / abstraction-plan / translation-plan /
  safety / oracle / benchmark
- 形成可重复、可审计、可 ground-truth 比较的迁移流程

## 当前工具状态

当前已经形成的主干能力：

- profile loader
- module / family / scenario / rule-pack 体系
- static intake / planning artifacts
- safety / soundness verifier
- oracle runner registry
- benchmark 叠加层
- external-source intake
- command-semantics gate

当前 family 覆盖范围：

- `net-link-type`
- `net-phy`
- `pci-miscdevice`

当前 benchmark 叠加层已支持：

- `bootstrap-benchmark`
- `refresh-benchmark-summary`
- `build-difference-metrics`
- `collect-benchmark-portfolio`

## 当前最重要的样本

- `nlmon`
  - 第一条完整 link-type 闭环样本
- `ax88796b`
  - 当前 ground-truth / `net-phy` benchmark 样本
- `goldfish_address_space`
  - external-source + runtime differential 案例样本
- `vsockmon`
  - planning-only / 缺失抽象探针样本
- `et1011c` / `qsemi`
  - 已闭环的 `net-phy` family 抽象样本

## 当前推荐阅读顺序

新开会话时，推荐按这个顺序读：

1. `HANDOFF.md`
2. `task_context.md`
3. `README.md`
4. `docs/tooling-abstraction-retrospective.md`
5. `docs/ground-truth-benchmarks.md`

按任务需要再补读：

- `docs/new-module-checklist.md`
- `docs/next-target-evaluation.md`
- `docs/standalone-tool-repo.md`
- `docs/goldfish_address_space/README.md`

## 当前主线判断

下面这些判断在新会话里应视为当前默认前提：

- 当前工具主仓是 `c2saferust-kernel-tooling`
- 当前目标是“设计和验证工具”，不是“继续 goldfish RFC 邮件工作”
- 外部 Linux worktree 只负责 driver / abstraction / oracle 落地
- `ax88796b` 是当前 ground-truth benchmark 的核心样本之一
- benchmark 设计当前强调：
  - blind-first
  - freeze-before-compare
  - provenance-required

## 当前最可能的下一步任务

如果没有新的外部指令，优先级通常应落在这些方向：

1. 完善 benchmark 闭环
2. 补 `provenance-log.jsonl` 的自动接入
3. 在独立工具仓上继续沉淀 family/profile/runner 文档
4. 用外部 Linux worktree 重放 `nlmon` / `ax88796b` / `goldfish` 样本

## 新会话启动模板

新开 agent 时，建议直接使用下面这段说明：

```text
工作目录使用 /home/lwz/rfl-dev/c2saferust-kernel-tooling。

先阅读：
- HANDOFF.md
- task_context.md
- README.md
- docs/tooling-abstraction-retrospective.md
- docs/ground-truth-benchmarks.md

当前目标是继续推进面向 Linux kernel / Rust-for-Linux 的 C2SafeRust 工具本身。

已知现状：
- scripts/c2saferust 已形成 profile + intake + safety + oracle + benchmark 叠加层
- net-link-type / net-phy / pci-miscdevice 三个 family 已进入工具主线
- ax88796b 已接入 benchmark/profile/intake 流程
- goldfish_address_space 已作为 external-source + differential 案例沉淀到工具文档
- benchmark 设计要求 blind-first、freeze-before-compare、provenance-required

请先总结当前工具状态，再继续执行我给出的下一项任务。
```

## 更新规则

这份文件需要和 `task_context.md` 一样维护，但职责更窄。

最低只要做这 4 件事：

1. 改 `Updated` 日期
2. 检查“当前主目标”是否变化
3. 检查“当前主工作目录”是否变化
4. 检查“当前最可能的下一步任务”是否变化
