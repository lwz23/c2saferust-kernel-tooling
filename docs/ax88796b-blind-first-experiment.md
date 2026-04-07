# ax88796b Blind-First 实验协议

本文档是 `ax88796b` strict blind-first 单案例实验的唯一执行规范。

> 注：`ax88796b_blind_strict` / `ax88796b-ground-truth-strict` 已保留为历史校准资产，
> 因为它们在 blind input 中包含 helper 级答案提示。正式 blind run 必须使用本文档
> 对应的 v2 profiles。

目标不是证明工具已经可以自动迁移任意 PHY 驱动，而是用“已有抽象已齐备”的
`ax88796b` 案例，把当前工具弱点拆成以下六类：

- blindness gap
- planner/profile gap
- codegen gap
- oracle gap
- benchmark gap
- operator-effort gap

## 1. 适用 profile

- module profile：`ax88796b_blind_strict_v2`
- benchmark profile：`ax88796b-ground-truth-strict-v2`

这两个 shadow profile 只服务本实验，不替换 canonical `ax88796b` 默认 profile。

## 2. blind tree 选择规则

blind tree 必须同时满足：

- 有 `rust/kernel/net/phy.rs`
- 有 `rust/kernel/net/phy/reg.rs`
- 没有 `drivers/net/phy/ax88796b_rust.rs`
- 有 `drivers/net/phy/ax88796b.c`

若当前本地可用历史中找不到满足以上四个条件的提交，则启用 fallback blind tree：

- 基于“已有完整 PHY abstraction”的提交创建 fresh blind worktree 和独立 reference worktree。
- blind worktree 初始化顺序固定为：创建 worktree、立即物理删除 `drivers/net/phy/ax88796b_rust.rs`、写入 seal record，然后才允许任何搜索、阅读或 artifact 生成。
- pre-freeze 期间该路径必须持续物理不存在；只允许 reference worktree 保留原始正文，供 freeze 后 compare 使用。
- fallback 原因、所选提交和隔离措施必须写入 provenance 与 experiment report。

禁止直接复用以下目录作为本轮执行基底：

- `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/ax88796b-blind-rust`
- `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/ax88796b-blind-validation`

它们只允许在主对比结束后作为历史校准证据使用。

## 3. allowlist / forbidden set

strict benchmark 的 allowlist 固定为：

- `drivers/net/phy/ax88796b.c`
- `drivers/net/phy/Kconfig`
- `drivers/net/phy/Makefile`
- `include/linux/phy.h`
- `include/linux/mii.h`
- `rust/kernel/net.rs`
- `rust/kernel/net/phy.rs`
- `rust/kernel/net/phy/reg.rs`
- `Documentation/rust/c2saferust/ax88796b_blind_strict_v2`

strict benchmark 的 forbidden set 固定为：

- `drivers/net/phy/ax88796b_rust.rs`
- `/home/lwz/rfl-dev/archive/legacy-agent-benchmarks/ax88796b-blind-*`
- 历史 blind benchmark 的评估正文、difference ledger、runtime diff 和 expert report
- 任何 `git show` / diff 形式的 `drivers/net/phy/ax88796b_rust.rs`

允许偶然暴露路径名，不允许读取正文或 diff。

## 4. contamination 规则

以下任一情况都将本轮 run 标记为 `contaminated`：

- 读取 `drivers/net/phy/ax88796b_rust.rs` 正文
- 读取任何 archive blind 资产正文
- 查看主线 `ax88796b` Rust 实现的 diff
- 在 pre-freeze 阶段恢复 `drivers/net/phy/ax88796b_rust.rs` 到 blind worktree
- 在 freeze 之前使用参考实现正文纠正 candidate

`contaminated` run 只保留污染原因，不进入正式比较。后续必须换 fresh blind tree 重开。

## 5. provenance 记录协议

从 `bootstrap-benchmark` 开始记录 `provenance-log.jsonl` 与镜像 `provenance_log.txt`。

若当前历史中不存在天然满足条件的 pre-intro blind tree，优先使用工具命令初始化
fresh blind/reference worktree，并在 blind tree 中物理删除目标 Rust 驱动：

```bash
python3 scripts/c2saferust/tool_cli.py init-blind-worktree-pair \
  --kernel-tree <kernel-repo> \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --baseline-rev b2e47002b2350f57bfa8fe1c231e9fbb6baef78b \
  --blind-tree <blind-tree> \
  --reference-tree <reference-tree>
```

若使用 fallback blind tree，应在 `bootstrap-benchmark` 之后立即追加一条 protocol 事件，
说明“本地历史中不存在满足 strict pre-intro 条件的提交，因此改用 fresh fallback worktree”。

工具会自动写入 bootstrap event；其余关键操作使用：

```bash
python3 scripts/c2saferust/tool_cli.py record-benchmark-provenance \
  --kernel-tree <blind-tree> \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --kind command \
  --command "python3 scripts/c2saferust/tool_cli.py refresh-artifacts --kernel-tree <blind-tree> --profile-id ax88796b_blind_strict_v2" \
  --path drivers/net/phy/ax88796b.c \
  --path rust/kernel/net/phy.rs \
  --note "strict blind planning refresh"
```

冻结点、污染事件、compare 解锁也通过同一个命令记录，例如：

