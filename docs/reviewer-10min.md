# 10-minute reviewer guide

Use this checklist when evaluating the public MVP (grants, collaborators, upstream).

## 1) Problem (1 min)

Agent-assisted or human C→Rust driver rewrites can silently expand unsound surfaces. This repo provides **local** gates and machine-readable artifacts so private kernel trees never need a SaaS scanner.

## 2) What ships here (2 min)

- CLI under `scripts/c2saferust/`
- Family profiles (`net-link-type`, `net-phy` samples)
- Docs for standalone-tool boundaries and module checklists

Not shipped: Linux kernel sources.

## 3) Safety claims to verify (3 min)

Read README **Safety Position**:

- `forbid(unsafe_code)` on driver side
- no direct `bindings::*`
- structured soundness obligations
- oracle feedback into artifacts

Skim [examples/sample-agent-gate-report.json](../examples/sample-agent-gate-report.json) for the shape of a gate report.

## 4) Run without a full product story (3 min)

Repo self-test (no kernel tree required for unit tests):

```bash
python3 -m py_compile scripts/c2saferust/*.py
python3 -m unittest scripts.c2saferust.tests.test_tool_cli
```

Full CLI against a real module still needs an external kernel tree — see README Quick Start.

## 5) Open questions for maintainers (1 min)

- Which driver families should be next profiles?
- What oracle/smoke hooks are acceptable upstream?
- How should artifacts live beside Documentation/rust/?
