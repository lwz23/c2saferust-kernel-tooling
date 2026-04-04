# C2SafeRust Tooling Abstraction Retrospective

## Scope

这份总结只统计“明确走过 `scripts/c2saferust` 工具流程”的样本，不统计更早的
blind-rust / 手工试验分支，也不把官方已有 Rust 驱动算成工具产物。

纳入统计的判断标准是：

- 有 module profile / artifact 目录
- 有 `translation-plan.json`
- 抽象实现状态由 `abstraction-plan.json` 给出
- 若已进入落地阶段，则有 `verify-safety` / `gate-agent-candidate` /
  `run-oracle` 的结果

## 当前结论

截至目前，工具链已经覆盖了两个 family、四个样本模块：

| Module | Family | 抽象状态 | 驱动状态 | 验证状态 |
| --- | --- | --- | --- | --- |
| `nlmon` | `net-link-type` | 已实现 | 已实现 | `safety/gate/oracle` 全通过 |
| `vsockmon` | `net-link-type` | 部分实现 | 未开始 | 仅静态 planning |
| `et1011c` | `net-phy` | 已实现 | 已实现 | `safety/gate/oracle` 全通过 |
| `qsemi` | `net-phy` | 已实现 | 已实现 | `safety/gate/oracle` 全通过 |

如果只看“已经把抽象真实实现进 Rust 内核层，并且被样本驱动/验证闭环使用”的部分，
目前是三个样本：

- `nlmon`
- `et1011c`
- `qsemi`

## 已实现的抽象面

### 1. `net-link-type` family

这部分最初由 `nlmon` 推出来，当前已经形成可复用的 link-type 抽象骨架：

- `rtnl`
  - 最小 `Registration<T: Driver>`
  - `ValidateContext`
  - `NlAttrTable`
- `netdevice`
  - `Device` 包装
  - `Operations` trait
  - `TxOutcome`
  - 私有区 `Private: Zeroable` 约束
- `skbuff`
  - move-only `SkBuff` owner
- `stats`
  - `dev_lstats_add` 安全包装
- `netlink_tap`
  - tap 注册 / 注销封装

这些抽象已经被 `nlmon` 使用并通过 smoke。

`vsockmon` 在这个 family 上复用了：

- `rtnl`
- `netdevice`
- `skbuff`
- `stats`

但它还缺一个尚未实现的模块特化抽象：

- `vsock_tap`

因此 `vsockmon` 目前只能算“复用了已有 family 抽象，并暴露了下一块缺失抽象”，
还不能算完整落地。

### 2. `net-phy` family

这部分是第二轮从 `rust-next` 干净树重新展开的工具化结果。

首先由 `et1011c` 确认了最小 PHY 抽象可行面：

- `phy`
  - safe driver registration / teardown
  - typed Clause 22/45 register access
- `phy_config_aneg`
  - safe `genphy_config_aneg` wrapper

然后由 `qsemi` 把 PHY family 扩展到“带中断/初始化回调”的第二阶段：

- `phy_config_init`
  - safe `Driver::config_init`
- `phy_irq_callbacks`
  - safe `Driver::config_intr`
  - safe `Driver::handle_interrupt`
  - typed `InterruptMode`
  - typed `InterruptAction`
  - `phy_error()` / `phy_trigger_machine()` 继续留在 abstraction 内部

因此，当前 `net-phy` family 已经形成两级能力：

- Stage 1: `phy` + `phy_config_aneg`
- Stage 2: `phy_config_init` + `phy_irq_callbacks`

## 按模块看，工具到底做成了什么

### `nlmon`

工作树：

- `/home/lwz/rfl-dev/worktrees/nlmon-tooling-clean-v1`

工具产物结论：

- 抽象面：`rtnl` / `netdevice` / `stats` / `skbuff` / `netlink_tap`
- 驱动：`drivers/net/nlmon_rust.rs`
- Safety: pass
- Gate: pass
- Oracle: pass

它的价值不是 `nlmon` 本身，而是把第一套 link-type workflow 跑通：

- static intake
- patch-plan
- abstraction-plan
- safety policy / soundness discharge
- agent gate
- QEMU `ip link` oracle

### `vsockmon`

工作树：

- `/home/lwz/rfl-dev/worktrees/nlmon-tooling-clean-v1`

工具产物结论：

- 不是“已落地模块”，而是“下一块缺失抽象的探针样本”
- 已证明现有 `net-link-type` family 抽象可以复用到大部分路径
- 剩余 blocker 是 `vsock_tap`