```bash
python3 scripts/c2saferust/tool_cli.py record-benchmark-provenance \
  --kernel-tree <blind-tree> \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --kind freeze \
  --note "candidate, planning artifacts, safety/gate/oracle results frozen before compare"
```

```bash
python3 scripts/c2saferust/tool_cli.py record-benchmark-provenance \
  --kernel-tree <blind-tree> \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --kind contamination \
  --contamination-status contaminated \
  --note "reference driver body was exposed before freeze"
```

## 6. 正式执行顺序

### Phase 0: 生成 strict artifact

```bash
python3 scripts/c2saferust/tool_cli.py refresh-artifacts \
  --kernel-tree <blind-tree> \
  --profile-id ax88796b_blind_strict_v2
```

```bash
python3 scripts/c2saferust/tool_cli.py bootstrap-benchmark \
  --kernel-tree <blind-tree> \
  --benchmark-id ax88796b-ground-truth-strict-v2
```

若 `translation-plan.json` 没有得到
`ready_for_minimal_driver_codegen = true`，应直接记为 planner/profile gap。

### Phase 1: 严格按 artifact 落 candidate

- 仅允许读取 allowlist 内容和 strict artifact。
- pre-freeze artifact 不允许出现 helper 级 Rust recipe；agent 必须自行从 C 源和
  `rust/kernel/net/phy*.rs` 推导 callback-to-abstraction 映射。
- 驱动必须保持 `#![forbid(unsafe_code)]`。
- 禁止 `bindings::*`、raw pointer、新 callback、reference-driver 正文借用。

### Phase 2: 先过 safety / gate / oracle

在 compile/package 前，先运行 Rust/Kbuild 环境预检：

```bash
make O=<build-dir> LLVM=<llvm-arg> rustavailable
```

失败时直接记为 `environment gap`，不要把问题归因给 candidate。

`refresh-artifacts` 会同时生成 `module-lifecycle.config`，后续 build dir 配置顺序固定为：

1. `make O=<build-dir> defconfig`
2. `make O=<build-dir> LLVM=<llvm-arg> rustavailable`
3. `scripts/kconfig/merge_config.sh -m -O <build-dir> <build-dir>/.config <artifact-dir>/module-lifecycle.config`
4. `make O=<build-dir> LLVM=<llvm-arg> olddefconfig`
5. `make O=<build-dir> LLVM=<llvm-arg> drivers/net/phy/ax88796b_rust.o`
6. `make O=<build-dir> LLVM=<llvm-arg> drivers/net/phy/ax88796b_rust.ko`

```bash
python3 scripts/c2saferust/tool_cli.py verify-safety \
  --kernel-tree <blind-tree> \
  --profile-id ax88796b_blind_strict_v2 \
  --output <artifact-dir>/safety-verdict.json
```

```bash
python3 scripts/c2saferust/tool_cli.py gate-agent-candidate \
  --kernel-tree <blind-tree> \
  --profile-id ax88796b_blind_strict_v2 \
  --output <artifact-dir>/agent-gate-report.json
```

```bash
python3 scripts/c2saferust/tool_cli.py run-oracle \
  --kernel-tree <blind-tree> \
  --profile-id ax88796b_blind_strict_v2 \
  --output <artifact-dir>/toolchain-run-record.json
```

本轮 runtime 成功门槛是 fallback `qemu-module-lifecycle` 达到
`lifecycle_validated`。若 `kunit-synthetic-mdio` 未实现，应在实验报告中明确记为
benchmark gap，而不是 candidate failure。

正式 build 闭环要求是 `.ko` 打包成功并由同一个 build dir 中产出的模块通过 lifecycle oracle；
仅 `.o` 编译通过不算正式验收。

### Phase 3: freeze before compare

拿到以下结果后立即冻结：

- candidate 源文件
- planning artifacts
- `safety-verdict.json`
- `agent-gate-report.json`
- `toolchain-run-record.json`

冻结后记录 provenance，再允许解锁 reference compare。

### Phase 4: compare 与评估

冻结后才允许读取主线 `drivers/net/phy/ax88796b_rust.rs`，然后运行：

```bash
python3 scripts/c2saferust/tool_cli.py build-difference-metrics \
  --kernel-tree <blind-tree> \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --candidate drivers/net/phy/ax88796b_rust.rs \
  --reference <reference-tree>/drivers/net/phy/ax88796b_rust.rs \
  --output <benchmark-dir>/difference-metrics.json
```

```bash
python3 scripts/c2saferust/tool_cli.py build-benchmark-experiment-report \
  --kernel-tree <blind-tree> \
  --benchmark-id ax88796b-ground-truth-strict-v2 \
  --output <benchmark-dir>/experiment-report.json
```

报告里必须补完这四个 expert delta 字段：

- 哪里比专家实现更绕
- 哪里依赖 profile 暗示过重
- 哪里暴露工具边界不严
- 哪里属于 oracle/benchmark 不足而不是 driver 不足

## 7. 正式验收产物

本轮正式验收以这四份输出为准：

- 冻结后的 artifact bundle
- 完整 `provenance-log.jsonl`
- `difference-metrics.json` 加人工 expert delta
- `experiment-report.json` 中的 weakness matrix

若 runtime 因环境问题或 runner 缺失未达标，但 safety/gate 已过，可输出 postmortem
报告；该结果不计入正式 blind benchmark claim。
