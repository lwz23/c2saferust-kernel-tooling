#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import argparse
from pathlib import Path

import intake
import safety
import smoke
import workflow


def _add_kernel_tree_args(parser: argparse.ArgumentParser, *, include_artifact_root: bool = False) -> None:
    parser.add_argument("--kernel-tree")
    parser.add_argument("--repo-root")
    if include_artifact_root:
        parser.add_argument("--artifact-root")


def _kernel_tree_arg(args: argparse.Namespace) -> str | None:
    return getattr(args, "kernel_tree", None) or getattr(args, "repo_root", None)


def _artifact_root_arg(args: argparse.Namespace) -> str | None:
    return getattr(args, "artifact_root", None)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C2SafeRust tool bootstrap CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    intake_kbuild = subparsers.add_parser("intake-kbuild")
    intake_kbuild.add_argument("--module-path", default="drivers/net/nlmon.c")
    intake_kbuild.add_argument("--output", required=True)
    _add_kernel_tree_args(intake_kbuild, include_artifact_root=True)

    audit_bindings = subparsers.add_parser("audit-bindings")
    audit_bindings.add_argument("--module-path", default="drivers/net/nlmon.c")
    audit_bindings.add_argument("--binding-output", required=True)
    audit_bindings.add_argument("--helper-output", required=True)
    _add_kernel_tree_args(audit_bindings, include_artifact_root=True)

    plan_patches = subparsers.add_parser("plan-patches")
    plan_patches.add_argument("--module-path", default="drivers/net/nlmon.c")
    plan_patches.add_argument("--kbuild-output", required=True)
    plan_patches.add_argument("--bindings-output", required=True)
    plan_patches.add_argument("--helpers-output", required=True)
    _add_kernel_tree_args(plan_patches, include_artifact_root=True)

    plan_abstractions = subparsers.add_parser("plan-abstractions")
    plan_abstractions.add_argument("--module-path", default="drivers/net/nlmon.c")
    plan_abstractions.add_argument("--abstraction-output", required=True)
    plan_abstractions.add_argument("--unsafe-output", required=True)
    _add_kernel_tree_args(plan_abstractions, include_artifact_root=True)

    plan_translation = subparsers.add_parser("plan-translation")
    plan_translation.add_argument("--module-path", default="drivers/net/nlmon.c")
    plan_translation.add_argument("--translation-output", required=True)
    _add_kernel_tree_args(plan_translation, include_artifact_root=True)

    plan_safety = subparsers.add_parser("plan-safety-policy")
    plan_safety.add_argument("--module-path", default="drivers/net/nlmon.c")
    plan_safety.add_argument("--policy-output", required=True)
    plan_safety.add_argument("--discharge-output", required=True)
    _add_kernel_tree_args(plan_safety, include_artifact_root=True)

    verify_safety = subparsers.add_parser("verify-safety")
    verify_safety.add_argument("--module-path", default="drivers/net/nlmon.c")
    verify_safety.add_argument("--output", required=True)
    _add_kernel_tree_args(verify_safety, include_artifact_root=True)

    plan_agent_workflow = subparsers.add_parser("plan-agent-workflow")
    plan_agent_workflow.add_argument("--module-path", default="drivers/net/nlmon.c")
    plan_agent_workflow.add_argument("--output", required=True)
    _add_kernel_tree_args(plan_agent_workflow, include_artifact_root=True)

    gate_agent_candidate = subparsers.add_parser("gate-agent-candidate")
    gate_agent_candidate.add_argument("--module-path", default="drivers/net/nlmon.c")
    gate_agent_candidate.add_argument("--output", required=True)
    gate_agent_candidate.add_argument("--safety-output")
    _add_kernel_tree_args(gate_agent_candidate, include_artifact_root=True)
    gate_agent_candidate.add_argument("--build-dir")
    gate_agent_candidate.add_argument("--make-llvm")
    gate_agent_candidate.add_argument("--skip-compile", action="store_true")

    run_smoke_qemu = subparsers.add_parser("run-smoke-qemu")
    run_smoke_qemu.add_argument("--module-path", default="drivers/net/nlmon.c")
    run_smoke_qemu.add_argument("--output", required=True)
    run_smoke_qemu.add_argument("--qemu-log-output")
    _add_kernel_tree_args(run_smoke_qemu, include_artifact_root=True)
    run_smoke_qemu.add_argument("--build-dir")
    run_smoke_qemu.add_argument("--make-llvm")
    run_smoke_qemu.add_argument("--timeout-sec", type=int, default=120)

    run_oracle = subparsers.add_parser("run-oracle")
    run_oracle.add_argument("--module-path", required=True)
    run_oracle.add_argument("--output", required=True)
    run_oracle.add_argument("--qemu-log-output")
    _add_kernel_tree_args(run_oracle, include_artifact_root=True)
    run_oracle.add_argument("--build-dir")
    run_oracle.add_argument("--make-llvm")
    run_oracle.add_argument("--timeout-sec", type=int, default=120)

    bootstrap = subparsers.add_parser("bootstrap-nlmon")
    bootstrap.add_argument("--module-path", default="drivers/net/nlmon.c")
    bootstrap.add_argument("--output-dir", required=True)
    _add_kernel_tree_args(bootstrap, include_artifact_root=True)

    bootstrap_module = subparsers.add_parser("bootstrap-module")
    bootstrap_module.add_argument("--module-path", required=True)
    bootstrap_module.add_argument("--output-dir", required=True)
    _add_kernel_tree_args(bootstrap_module, include_artifact_root=True)

    refresh_artifacts = subparsers.add_parser("refresh-artifacts")
    refresh_artifacts.add_argument("--module-path", required=True)
    refresh_artifacts.add_argument("--output-dir")
    _add_kernel_tree_args(refresh_artifacts, include_artifact_root=True)

    apply_oracle_feedback = subparsers.add_parser("apply-oracle-feedback")
    apply_oracle_feedback.add_argument("--module-path", required=True)
    apply_oracle_feedback.add_argument("--run-record", required=True)
    apply_oracle_feedback.add_argument("--qemu-log")
    apply_oracle_feedback.add_argument("--output")
    _add_kernel_tree_args(apply_oracle_feedback, include_artifact_root=True)

    return parser.parse_args()


