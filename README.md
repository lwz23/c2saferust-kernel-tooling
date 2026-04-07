# C2SafeRust Kernel Tooling

`C2SafeRust Kernel Tooling` 是当前面向 Linux kernel / Rust-for-Linux 的 canonical
工具仓库。

它不携带 Linux 内核源码本身，而是面向外部目标 kernel tree 工作：

- 读取目标模块的 C 源文件或外部 source tree
- 生成结构化 planning / safety / oracle / benchmark artifact
- 约束 abstraction 扩展与 Rust driver 生成流程
- 将 artifact 默认回写到目标 kernel tree

当前它服务的目标不是“任意 C 项目通用翻译器”，而是：

- Linux kernel 内核驱动
- Rust-for-Linux abstraction-first 迁移流程
- `unsafe` / soundness / oracle / benchmark 受 artifact 约束的闭环

## Repo Layout

- `HANDOFF.md`
  - 新会话快速接手入口
- `task_context.md`
  - 当前工具状态、样本与工作模式的长版上下文
- `scripts/c2saferust/`
  - CLI、planning pipeline、safety gate、oracle runner、benchmark 叠加层、profiles
- `docs/new-module-checklist.md`
  - 新模块复用流程 checklist
- `docs/ground-truth-benchmarks.md`
  - blind-first / freeze-before-compare benchmark 设计
- `docs/tooling-abstraction-retrospective.md`
  - 已沉淀抽象资产与样本复盘
- `docs/next-target-evaluation.md`
  - 候选方向与下一目标筛选记录
- `docs/goldfish_address_space/`
  - external-source + differential oracle 案例复盘

## Working Model

推荐工作模式：

- 一个独立工具主仓库
- 多个外部 `rust-next` / `linux-next` worktree

例如：

```bash
TOOL_REPO=/path/to/c2saferust-kernel-tooling
LINUX_TREE=/path/to/linux-rust-next-worktree
MODULE=drivers/net/nlmon.c
```

artifact 默认写回：

```text
<linux-tree>/Documentation/rust/c2saferust/<module-id>/
```

也可以通过 `--artifact-root` 改写输出根目录。

## Current Capability

当前主干能力包括：

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

## Quick Start

先为目标模块生成完整 planning artifact：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py refresh-artifacts \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE
```

或显式指定输出目录：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py bootstrap-module \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE \
  --output-dir $LINUX_TREE/Documentation/rust/c2saferust/nlmon
```

如果输入是外部源码树：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py bootstrap-module \
  --kernel-tree $LINUX_TREE \
  --profile-id goldfish_address_space \
  --source-tree /path/to/external/source \
  --output-dir $LINUX_TREE/Documentation/rust/c2saferust/goldfish_address_space
```

常用后续命令：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py verify-safety \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE \
  --output $LINUX_TREE/Documentation/rust/c2saferust/nlmon/safety-verdict.json
```

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py gate-agent-candidate \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE \
  --output $LINUX_TREE/Documentation/rust/c2saferust/nlmon/agent-gate-report.json
```

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py run-oracle \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE \
  --output $LINUX_TREE/Documentation/rust/c2saferust/nlmon/toolchain-run-record.json
```

ground-truth benchmark 常用入口：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py bootstrap-benchmark \
  --kernel-tree $LINUX_TREE \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --output-dir $LINUX_TREE/Documentation/rust/c2saferust/benchmarks/ax88796b-ground-truth-strict-v2
```

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py collect-benchmark-portfolio \
  --root $LINUX_TREE/Documentation/rust/c2saferust/benchmarks \
  --output $LINUX_TREE/Documentation/rust/c2saferust/benchmarks/portfolio.json \
  --csv-output $LINUX_TREE/Documentation/rust/c2saferust/benchmarks/portfolio.csv
```

## Supported Families / Samples

当前仓库内已经包含的 profile / rule-pack / runner / benchmark 样本包括：

- `net-link-type`
  - `nlmon`
  - `vsockmon`
- `net-phy`
  - `et1011c`
  - `qsemi`
  - `ax88796b`
- `pci-miscdevice`
  - `goldfish_address_space`

当前 benchmark 样本包括：

- `nlmon-ground-truth`
- `ax88796b-ground-truth`
- `ax88796b-ground-truth-strict-v2`
- `rnull-calibration`
- `cpufreq-dt-clean-restart`

这表示它已经不再只是 `nlmon` 专用脚本，而是一个可跨 family 复用、支持 blind-first
ground-truth 评估的 Linux/RFL 专用工具仓。

`ax88796b-ground-truth-strict` / `ax88796b_blind_strict` 仍保留在仓库中，但只作为
历史校准资产，不应用于正式 blind run。

## Safety Position

这个工具的强约束是：

- driver 侧必须能做到 `#![forbid(unsafe_code)]`
- driver 侧不得直接使用 `bindings::*`
- soundness obligation 必须显式结构化
- oracle / smoke / benchmark 结果要能反向修正 artifact，而不是直接人工绕过
- ground-truth benchmark 必须遵守：
  - blind-first
  - freeze-before-compare
  - provenance-required

如果某模块做不到这些约束，结论应当是：

- abstraction 还不够

而不是：

- 先在驱动里写 `unsafe` 凑通功能

## Self Test

仓库自身的快速回归：

```bash
python3 -m py_compile scripts/c2saferust/*.py
python3 -m unittest scripts.c2saferust.tests.test_tool_cli
```

当前测试覆盖独立仓库模式下的：

- planning artifact 生成
- `--kernel-tree` / `--artifact-root` 行为
- safety / gate / oracle feedback 流程
- benchmark bootstrap / summary / difference metrics / portfolio
- `net-link-type` / `net-phy` / `pci-miscdevice` family 的 profile 驱动能力

## References

- [HANDOFF.md](HANDOFF.md)
- [task_context.md](task_context.md)
- [docs/new-module-checklist.md](docs/new-module-checklist.md)
- [docs/ground-truth-benchmarks.md](docs/ground-truth-benchmarks.md)
- [docs/standalone-tool-repo.md](docs/standalone-tool-repo.md)
- [docs/tooling-abstraction-retrospective.md](docs/tooling-abstraction-retrospective.md)
- [docs/next-target-evaluation.md](docs/next-target-evaluation.md)
- [docs/goldfish_address_space/README.md](docs/goldfish_address_space/README.md)
