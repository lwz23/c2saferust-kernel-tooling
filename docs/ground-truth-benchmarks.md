# Ground-Truth Benchmarks

本文档描述 `C2SafeRust` 当前用于 ground-truth 对比实验的 benchmark 叠加层。
目标不是替代现有 intake/safety/oracle 流程，而是在其之上增加一层可审计、可复现、
可汇总的实验约束。

## 设计原则

- blind-first：生成阶段禁止直接查看官方 Rust ground-truth 实现。
- freeze-before-compare：先冻结候选实现和 planning artifacts，再做官方对比。
- provenance-required：所有 reference exposure 都必须进入 `provenance-log.jsonl`。
- claim-ceiling：每个 benchmark 只能对已经被证据覆盖的结论做 claim。
- portfolio-ready：单样本结果必须能自动汇总到 JSON/CSV 组合产物中。

## 当前梯队

### Phase 1 Formal

- `nlmon-ground-truth`
  - family: `net-link-type`
  - 目标 runtime state: `differential_validated`
  - 用途：验证 blind-first 工作流在已有 in-tree Rust 对照上的完整闭环。
- `ax88796b-ground-truth-strict-v2`
  - family: `net-phy`
  - 目标 runtime state: `lifecycle_validated`
  - 用途：验证 `phy_driver` family 在 blind-safe v2 约束下的 artifact/planner/oracle 接口是否成立。
  - fallback：若暂时缺少可信的 PHY 数据面 harness，则最低必须完成
    `KUnit + synthetic MDIO` 或 `qemu-module-lifecycle` 的生命周期级验证。

### Historical Calibration Only

- `ax88796b-ground-truth-strict`
  - 保留原因：历史校准对照。
  - 限制：其 blind input 带有 helper 级提示，不得再作为正式 blind-first 输入。

### Calibration / Clean Restart

- `rnull-calibration`
  - 用于校准 benchmark 输出字段和 portfolio 统计口径。
- `cpufreq-dt-clean-restart`
  - 用于验证“clean restart / fresh blind run”场景下的工件一致性。

## 防作弊边界

- benchmark profile 的 `search_hygiene.forbidden_reference_paths` 明确列出禁止直接读取的官方 Rust 对照文件。
- `search_allowlist` 只开放当前 benchmark 必需的 C 侧、抽象层和 artifact 路径。
- `incidental_path_exposure_policy` 当前固定为 `path-only-no-body-no-diff`：
  - 可以暴露路径名。
  - 不可以暴露正文。
  - 不可以暴露 diff。
- `freeze_before_compare = true` 时，必须先产出：
  - `module-manifest.json`
  - `planner-contract.json`
  - `validator-ready-summary.json`
  然后才允许进入官方对比或 difference metrics 阶段。

## 工件集合

每个 benchmark 目录当前固定使用以下工件名：

- `module-manifest.json`
- `planner-contract.json`
- `validator-ready-summary.json`
- `difference-metrics.json`
- `differential-oracle-run-record.json`
- `lifecycle-oracle-run-record.json`
- `provenance-log.jsonl`
- `provenance_log.txt`

其中：

- `module-manifest.json`
  - 固化 benchmark 身份、claim ceiling、blind policy、runtime plan。
- `planner-contract.json`
  - 固化 acceptance target 和 reference policy。
- `validator-ready-summary.json`
  - 汇总当前静态/build/runtime 状态，并下调 claim ceiling。
- `difference-metrics.json`
  - 记录 candidate 与官方 Rust ground-truth 之间的定量差异。
  - 仅允许在 freeze 事件已经写入 provenance 之后生成。

## 定量指标口径

`difference-metrics.json` 当前包含以下核心维度：

- `api_fit.api_hit_rate`
  - 候选实现命中的 API markers / 官方参考命中的 API markers。
- `callback_fit.callback_coverage_rate`
  - 候选实现命中的 callback markers / 官方参考命中的 callback markers。
- `candidate.unsafe_sites` 与 `reference.unsafe_sites`
  - 用于比较候选实现是否引入了额外 `unsafe`。
- `candidate.bindings_direct_uses` 与 `reference.bindings_direct_uses`
  - 用于比较候选实现是否更依赖 raw bindings。
- `candidate.state_flags_count`
  - 用于观察候选实现是否引入了更臃肿的状态机。
- `candidate.teardown_edges_count`
  - 用于观察 teardown 设计是否比参考实现更松散或更复杂。
- `size.size_ratio`
  - 代码规模近似比值。

这组指标不是“语义等价证明”，而是 benchmark calibration 与论文比较分析用的结构化特征。

## CLI 工作流

### 1. Bootstrap benchmark

```bash
python3 scripts/c2saferust/tool_cli.py bootstrap-benchmark \
  --kernel-tree <linux-tree> \
  --benchmark-id nlmon-ground-truth \
  --output-dir Documentation/rust/c2saferust/benchmarks/nlmon-ground-truth
```

### 2. 刷新 validator summary

```bash
python3 scripts/c2saferust/tool_cli.py refresh-benchmark-summary \
  --kernel-tree <linux-tree> \
  --benchmark-id nlmon-ground-truth
```

### 3. 生成 official compare 的差异指标

```bash
python3 scripts/c2saferust/tool_cli.py build-difference-metrics \
  --kernel-tree <linux-tree> \
  --benchmark-id nlmon-ground-truth \
  --candidate drivers/net/nlmon_rust_candidate.rs \
  --reference drivers/net/nlmon_rust.rs \
  --output Documentation/rust/c2saferust/benchmarks/nlmon-ground-truth/difference-metrics.json
```

### 4. 汇总 portfolio

```bash
python3 scripts/c2saferust/tool_cli.py collect-benchmark-portfolio \
  --root Documentation/rust/c2saferust/benchmarks \
  --output Documentation/rust/c2saferust/benchmarks/portfolio.json \
  --csv-output Documentation/rust/c2saferust/benchmarks/portfolio.csv
```

## 当前限制

- `ax88796b` 当前的目标 state 仍是 `lifecycle_validated`，不是 `differential_validated`。
- benchmark 层当前只负责结构化实验约束，不替代人工审计。
- `difference-metrics.json` 当前是语法/结构级比较，不是形式化行为证明。
- `provenance-log.jsonl` 目前只定义了产物位置和要求，尚未把所有 agent-side exposure 自动接入。