def run_intake_kbuild(args: argparse.Namespace) -> str:
    payload = intake.build_kbuild_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_audit_bindings(args: argparse.Namespace) -> str:
    binding_payload = intake.build_binding_gap_audit(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    helper_payload = intake.build_helper_audit(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.binding_output, binding_payload)
    intake.write_json(args.helper_output, helper_payload)
    return intake.stable_json(binding_payload)


def run_plan_patches(args: argparse.Namespace) -> str:
    kbuild_payload = intake.build_kbuild_patch_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    bindings_payload = intake.build_bindings_patch_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    helpers_payload = intake.build_helpers_patch_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.kbuild_output, kbuild_payload)
    intake.write_json(args.bindings_output, bindings_payload)
    intake.write_json(args.helpers_output, helpers_payload)
    return intake.stable_json(kbuild_payload)


def run_plan_abstractions(args: argparse.Namespace) -> str:
    abstraction_payload = intake.build_abstraction_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    unsafe_payload = intake.build_unsafe_obligations(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.abstraction_output, abstraction_payload)
    intake.write_json(args.unsafe_output, unsafe_payload)
    return intake.stable_json(abstraction_payload)


def run_plan_translation(args: argparse.Namespace) -> str:
    payload = intake.build_translation_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.translation_output, payload)
    return intake.stable_json(payload)


def run_plan_safety_policy(args: argparse.Namespace) -> str:
    policy_payload = intake.build_safety_policy(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    discharge_payload = intake.build_soundness_discharge(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.policy_output, policy_payload)
    intake.write_json(args.discharge_output, discharge_payload)
    return intake.stable_json(policy_payload)


def run_verify_safety(args: argparse.Namespace) -> str:
    payload = safety.verify_module_safety(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    safety.write_verdict(args.output, payload)
    return intake.stable_json(payload)


def run_plan_agent_workflow(args: argparse.Namespace) -> str:
    payload = intake.build_agent_workflow_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_gate_agent_candidate(args: argparse.Namespace) -> str:
    payload = workflow.gate_agent_candidate(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        output=args.output,
        safety_output=args.safety_output,
        build_dir=args.build_dir,
        make_llvm=args.make_llvm,
        skip_compile=args.skip_compile,
    )
    return intake.stable_json(payload)


def run_oracle(args: argparse.Namespace) -> str:
    payload = smoke.run_oracle(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        output=args.output,
        qemu_log_output=args.qemu_log_output,
        build_dir=args.build_dir,
        make_llvm=args.make_llvm,
        timeout_sec=args.timeout_sec,
    )
    return intake.stable_json(payload)


def run_smoke_qemu(args: argparse.Namespace) -> str:
    return run_oracle(args)


def run_bootstrap_module(args: argparse.Namespace) -> str:
    manifest = intake.write_planning_artifacts(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        output_dir=args.output_dir,
        artifact_root=_artifact_root_arg(args),
    )
    return intake.stable_json(manifest)


def run_bootstrap_nlmon(args: argparse.Namespace) -> str:
    return run_bootstrap_module(args)


def run_refresh_artifacts(args: argparse.Namespace) -> str:
    manifest = intake.write_planning_artifacts(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        output_dir=args.output_dir,
        artifact_root=_artifact_root_arg(args),
    )
    return intake.stable_json(manifest)


def run_apply_oracle_feedback(args: argparse.Namespace) -> str:
    payload = smoke.apply_oracle_feedback(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        run_record=args.run_record,
        qemu_log=args.qemu_log,
        output=args.output,
        artifact_root=_artifact_root_arg(args),
    )
    return intake.stable_json(payload)


def main() -> int:
    args = parse_args()
    if args.command == "intake-kbuild":
        rendered = run_intake_kbuild(args)
    elif args.command == "audit-bindings":
        rendered = run_audit_bindings(args)
    elif args.command == "plan-patches":
        rendered = run_plan_patches(args)
    elif args.command == "plan-abstractions":
        rendered = run_plan_abstractions(args)
    elif args.command == "plan-translation":
        rendered = run_plan_translation(args)
    elif args.command == "plan-safety-policy":
        rendered = run_plan_safety_policy(args)
    elif args.command == "verify-safety":
        rendered = run_verify_safety(args)
    elif args.command == "plan-agent-workflow":
        rendered = run_plan_agent_workflow(args)
    elif args.command == "gate-agent-candidate":
        rendered = run_gate_agent_candidate(args)
    elif args.command == "run-smoke-qemu":
        rendered = run_smoke_qemu(args)
    elif args.command == "run-oracle":
        rendered = run_oracle(args)
    elif args.command == "bootstrap-module":
        rendered = run_bootstrap_module(args)
    elif args.command == "refresh-artifacts":
        rendered = run_refresh_artifacts(args)
    elif args.command == "apply-oracle-feedback":
        rendered = run_apply_oracle_feedback(args)
    else:
        rendered = run_bootstrap_nlmon(args)

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
