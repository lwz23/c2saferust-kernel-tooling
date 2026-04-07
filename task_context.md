# TASK_CONTEXT

- Quick start handoff: `HANDOFF.md`
- Updated: 2026-04-07
- Scope: `/home/lwz/rfl-dev/c2saferust-kernel-tooling`
- Snapshot source: standalone tool repo + local external Linux worktrees + benchmark artifacts

## Task Goal

这个仓库现在是面向 Linux kernel / Rust-for-Linux 的 `C2SafeRust` canonical tool repo。

核心目标不是“直接做出某一个 Rust 驱动”，而是：

- 让工具能够从 in-tree C 模块或 external-source 模块出发，
- 沿着 Rust-for-Linux 的标准迁移流程，
- 产出静态 intake、抽象层计划、驱动计划、safety verdict、oracle 结果与 benchmark 工件，
- 并在外部 Linux worktree 中完成验证与回放。

## Repo Role

当前仓库负责：

- `scripts/c2saferust/*.py`
- `scripts/c2saferust/profiles/`
- `scripts/c2saferust/oracle_runners.py`
- `scripts/c2saferust/safety_verifier.rs`
- benchmark 叠加层
- tool-only 文档与交接入口

外部 Linux worktree 负责：

- Rust abstraction 代码
- Rust driver 代码
- Kbuild / bindings / helpers patch
- `Documentation/rust/c2saferust/<module>/` artifact
- 模块级 smoke / runtime / benchmark 记录

## Current Status

### 主线定位

- 当前 canonical tool repo：
  - `/home/lwz/rfl-dev/c2saferust-kernel-tooling`
- 2026-04-07 已把最新 benchmark 支撑代码、`ax88796b` profile、goldfish retrospective
  文档、`HANDOFF.md` 方向迁入本仓。
- 同日开始对旧 in-tree tool-flow 树、blind benchmark 树和 RFC 历史树做统一归档，
  后续不再把它们留在 `worktrees/` 根下。

### 当前能力面

当前工具已经形成：

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

### 当前 benchmark 设计

当前 ground-truth benchmark 的结构化约束已经固定为：

- blind-first
- freeze-before-compare
- provenance-required
- claim-ceiling

固定工件包括：

- `module-manifest.json`
- `planner-contract.json`
- `validator-ready-summary.json`
- `difference-metrics.json`
- `differential-oracle-run-record.json`
- `lifecycle-oracle-run-record.json`
- `provenance-log.jsonl`

### 当前代表样本

- `nlmon`
  - 第一条完整 `net-link-type` 闭环样本
- `vsockmon`
  - planning-only / 缺失抽象探针样本
- `et1011c`
  - `net-phy` 第一阶段抽象闭环样本
- `qsemi`
  - `net-phy` 第二阶段抽象闭环样本
- `ax88796b`
  - 当前 `net-phy` ground-truth benchmark 样本
- `goldfish_address_space`
  - `pci-miscdevice` + external-source + runtime differential 案例样本

## Key External Trees

当前仍然有价值的外部树包括：

- `/home/lwz/rfl-dev/archive/tool-flow-history/nlmon-tooling-clean-v1`
  - 已归档的 Linux 集成树与工具演化历史
- `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/ax88796b-blind-rust`
  - `ax88796b` 历史 blind benchmark 与 runtime diff 证据
- `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/ax88796b-blind-validation`
  - `ax88796b` fresh blind validation 证据
- `/home/lwz/rfl-dev/worktrees/goldfish-address-space-upstream-v1`
  - goldfish candidate / runtime differential 树
- `/home/lwz/rfl-dev/worktrees/goldfish-address-space-c-baseline-v1`
  - goldfish baseline / runtime differential 树
- `/home/lwz/rfl-dev/archive/rfc-history/goldfish-address-space-rfc-v1`
  - goldfish RFC 邮件整理历史

这些目录是证据源和实验树，不是新的工具主仓。

## Current Conclusions

截至当前，可以把下面这些判断当成默认前提：

- 工具已经不是单模块脚本，而是 family/profile 驱动的 Linux/RFL 专用工具链。
- `net-link-type` 与 `net-phy` 的主干抽象资产已经被真实样本证明可复用。
- `pci-miscdevice` 方向目前主要由 `goldfish_address_space` 案例推动，已沉淀出
  external-source intake、runner 分层和 command-semantics gate。
- `ax88796b` 当前在工具主线上已经完成 profile / benchmark 接入；历史 blind worktree
  中存在更完整的 compare / runtime evidence，但当前主仓还需要把这条 benchmark
  进一步做成可重复的一键闭环。

## Default Next Steps

如果没有新的外部指令，优先级通常应落在这些方向：

1. 完善 benchmark 闭环与 `provenance-log.jsonl` 自动接入
2. 把 ground-truth 结果整理成可复现 portfolio
3. 用外部 Linux worktree 继续验证 `ax88796b` 与 `goldfish_address_space`
4. 继续收敛 tool-only 文档与评估流程

## Reading Order

新会话推荐按这个顺序进入：

1. `HANDOFF.md`
2. `task_context.md`
3. `README.md`
4. `docs/tooling-abstraction-retrospective.md`
5. `docs/ground-truth-benchmarks.md`
6. 按任务需要补读：
   - `docs/new-module-checklist.md`
   - `docs/next-target-evaluation.md`
   - `docs/goldfish_address_space/README.md`

## Boundaries

下面这些话当前还不能夸大：

- 不能说工具已经对任意内核驱动自动迁移
- 不能说所有 benchmark 都已经达到 `differential_validated`
- 不能说 goldfish 已经变成通用 family 闭环样本
- 不能说 benchmark 工件已经完全自动接入所有 agent-side provenance

当前更准确的说法是：

- 工具已经形成可复用的 family/profile/oracle/benchmark 框架
- 但 benchmark 自动化、provenance 完整性与更多样本回放仍是接下来要补的主线工作