所以它当前的意义是：

- 证明 `nlmon` 的抽象并非纯粹 hardcode 到单模块
- 帮工具明确下一块需要模板化 / 新增的抽象面

### `et1011c`

工作树：

- `/home/lwz/rfl-dev/worktrees/phy-reference-clean-v1`
- `/home/lwz/rfl-dev/worktrees/phy-reference-clean-v2-qsemi`

工具产物结论：

- 抽象面：`phy` / `phy_config_aneg`
- 驱动：`drivers/net/phy/et1011c_rust.rs`
- Safety: pass
- Gate: pass
- Oracle: pass

它的作用是把工具从 `net-link-type` 推广到 `net-phy`，验证：

- profile 化 source model 可以切换 family
- oracle runner 可以改成 module lifecycle 风格
- 同样的 safety / gate 流程可以约束 PHY 驱动

### `qsemi`

工作树：

- `/home/lwz/rfl-dev/worktrees/phy-reference-clean-v2-qsemi`

工具产物结论：

- 抽象面：`phy` / `phy_config_aneg` / `phy_config_init` /
  `phy_irq_callbacks`
- 驱动：`drivers/net/phy/qsemi_rust.rs`
- Safety: pass
- Gate: pass
- Oracle: pass

它的价值在于：

- 不是重复 `et1011c`，而是逼出了 PHY family 的第二阶段抽象扩展
- 验证了“缺失抽象 -> 先更新 artifact -> 再扩 abstraction -> 再落驱动”
  这套流程可以继续工作

## 截至目前已经沉淀下来的“工具级抽象资产”

如果按“以后可以复用到新模块”的角度看，目前真正沉淀下来的不是某个单一驱动，
而是下面这些 abstraction asset：

### 已经被真实样本证明可复用

- `net-link-type` family 框架
- `net-phy` family 框架
- profile-driven source model dispatch
- patch-plan / abstraction-plan / translation-plan 生成
- safety policy / soundness discharge / verifier
- agent workflow gate
- QEMU oracle runner
- module lifecycle oracle runner

### 已被两个及以上样本证明不是单点特化

- `rtnl` / `netdevice` / `skbuff` / `stats`
  - `nlmon` 已用
  - `vsockmon` 规划复用
- `phy`
  - `et1011c` 已用
  - `qsemi` 已用
- `phy_config_aneg`
  - `et1011c` 已用
  - `qsemi` 继承可用

### 目前只被单个样本逼出来，但已经进入通用抽象层

- `netlink_tap`
  - 来源：`nlmon`
- `phy_config_init`
  - 来源：`qsemi`
- `phy_irq_callbacks`
  - 来源：`qsemi`

这些虽然目前只由一个样本驱动出来，但它们已经不再是 driver-local 逻辑，
而是上升到了通用 Rust abstraction 层，所以应当视为“已实现的工具资产”。

## 当前还不能宣称已经做到的事

下面这些事情目前还不能夸大：

- 不能说工具已经对任意 netdevice / 任意 PHY 自动迁移
- 不能说 link-type family 已经完备
  - `vsock_tap` 还没补
- 不能说 oracle 已覆盖真实硬件等价
  - `et1011c` / `qsemi` 当前还是 module lifecycle 级别
- 不能说所有抽象都已被多个独立模块交叉验证
  - 一部分抽象还只有单样本来源

## 最简结论

如果只回答“截至目前，这个工具已经实现了哪些模块抽象”：

- `nlmon` 推出了并验证了 `net-link-type` family 的第一套可用抽象：
  `rtnl`、`netdevice`、`skbuff`、`stats`、`netlink_tap`
- `et1011c` 推出了并验证了 `net-phy` family 的第一阶段抽象：
  `phy`、`phy_config_aneg`
- `qsemi` 在 `et1011c` 基础上扩展并验证了 `net-phy` family 的第二阶段抽象：
  `phy_config_init`、`phy_irq_callbacks`
- `vsockmon` 证明了 `net-link-type` family 已经有可复用部分，但也明确暴露出
  下一块尚未实现的抽象 `vsock_tap`

因此，目前工具已经不再只是“一个 `nlmon` 迁移脚本”，而是已经拥有：

- 一套已验证的 `net-link-type` 抽象族
- 一套已验证的 `net-phy` 抽象族
- 一个能用 artifact、safety gate、oracle 去约束后续样本的迁移工作流
