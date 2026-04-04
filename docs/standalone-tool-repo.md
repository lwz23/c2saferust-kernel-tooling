# Standalone Tool Repo Assessment

## 结论

可以拆出单独仓库，但仓库定位必须明确为：

- 面向 Linux kernel / Rust-for-Linux 的 `C2SafeRust` tooling repo

而不是：

- 任意 C 项目通用的 C2Rust 翻译器

## 为什么可以拆

当前工具和目标内核树之间，已经天然分成两类内容：

工具仓库应保留：

- `scripts/c2saferust/*.py`
- `scripts/c2saferust/profiles/`
- `scripts/c2saferust/oracle_runners.py`
- `scripts/c2saferust/safety_verifier.rs`
- 流程文档、checklist、测试 fixture

目标内核树应保留：

- Rust abstraction 代码
- Rust driver 代码
- Kbuild / bindings / helpers patch
- `Documentation/rust/c2saferust/<module>/` artifact
- `task_context.md`、实验记录、模块级 smoke 结果

这两者分工是稳定的，所以适合拆分。

## 为什么它不是通用工具

当前仍然存在强领域耦合，但这些耦合是合理且应保留的：

- profile 明确依赖 Linux 目录布局：
  - `drivers/...`
  - `rust/kernel/...`
  - `rust/bindings/bindings_helper.h`
  - `rust/helpers/helpers.c`
- build / gate / oracle 明确依赖 kernel 构建方式：
  - `make O=...`
  - `bzImage`
  - `.ko`
  - QEMU boot
- soundness 规则依赖 Rust-for-Linux 抽象边界：
  - abstraction-only `unsafe`
  - driver-side `#![forbid(unsafe_code)]`

因此，拆库后的正确方向不是“去 Linux 化”，而是：

- 让工具仓库与目标 kernel tree 解耦
- 但保留其 Linux/RFL 专用性质

## 当前已实现的分离准备

当前工具接口已经支持：

- `--kernel-tree`
  - 用显式目标 Linux 树替代“脚本所在 repo 就是内核树”的假设
- `--artifact-root`
  - 允许 planning/safety/oracle artifact 改写到自定义根目录
- 保留 `--repo-root`
  - 作为兼容别名

因此后续拆库时，调用方式可以保持稳定：

```bash
python3 <tool-repo>/scripts/c2saferust/tool_cli.py refresh-artifacts \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c>
```

## 当前验证状态

截至 2026-04-04，这个“独立工具仓库 + 外部 Linux worktree”分离模式已经完成两轮
真实回放：

- `et1011c`
  - fresh external tree 完成 artifact refresh
  - `verify-safety` 通过
  - `gate-agent-candidate` 通过
  - `run-oracle` 通过
- `nlmon`
  - fresh external tree 完成 Kbuild / bindings / helpers / abstraction / driver replay
  - `verify-safety` 通过
  - `gate-agent-candidate` 通过
  - `run-oracle` 通过
  - QEMU `ip link add/up/show/down/del nlmon0` smoke 闭环通过

这意味着当前仓库已经不只是“理论上可拆”，而是已经被外部树回放实证过。

## 推荐拆库形态

推荐采用：

- 一个独立工具主仓库
- 多个外部 Linux worktree 作为实验目标树

不推荐当前就采用：

- `core + linux-adapter` 双仓库
- 工具仓库内嵌固定 kernel template

原因：

- 当前 family 抽象还不够多，不足以支撑再拆一层抽象框架
- 维护成本会显著上升
- 现阶段最重要的是“稳定复用同一套方法论”，不是做平台泛化

## 建议的后续拆库顺序

1. 保持当前 Linux 树中的工具继续可用。
2. 先把文档、CLI 接口、artifact root 约定稳定下来。
3. 再把 `scripts/c2saferust/` 和相关文档复制到独立仓库。
4. 用现有已闭环样本做回归：
   - `nlmon`
   - `et1011c`
   - `qsemi`
5. 再用 `vsockmon` 验证 planning-only 模式没有退化。

## 验收标准

当下面条件满足时，就可以认为“拆成独立工具仓库”是成功的：

- 工具仓库自身不需要携带 Linux 内核代码
- 指向新的 `linux-next` worktree 即可开始新样本实验
- artifact 默认仍回写目标 kernel tree
- `nlmon` / `et1011c` / `qsemi` 的既有闭环结果不退化
- 新模块接入时不再需要复制脚本，只需要：
  - 新 kernel tree
  - 新 module profile
  - 新 `task_context.md`
