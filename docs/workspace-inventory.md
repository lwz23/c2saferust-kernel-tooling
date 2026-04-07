# Workspace Inventory

- Updated: 2026-04-07
- Root: `/home/lwz/rfl-dev`
- Canonical tool repo: `/home/lwz/rfl-dev/c2saferust-kernel-tooling`

本文档用于把当前 `rfl-dev` 工作区按“现流程产物 / 历史 blind 资产 / 交付历史 /
一次性构建产物”重新分类，作为后续清理和归档的唯一人工说明。

## 分类规则

### `active-validation`

满足以下至少一项：

- 当前仍直接服务 `c2saferust-kernel-tooling` 的外部回放验证
- 树内保留 `Documentation/rust/c2saferust/...` 证据，且这些证据仍用于当前工具论证

### `tool-flow-archive`

满足以下条件：

- 明确走过 `scripts/c2saferust` 流程
- 但已不再作为当前主开发或验证入口
- 其价值主要是“历史演化”和“工具迁移前的集成树快照”

### `legacy-agent-benchmark`

满足以下至少一项：

- 树内主要证据位于 `Documentation/rust/lwz-dev/...`
- 文档语境是 `blind-first` / `fresh-validation` / `engineering-grade`
- 其价值主要是前工具时代的 agent / benchmark 方法学验证

### `rfc-history`

满足以下条件：

- 树的主要作用是保留 upstream-clean / RFC split / 邮件整理历史
- 不再参与当前工具闭环

### `build-output`

满足以下条件：

- 目录名是 `/home/lwz/rfl-dev/build*`
- 主要内容是 `O=` 构建输出、QEMU/KUnit 生成物或一次性检查目录

## 当前工作树清单

### 保留为 `active-validation`

- `/home/lwz/rfl-dev/worktrees/standalone-nlmon-replay-v1`
  - 作用：独立工具仓驱动 fresh `nlmon` 回放
- `/home/lwz/rfl-dev/worktrees/standalone-et1011c-replay-v1`
  - 作用：独立工具仓驱动 fresh `et1011c` 回放
- `/home/lwz/rfl-dev/worktrees/goldfish-address-space-upstream-v1`
  - 作用：`goldfish_address_space` candidate / differential 验证树
- `/home/lwz/rfl-dev/worktrees/goldfish-address-space-c-baseline-v1`
  - 作用：`goldfish_address_space` baseline / differential 对照树

### 归档为 `tool-flow-archive`

- `/home/lwz/rfl-dev/worktrees/nlmon-tooling-clean-v1`
  - 说明：旧的工具集成主树，现已由独立工具仓取代
- `/home/lwz/rfl-dev/worktrees/phy-reference-clean-v1`
  - 说明：早期 `net-phy` tool-flow 集成树
- `/home/lwz/rfl-dev/worktrees/phy-reference-clean-v2-qsemi`
  - 说明：`qsemi` 阶段的旧 in-tree tool-flow 集成树

### 归档为 `legacy-agent-benchmark`

- `/home/lwz/rfl-dev/worktrees/nlmon-blind-rust`
- `/home/lwz/rfl-dev/worktrees/ax88796b-blind-rust`
- `/home/lwz/rfl-dev/worktrees/ax88796b-blind-validation`
- `/home/lwz/rfl-dev/worktrees/rnull-blind-rust`
- `/home/lwz/rfl-dev/worktrees/rnull-fresh-validation`
- `/home/lwz/rfl-dev/worktrees/cpufreq-dt-blind-rust`
- `/home/lwz/rfl-dev/worktrees/cpufreq-dt-blind-rerun`
- `/home/lwz/rfl-dev/worktrees/cpufreq-dt-fresh-validation`
- `/home/lwz/rfl-dev/worktrees/drm-panic-qr-blind-rust`

说明：

- 这些树不再作为“当前工具能力”的正向样本
- 但仍保留为历史 benchmark / methodology 证据
- `cpufreq-dt-blind-rust` 当前不在 `/home/lwz/rfl-dev/linux` 的 worktree registry 中，
  归档时按普通历史目录处理，不使用 `git worktree move`

### 归档为 `rfc-history`

- `/home/lwz/rfl-dev/worktrees/goldfish-address-space-rfc-v1`
- `/home/lwz/rfl-dev/worktrees/phy-reference-upstream-clean-v1`
- `/home/lwz/rfl-dev/worktrees/phy-reference-upstream-clean-v2-qsemi`
- `/home/lwz/rfl-dev/worktrees/phy-reference-upstream-clean-split-v1`

## 构建输出清单

### 保留

- `/home/lwz/rfl-dev/build`
  - 说明：当前仍直接链接到 `/home/lwz/rfl-dev/linux`，是活动 build 根

### 删除

- `/home/lwz/rfl-dev/build-ax88796b-blind`
- `/home/lwz/rfl-dev/build-ax88796b-blind-validation`
- `/home/lwz/rfl-dev/build-cpufreq-dt-blind`
- `/home/lwz/rfl-dev/build-cpufreq-dt-blind-rerun`
- `/home/lwz/rfl-dev/build-cpufreq-dt-fresh-validation`
- `/home/lwz/rfl-dev/build-drm-panic-qr-blind`
- `/home/lwz/rfl-dev/build-goldfish-rfc-v1-check`
- `/home/lwz/rfl-dev/build-nlmon-blind-c`
- `/home/lwz/rfl-dev/build-nlmon-blind-c-min`
- `/home/lwz/rfl-dev/build-nlmon-blind-c-serial`
- `/home/lwz/rfl-dev/build-nlmon-blind-rust`
- `/home/lwz/rfl-dev/build-nlmon-blind-rust-min`
- `/home/lwz/rfl-dev/build-nlmon-blind-rust-serial`
- `/home/lwz/rfl-dev/build-nlmon-kasan`
- `/home/lwz/rfl-dev/build-nlmon-kmem`
- `/home/lwz/rfl-dev/build-nlmon-lockdep`
- `/home/lwz/rfl-dev/build-rnull-blind`
- `/home/lwz/rfl-dev/build-rnull-fresh-validation`
- `/home/lwz/rfl-dev/build-rnull-v6.10`

## 清理后的目标形态

- `c2saferust-kernel-tooling` 作为唯一工具主仓
- `worktrees/` 根下只保留 active validation trees
- 归档树统一进入：
  - `/home/lwz/rfl-dev/archive/tool-flow-history`
  - `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks`
  - `/home/lwz/rfl-dev/archive/rfc-history`
- `/home/lwz/rfl-dev` 根下只保留一个活动 build 根：
  - `/home/lwz/rfl-dev/build`
