# Next Target Evaluation

## Why This Exists

`nlmon` 和后续 `net-phy` 样本已经证明：

- 当前工具能约束 in-tree C 模块的 planning / safety / gate / oracle 闭环
- 它已经不再只是单模块脚本

但下一轮如果继续找新目标，约束条件已经变了：

- 2026-04-03 Andrew Lunn 在 `nlmon` RFC 线程中明确表示，`netdev` 更希望看到
  “新的硬件/新的真实用户”，而不是对现有 C 驱动再做一个 Rust 版本
- 当前本地环境没有真实硬件
- 当前也不适合把“依赖外部测试者/外部硬件验证”当成主验证路径
- 如果不继续沿 Andrew 的 `netdev/PHY` 指导线走，下一轮目标最好不要自然把 Andrew
  再带进收件人集合

因此，下一目标筛选必须同时满足“工具目标”和“子系统语境”两方面约束。

## Hard Filters

下一目标至少应满足：

- 不是继续围绕 `drivers/net/*` / `drivers/net/phy/*` 选题，除非明确决定继续跟进
  Andrew 的硬件驱动路线
- 最好有公开可获取的 C 源码，且源码不是只存在于本地私有树
- 尽量复用 `rust-next` 当前已经存在的 Rust abstraction：
  - `rust/kernel/platform.rs`
  - `rust/kernel/pci.rs`
  - `rust/kernel/miscdevice.rs`
- 最好能在当前机器上做本地 oracle，而不是把正确性完全押给外部硬件
- 收件人路径不应自然重新落到 Andrew / `netdev`

## Environment Reality

当前机器的关键运行条件是：

- 存在标准 QEMU：
  - `/usr/bin/qemu-system-x86_64`
  - `/usr/bin/qemu-system-aarch64`
- 当前 `qemu-system-x86_64 -device help` 能确认存在标准设备：
  - `pci-testdev`
  - `edu`
  - `ivshmem-plain`
  - `ivshmem-doorbell`
- 但当前机器上不存在 Android emulator 相关二进制：
  - `emulator`
  - `aemu`
  - `adb`

这意味着：

- 标准 QEMU 设备可以作为“本地可跑 oracle”的现实基础
- Android emulator / Goldfish 设备虽然是很好的真实候选，但当前不能假设它们已经可跑

## Candidate Summary

| Candidate | Subsystem | Public C Source | Local Oracle Feasibility | Rust Abstraction Fit | Maintainer Path Risk | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `JLSemi JL2101/JL2xxx` | `net/phy` | 有 | 差，需要真实硬件 | 中 | 高，会继续落入 Andrew/netdev 语境 | 暂不选 |
| `virt_wifi` / `vwifi` | wireless | 有 | 一般 | 低，缺大量无线抽象 | 中 | 暂不选 |
| `goldfish_sync` | platform + miscdevice | 有 | 差，依赖 Android emulator 侧设备 | 中，但需要 `dma_fence` / `sync_file` / IRQ 工作流 | 低 | 暂不选 |
| `goldfish_address_space` | PCI + miscdevice | 有 | 差，依赖 Android emulator 侧设备 | 高，适合 `pci` + `miscdevice` | 低 | 最像样的“真实缺失目标” |
| 标准 QEMU `edu` / `pci-testdev` 类目标 | PCI | 设备/文档公开，但缺强 upstream 叙事 | 高 | 高，直接贴近现有 Rust PCI 抽象 | 低 | 适合作为 runner/family 硬化样本，不适合作为首个 upstream 目标 |

## Detailed Notes

### Rejected for now: `JLSemi JL2101/JL2xxx`

这条线的问题不是“完全不可做”，而是它同时踩中了两个当前不该踩的约束：

- 它仍在 `net/phy` 语境里，下一轮 RFC 仍会自然落到 Andrew / `netdev`
- 当前没有硬件，不符合 Andrew 当前给出的“新硬件、增量抽象、逐步提交”建议

另外，公开源码规模也比最初想象的大得多：

- `jlsemi.c` 约 577 行
- `jlsemi-core.c` 约 2667 行

它不再是“拿现有 `qsemi` 抽象顺手翻一下”的级别。

### Rejected for now: `goldfish_sync`

`goldfish_sync` 的优点是：

- 有真实用户场景，服务 Android emulator 图形同步
- 是缺失于主线之外的真实虚拟设备驱动
- maintainer 路径不自然落到 Andrew

但它当前不适合作为下一步的原因也很明确：

- `goldfish_sync.c` 约 840 行
- 它深度依赖：
  - `dma_fence`
  - `sync_file`
  - IRQ + workqueue 协同
  - `miscdevice`
  - platform 资源 / 中断获取

这对当前工具来说跨得太大，不适合作为从 `net-*` 跨到新子系统的第一块样本。

