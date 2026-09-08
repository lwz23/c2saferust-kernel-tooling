# C2SafeRust Kernel Tooling

Local-first tooling for **Linux / Rust-for-Linux** driver migration safety gates.

This repository does **not** carry Linux kernel sources. It operates on an **external** kernel tree:

- Read target module C sources
- Emit structured **planning / safety / oracle** artifacts
- Constrain abstraction expansion and Rust driver generation
- Write artifacts back beside the target tree by default

It is **not** a general C→Rust translator. It targets:

- Linux kernel drivers
- Rust-for-Linux abstraction-first migration
- Closed loops where `unsafe` / soundness / smoke oracles are artifact-constrained

**English reviewer pack (10 minutes):** [docs/reviewer-10min.md](docs/reviewer-10min.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [examples/sample-agent-gate-report.json](examples/sample-agent-gate-report.json)

---

`C2SafeRust Kernel Tooling` 是一个面向 Linux kernel / Rust-for-Linux 的独立工具仓库。

它不携带 Linux 内核源码本身，而是面向外部目标 kernel tree 工作：

- 读取目标模块的 C 源文件
- 生成结构化 planning / safety / oracle artifact
- 约束后续 abstraction 扩展与 Rust driver 生成流程
- 将 artifact 默认回写到目标 kernel tree

当前它服务的目标不是“任意 C 项目通用翻译器”，而是：

- Linux kernel 内核驱动
- Rust-for-Linux abstraction-first 迁移流程
- `unsafe` / soundness / smoke oracle 受 artifact 约束的闭环

## Repo Layout

- `scripts/c2saferust/`
  - CLI、planning pipeline、safety gate、oracle runner、profiles
- `docs/new-module-checklist.md`
  - 新模块复用流程 checklist
- `docs/standalone-tool-repo.md`
  - 为什么可以拆成独立工具仓库，以及边界怎么划分
- `docs/tooling-abstraction-retrospective.md`
  - 当前工具化抽象工作的复盘
- `docs/reviewer-10min.md`
  - grant / external reviewer 10-minute path
- `examples/sample-agent-gate-report.json`
  - illustrative agent-gate report shape

## Working Model

推荐工作模式：

- 一个独立工具仓库
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

## Quick Start

先为目标模块生成完整 planning artifact：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py refresh-artifacts \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE
```

或者显式指定输出目录：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py bootstrap-module \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE \
  --output-dir $LINUX_TREE/Documentation/rust/c2saferust/nlmon
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

oracle 失败后，将反馈回灌到 artifact：

```bash
python3 $TOOL_REPO/scripts/c2saferust/tool_cli.py apply-oracle-feedback \
  --kernel-tree $LINUX_TREE \
  --module-path $MODULE \
  --run-record $LINUX_TREE/Documentation/rust/c2saferust/nlmon/toolchain-run-record.json
```

## Supported Families / Samples

当前仓库内已经包含的 profile / rule-pack / runner 样本包括：

- `net-link-type`
  - `nlmon`
  - `vsockmon`
- `net-phy`
  - `et1011c`
  - `qsemi`

这表示它已经不再只是 `nlmon` 专用脚本，而是开始具备 family/profile 驱动的复用能力。

## Safety Position

这个工具的强约束是：

- driver 侧必须能做到 `#![forbid(unsafe_code)]`
- driver 侧不得直接使用 `bindings::*`
- soundness obligation 必须显式结构化
- oracle / smoke 结果要能反向修正 artifact，而不是直接人工绕过

如果某模块做不到这些约束，结论应当是：

- abstraction 还不够

而不是：

- 先在驱动里写 `unsafe` 凑通功能

## Self Test

仓库自身的快速回归（不需要完整产品内核树即可跑单元测试）：

```bash
python3 -m py_compile scripts/c2saferust/*.py
python3 -m unittest scripts.c2saferust.tests.test_tool_cli
```

当前测试覆盖独立仓库模式下的：

- planning artifact 生成
- `--kernel-tree` / `--artifact-root` 行为
- safety / gate / oracle feedback 流程
- `net-link-type` 与 `net-phy` family 的 profile 驱动能力

## References

- [docs/new-module-checklist.md](docs/new-module-checklist.md)
- [docs/standalone-tool-repo.md](docs/standalone-tool-repo.md)
- [docs/tooling-abstraction-retrospective.md](docs/tooling-abstraction-retrospective.md)
- [docs/reviewer-10min.md](docs/reviewer-10min.md)
- [CONTRIBUTING.md](CONTRIBUTING.md)
