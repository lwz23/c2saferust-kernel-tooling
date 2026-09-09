# Security Policy

## Supported versions

Security fixes are applied on the `main` branch of this repository. There are no long-lived release branches yet; please report issues against current `main`.

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security-sensitive reports.

Preferred channels (either is fine):

1. **GitHub Security Advisories** — open a private report via  
   https://github.com/lwz23/c2saferust-kernel-tooling/security/advisories/new
2. **Email** — cheerotter13@gmail.com with subject prefix `[SECURITY]`

Include as much detail as you can: affected commit/tag, reproduction steps, impact, and any suggested fix. We aim to acknowledge reports within a few business days.

## Scope notes

This project is **local-first tooling** that reads an external kernel tree and emits planning/safety/oracle artifacts. It does not ship kernel sources and is not a network-facing SaaS. Reports about the tooling itself (unsafe bypasses, incorrect safety gates, supply-chain issues in published artifacts) are in scope. Kernel bugs found while using the tool should be reported to the appropriate upstream (e.g. kernel.org / subsystem maintainers), not here.

## Disclosure

We prefer coordinated disclosure. After a fix lands on `main`, we may publish a brief advisory summary. Please allow reasonable time before public discussion of unfixed issues.
