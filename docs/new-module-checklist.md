# New Module Reuse Checklist

## Goal

这份 checklist 用于在后续接入一个新模块时，复用现有 `C2SafeRust` 工具流程，
并避免重新退化为“人工先写驱动、再回填 artifact”的模式。

默认前提：

- 工具仓库负责脚本、profile、rule-pack、runner、流程文档
- 目标 `linux-next` worktree 负责内核代码、artifact、实验记录
- artifact 默认写回目标内核树的
  `Documentation/rust/c2saferust/<module>/`

## Phase 0: 准备目标树

- 从新的干净 `rust-next` 创建独立 worktree。
- 不复用旧模块的 Rust 实现历史作为开发基底。
- 在目标树里创建/更新 `plan.md` 与 `task_context.md`。
- 先确认当前模块属于哪一类：
  - 可复用现有 `net-link-type`
  - 可复用现有 `net-phy`
  - 需要定义新 family

## Phase 1: 建 profile，不写驱动

- 在工具侧新增或复用 `module profile`。
- 必须明确这些字段：
  - `module_path`
  - `family_id`
  - `driver_rust_path`
  - `kbuild`
  - `translation`
  - `oracle`
  - `rule_pack_ids`
- 如果现有 `scenario` 不够，先补 `scenario profile`，不要把 oracle 逻辑硬编码到单模块。

推荐先跑：

```bash
python3 scripts/c2saferust/tool_cli.py bootstrap-module \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c> \
  --output-dir <linux-tree>/Documentation/rust/c2saferust/<module>
```

或直接使用 profile 默认输出目录：

```bash
python3 scripts/c2saferust/tool_cli.py refresh-artifacts \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c>
```

## Phase 2: 先读 artifact，再决定能不能写代码

必须先检查这些 artifact：

- `kbuild-patch-plan.json`
- `bindings-patch-plan.json`
- `helpers-patch-plan.json`
- `abstraction-plan.json`
- `unsafe-obligations.json`
- `translation-plan.json`
- `safety-policy.json`
- `soundness-discharge.json`
- `agent-workflow-plan.json`

判定规则：

- 如果 `patch-plan.status == pending`
  - 先做阶段 1/2 patch
  - 不进入驱动开发
- 如果 `abstraction-plan` 里有 `status = missing`
  - 先补 abstraction
  - 不进入驱动开发
- 只有当 `translation-plan.readiness.ready_for_minimal_driver_codegen = true`
  - 才允许进入驱动生成

## Phase 3: 落地 Kbuild / bindings / helpers

- 严格按 patch-plan 修改目标树。
- 不做额外“顺手整理”。
- 修改后立刻重新跑：

```bash
python3 scripts/c2saferust/tool_cli.py refresh-artifacts \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c>
```

通过标准：

- `kbuild-patch-plan.json` 变成 `already_applied`
- `bindings-patch-plan.json` 变成 `already_applied`
- `helpers-patch-plan.json` 变成 `already_applied`

## Phase 4: 先扩 abstraction，再写驱动

- 只在 artifact 明确允许的 abstraction 文件中扩展 Rust abstraction。
- 不允许在驱动文件里偷穿：
  - `bindings::*`
  - raw pointer
  - `unsafe`
  - 未在 artifact 中声明的新 callback

每次 abstraction 改完后必须重新生成 artifact，确认：

- `abstraction-plan.json` 里的对应 area 从 `missing` 变成 `implemented`
- `unsafe-obligations.json` / `soundness-discharge.json` 已同步更新

## Phase 5: 生成驱动，但受硬约束限制

驱动文件的硬约束：

- 顶部必须有 `#![forbid(unsafe_code)]`
- 不直接调用 `bindings::*`
- 不持有 raw pointer
- 不新增 artifact 之外的 callback / FFI surface

如果做不到，结论应是“abstraction 还不够”，而不是“先在驱动里临时绕过去”。

## Phase 6: 先过 safety / gate，再进 oracle

先运行：

```bash
python3 scripts/c2saferust/tool_cli.py verify-safety \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c> \
  --output <verdict.json>
```

再运行：

```bash
python3 scripts/c2saferust/tool_cli.py gate-agent-candidate \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c> \
  --output <gate-report.json>
```

通过标准：

- `safety-verdict.json.pass = true`
- `agent-gate-report.json.ready_for_smoke = true`

若不通过：

- 只修 soundness / abstraction / driver 违规项
- 不直接跳去跑 smoke

## Phase 7: 跑 oracle，并把失败反馈回 artifact

运行：

```bash
python3 scripts/c2saferust/tool_cli.py run-oracle \
  --kernel-tree <linux-tree> \
  --module-path <drivers/.../foo.c> \
  --output <toolchain-run-record.json>
```

如果 oracle 失败：

- 先运行 `apply-oracle-feedback`
- 或者先人工更新 artifact 再继续
- 不允许绕过 artifact 直接“就地修驱动”

目标是形成闭环：

- `toolchain-run-record.json`
- `qemu-smoke.log` 或 runner log
- `oracle-feedback.json`

## Phase 8: 记录与分支收尾

- 每完成一个阶段，都更新目标树里的 `task_context.md`。
- 如果计划与实际出现偏差，先更新 `task_context.md`，再继续开发。
- 当模块达到 `safety/gate/oracle` 闭环后，再决定是否切 `upstream-clean` 分支。

## 最小验收清单

一个模块只有满足下面条件，才算“通过工具流程”：

- 有完整 planning artifact
- `translation-plan.readiness.ready_for_minimal_driver_codegen = true`
- 驱动侧 `#![forbid(unsafe_code)]`
- `verify-safety` 通过
- `gate-agent-candidate` 通过
- `run-oracle` 通过
- `task_context.md` 记录了阶段结果与 blocker 演进

如果只完成静态 artifact，而没有 driver/gate/oracle，则应标记为：

- `planning-only`
- 或 `abstraction-probe sample`

而不是“已完成 Rust 化”
