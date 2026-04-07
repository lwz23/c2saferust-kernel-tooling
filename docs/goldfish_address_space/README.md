# goldfish_address_space

这个目录记录 `goldfish_address_space` 作为当前 external-source 主目标的
artifact、runtime 证据、以及围绕 soundness / command semantics 的工具化约束。

## 当前已成立的结论

- `verify-safety` 已通过：
  - `safety-verdict.json` 为零 finding
  - `safety-violation-ledger.json` 为零 finding
- 已新增并落地 `command-semantics.json`：
  - 5 条关键命令 helper 被显式分类为
    `status-gated` / `readback-gated` / `issue-only`
  - `generate_handle` / `tell_ping_info_addr` 被固定为 `readback-gated`
  - `destroy_handle` 被固定为 release-path `issue-only`
- `verify-command-semantics` 已在真实 Rust candidate 上通过：
  - `command-semantics-verdict.json.pass = true`
  - `verified_commands = 5`
  - `total_violations = 0`
- minimal guest differential 已从旧的 9 个字段扩展到 16 个字段：
  - `open_rc`
  - `ping_device_type_rc`
  - `allocate_block_rc`
  - `ping_allocate_rc`
  - `mmap_rc`
  - `invalid_mmap_rc`
  - `claim_shared_rc`
  - `unclaim_shared_rc`
  - `second_unclaim_shared_rc`
  - `ping_unallocate_rc`
  - `deallocate_block_rc`
  - `deallocate_missing_rc`
  - `unknown_ioctl_rc`
  - `close_rc`
  - `reopen_rc`
  - `reclose_rc`
- 当前 `required_operations` 也已扩展到：
  - `open`
  - `release`
  - `allocate_block`
  - `deallocate_block`
  - `ping`
  - `mmap`
  - `claim_shared`
  - `unclaim_shared`
  - `unknown_ioctl`
  - `reopen`
- 在同一 Android emulator goldfish 硬件模型、同一 minimal guest userspace tester、
  `C baseline tree` 对 `Rust candidate tree` 的真实差分下，当前结果是：
  - `differential-oracle-run-record.json.pass = true`
  - `evidence_tier = differential_pass`
  - `ready_for_equivalence_claim = true`
  - `mismatches = []`

## 本轮最重要的新增价值

- 这次 goldfish 线不再只是“单边 smoke 通过”。
- 工具已经能用 runtime differential 抓出“看起来更安全、实际上与 C 不等价”的问题：
  - 典型例子就是 open-path 上 `GEN_HANDLE` / `TELL_PING_INFO_ADDR`
  - Rust 版一度错误地把它们强化成 `STATUS` gate
  - external C 实际语义却是 readback gate
- 现在这个差异已经不只是靠人工总结，而是被正式沉淀成：
  - `command-semantics.json`
  - `verify-command-semantics`
  - oracle 前置 gate

## 当前还不能宣称的事情

- 这还不是“整个 goldfish 子系统已经完成 Rust 支持”。
- 这也不是“`goldfish_address_space` 所有语义都已被完整证明等价”。
- 当前可宣称的是：
  - 对当前 oracle 覆盖的 10 类 required operations 和 16 个 comparison fields，
    Rust candidate 与 C baseline 已达到真实 runtime differential 一致。
- `PING_WITH_DATA` 目前仍未纳入等价声明范围。
- `CLAIM_SHARED` / `UNCLAIM_SHARED` 当前只证明了 ioctl/bookkeeping 层面的行为一致，
  还没有把更深层 shared-memory host protocol 语义一起纳入。

## 复盘与流程图

- 完整复盘文档：
  - `retrospective.md`
- 论文风格流程图：
  - `goldfish_tool_workflow.svg`
- 流程图生成脚本：
  - `scripts/c2saferust/render_goldfish_retrospective_flow.py`

## 关键文件

- 工具侧静态 artifact：
  - `command-semantics.json`
  - `translation-plan.json`
  - `safety-policy.json`
  - `soundness-discharge.json`
- Rust candidate runtime 记录：
  - `/home/lwz/rfl-dev/worktrees/goldfish-address-space-upstream-v1/Documentation/rust/c2saferust/goldfish_address_space/command-semantics-verdict.json`
  - `/home/lwz/rfl-dev/worktrees/goldfish-address-space-upstream-v1/Documentation/rust/c2saferust/goldfish_address_space/oracle-candidate-run-record.json`
  - `/home/lwz/rfl-dev/worktrees/goldfish-address-space-upstream-v1/Documentation/rust/c2saferust/goldfish_address_space/oracle-baseline-run-record.json`
  - `/home/lwz/rfl-dev/worktrees/goldfish-address-space-upstream-v1/Documentation/rust/c2saferust/goldfish_address_space/differential-oracle-run-record.json`
