# Contributing

Thanks for looking at C2SafeRust Kernel Tooling.

## What this project is

Local-first tooling that generates **planning / safety / oracle artifacts** for Rust-for-Linux driver migration. It does **not** ship a Linux kernel tree. You point it at an external `linux` / `rust-next` worktree.

## Hard safety position

- Driver side should be able to use `#![forbid(unsafe_code)]`
- Driver side must not reach for `bindings::*` directly
- Soundness obligations must be explicit structured artifacts
- Oracle/smoke results should feed back into artifacts — not silent human bypass

If a module cannot meet these, the conclusion is **abstraction is incomplete**, not "add unsafe to the driver".

## Dev loop

```bash
python3 -m py_compile scripts/c2saferust/*.py
python3 -m unittest scripts.c2saferust.tests.test_tool_cli
```

## Pull requests

- Keep PRs focused (one family profile, one CLI fix, or docs)
- Include how to reproduce (`--kernel-tree`, module path, commands)
- Do not add proprietary kernel sources to this repo

## Reviewer pack

See [docs/reviewer-10min.md](docs/reviewer-10min.md) and [examples/sample-agent-gate-report.json](examples/sample-agent-gate-report.json).