### Best real missing target: `goldfish_address_space`

目前筛下来，`goldfish_address_space` 是最值得认真保留的真实候选：

- 它是 Android emulator 的真实虚拟设备，不是凭空发明的 toy
- 公开可取的 C 源存在，且在 Android 公共仓中持续构建
- 它不在 `netdev` 维护语境里
- 它与当前 Rust abstraction 的贴合度比 `goldfish_sync` 更高：
  - `pci`
  - `miscdevice`
  - `mmap`
  - `ioctl`

但它当前仍有两个硬 blocker：

- 当前机器没有 Android emulator 工具链，无法直接建立可靠本地 oracle
- 当前 `C2SafeRust` 工具还没有“外部 C 源输入、内核树内 Rust 落点”这一工作模式

源码复杂度也不是极小样本：

- `goldfish_address_space.c` 约 1014 行
- 主要表面包括：
  - PCI probe/remove
  - BAR 映射
  - `miscdevice`
  - `open/release`
  - `mmap`
  - `ioctl`

这说明它可以成为下一阶段的真实目标，但不应该在当前工具还没补齐外部 source intake
之前直接开写。

### Best immediate tool-hardening target: standard-QEMU PCI sample track

如果当前目标是“先把工具对新 family 的支持做扎实，并确保本地 oracle 可以跑”，
那么更务实的方向是先做一轮标准 QEMU PCI family 硬化：

- 当前环境已经有标准 QEMU
- 当前环境能直接看到 `pci-testdev` / `edu`
- `rust-next` 已经有现成 PCI Rust sample：
  - `samples/rust/rust_driver_pci.rs`
  - `samples/rust/rust_driver_auxiliary.rs`
  - `samples/rust/rust_dma.rs`

它的问题也很明确：

- upstream 叙事弱
- 更适合作为“runner/family/oracle 验证样本”
- 不适合作为下一封正式 RFC 的主角

所以它更像是为 `goldfish_address_space` 铺路，而不是替代
`goldfish_address_space`。

## Tooling Gap Exposed By This Search

这轮筛选暴露了一个此前在 `nlmon` / `et1011c` / `qsemi` 路径里没有暴露出的关键工具缺口：

- 当前工具默认 `module_path` 指向目标 kernel tree 内的 C 文件
- CLI 只有 `--kernel-tree` / `--repo-root`
- 没有 first-class `--source-root` / `--external-source`

这对 in-tree C 模块是够用的，但对下一类更真实的目标不够：

- 外部仓中的 C 驱动源码
- 主线内准备 landing 的 Rust abstraction / Rust driver

因此，在真正开始 `goldfish_address_space` 之前，工具必须先补这层能力。

## Recommendation

当前最合理的结论不是“立刻开始另一个驱动重写”，而是：

1. 不要从这棵树继续发一个新的 Andrew/netdev 方向 RFC。
2. 把 `goldfish_address_space` 定为下一阶段最值得保留的真实缺失目标。
3. 先补工具缺口，再决定是否正式切新 worktree 开写。

## Concrete Plan

### Phase 1: Tool First

- 给 CLI 和 intake 增加“外部 C 源输入”的一等支持
- 允许区分：
  - `source tree`
  - `target kernel tree`
  - `artifact root`
- 在 profile 中显式表达：
  - 外部 C 源路径
  - 目标 Rust 落点路径
  - Kbuild / bindings / helpers 修改目标

### Phase 2: New Family Skeleton

- 新增 `pci-virtual` 或等价 family 骨架
- 先复用已有 Rust PCI abstraction
- 把 runner 从当前 `net/link-type` 场景进一步剥离到真正独立的 PCI runner

### Phase 3: Runner Hardening

- 用标准 QEMU 可直接启动的 PCI 设备做第一轮 runner 验证
- 这一阶段的目标是证明：
  - family 切换可行
  - runner 不再只服务 `ip link`
  - safety/gate/oracle 工作流能迁移到 PCI 型目标

### Phase 4: Real Target Bring-up

- 在新的干净 `rust-next` worktree 中引入 `goldfish_address_space`
  的外部 C 源样本
- 基于新的 external-source intake 生成 planning artifact
- 再决定：
  - 当前是否已经具备足够好的本地 oracle
  - 是否需要先停留在 planning / abstraction 阶段
  - 是否值得切 `upstream-clean` 分支

## Bottom Line

如果只问“下一目标选谁”：

- 真正有 upstream 讨论价值、又不会把 Andrew 自然拉回收件人的最佳候选：
  `goldfish_address_space`

如果问“下一步立刻该实现什么”：

- 不是直接写新驱动
- 而是先让工具支持“外部 C 源 + 内核树 landing”的工作模式，并补出 PCI family /
  runner 的通路
