#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import argparse
import json
from pathlib import Path

import autoprofiler
import benchmark
import candidate_targets
import intake
import safety
import semantics
import smoke
import workflow


def _add_kernel_tree_args(parser: argparse.ArgumentParser, *, include_artifact_root: bool = False) -> None:
    parser.add_argument("--kernel-tree")
    parser.add_argument("--repo-root")
    if include_artifact_root:
        parser.add_argument("--artifact-root")


def _add_module_selector_args(
    parser: argparse.ArgumentParser,
    *,
    default_module_path: str | None = None,
    require_output_compat: bool = False,
) -> None:
    if default_module_path is not None:
        parser.add_argument("--module-path", default=default_module_path)
    else:
        parser.add_argument("--module-path")
    parser.add_argument("--profile-id")
    parser.add_argument("--profile-path")
    parser.add_argument("--source-tree")


def _kernel_tree_arg(args: argparse.Namespace) -> str | None:
    return getattr(args, "kernel_tree", None) or getattr(args, "repo_root", None)


def _artifact_root_arg(args: argparse.Namespace) -> str | None:
    return getattr(args, "artifact_root", None)


def _module_selector_kwargs(args: argparse.Namespace) -> dict:
    return {
        "profile_id": getattr(args, "profile_id", None),
        "source_tree": getattr(args, "source_tree", None),
    }


def _intake_selector_kwargs(args: argparse.Namespace) -> dict:
    return {
        **_module_selector_kwargs(args),
        "profile_path": getattr(args, "profile_path", None),
    }


def _add_benchmark_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--reference-tree")
    parser.add_argument("--official-reference")
    parser.add_argument("--official-reference-initial")
    parser.add_argument("--build-dir-c")
    parser.add_argument("--build-dir-rust")


def _benchmark_kwargs(args: argparse.Namespace) -> dict:
    return {
        "repo_root": _kernel_tree_arg(args),
        "artifact_root": _artifact_root_arg(args),
        "module_path": getattr(args, "module_path", None),
        "profile_id": getattr(args, "profile_id", None),
        "source_tree": getattr(args, "source_tree", None),
        "output_dir": getattr(args, "output_dir", None),
        "reference_tree": getattr(args, "reference_tree", None),
        "official_reference": getattr(args, "official_reference", None),
        "official_reference_initial": getattr(args, "official_reference_initial", None),
        "build_dir_c": getattr(args, "build_dir_c", None),
        "build_dir_rust": getattr(args, "build_dir_rust", None),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C2SafeRust tool bootstrap CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    intake_kbuild = subparsers.add_parser("intake-kbuild")
    _add_module_selector_args(intake_kbuild, default_module_path="drivers/net/nlmon.c")
    intake_kbuild.add_argument("--output", required=True)
    _add_kernel_tree_args(intake_kbuild, include_artifact_root=True)

    audit_bindings = subparsers.add_parser("audit-bindings")
    _add_module_selector_args(audit_bindings, default_module_path="drivers/net/nlmon.c")
    audit_bindings.add_argument("--binding-output", required=True)
    audit_bindings.add_argument("--helper-output", required=True)
    _add_kernel_tree_args(audit_bindings, include_artifact_root=True)

    plan_external_headers = subparsers.add_parser("plan-external-headers")
    _add_module_selector_args(plan_external_headers, default_module_path="drivers/net/nlmon.c")
    plan_external_headers.add_argument("--output", required=True)
    _add_kernel_tree_args(plan_external_headers, include_artifact_root=True)

    plan_patches = subparsers.add_parser("plan-patches")
    _add_module_selector_args(plan_patches, default_module_path="drivers/net/nlmon.c")
    plan_patches.add_argument("--kbuild-output", required=True)
    plan_patches.add_argument("--bindings-output", required=True)
    plan_patches.add_argument("--helpers-output", required=True)
    _add_kernel_tree_args(plan_patches, include_artifact_root=True)

    plan_abstractions = subparsers.add_parser("plan-abstractions")
    _add_module_selector_args(plan_abstractions, default_module_path="drivers/net/nlmon.c")
    plan_abstractions.add_argument("--abstraction-output", required=True)
    plan_abstractions.add_argument("--unsafe-output", required=True)
    _add_kernel_tree_args(plan_abstractions, include_artifact_root=True)

    plan_translation = subparsers.add_parser("plan-translation")
    _add_module_selector_args(plan_translation, default_module_path="drivers/net/nlmon.c")
    plan_translation.add_argument("--translation-output", required=True)
    _add_kernel_tree_args(plan_translation, include_artifact_root=True)

    plan_safety = subparsers.add_parser("plan-safety-policy")
    _add_module_selector_args(plan_safety, default_module_path="drivers/net/nlmon.c")
    plan_safety.add_argument("--policy-output", required=True)
    plan_safety.add_argument("--discharge-output", required=True)
    _add_kernel_tree_args(plan_safety, include_artifact_root=True)

    plan_command_semantics = subparsers.add_parser("plan-command-semantics")
    _add_module_selector_args(plan_command_semantics, default_module_path="drivers/net/nlmon.c")
    plan_command_semantics.add_argument("--output", required=True)
    _add_kernel_tree_args(plan_command_semantics, include_artifact_root=True)

    verify_safety = subparsers.add_parser("verify-safety")
    _add_module_selector_args(verify_safety, default_module_path="drivers/net/nlmon.c")
    verify_safety.add_argument("--output", required=True)
    verify_safety.add_argument("--ledger-output")
    _add_kernel_tree_args(verify_safety, include_artifact_root=True)

    verify_command_semantics = subparsers.add_parser("verify-command-semantics")
    _add_module_selector_args(verify_command_semantics, default_module_path="drivers/net/nlmon.c")
    verify_command_semantics.add_argument("--output", required=True)
    _add_kernel_tree_args(verify_command_semantics, include_artifact_root=True)

    build_safety_violation_ledger = subparsers.add_parser("build-safety-violation-ledger")
    build_safety_violation_ledger.add_argument("--verdict", required=True)
    build_safety_violation_ledger.add_argument("--output", required=True)

    plan_agent_workflow = subparsers.add_parser("plan-agent-workflow")
    _add_module_selector_args(plan_agent_workflow, default_module_path="drivers/net/nlmon.c")
    plan_agent_workflow.add_argument("--output", required=True)
    _add_kernel_tree_args(plan_agent_workflow, include_artifact_root=True)

    gate_agent_candidate = subparsers.add_parser("gate-agent-candidate")
    _add_module_selector_args(gate_agent_candidate, default_module_path="drivers/net/nlmon.c")
    gate_agent_candidate.add_argument("--output", required=True)
    gate_agent_candidate.add_argument("--safety-output")
    _add_kernel_tree_args(gate_agent_candidate, include_artifact_root=True)
    gate_agent_candidate.add_argument("--build-dir")
    gate_agent_candidate.add_argument("--make-llvm")
    gate_agent_candidate.add_argument("--skip-compile", action="store_true")

    run_smoke_qemu = subparsers.add_parser("run-smoke-qemu")
    _add_module_selector_args(run_smoke_qemu, default_module_path="drivers/net/nlmon.c")
    run_smoke_qemu.add_argument("--output", required=True)
    run_smoke_qemu.add_argument("--qemu-log-output")
    _add_kernel_tree_args(run_smoke_qemu, include_artifact_root=True)
    run_smoke_qemu.add_argument("--build-dir")
    run_smoke_qemu.add_argument("--make-llvm")
    run_smoke_qemu.add_argument("--timeout-sec", type=int, default=120)

    run_oracle = subparsers.add_parser("run-oracle")
    _add_module_selector_args(run_oracle)
    run_oracle.add_argument("--output", required=True)
    run_oracle.add_argument("--qemu-log-output")
    _add_kernel_tree_args(run_oracle, include_artifact_root=True)
    run_oracle.add_argument("--build-dir")
    run_oracle.add_argument("--make-llvm")
    run_oracle.add_argument("--timeout-sec", type=int, default=120)

    run_differential_oracle = subparsers.add_parser("run-differential-oracle")
    _add_module_selector_args(run_differential_oracle)
    run_differential_oracle.add_argument("--output", required=True)
    _add_kernel_tree_args(run_differential_oracle, include_artifact_root=True)
    run_differential_oracle.add_argument("--build-dir")
    run_differential_oracle.add_argument("--make-llvm")
    run_differential_oracle.add_argument("--timeout-sec", type=int, default=120)
    run_differential_oracle.add_argument("--baseline-kernel-tree", required=True)
    run_differential_oracle.add_argument("--baseline-module-path")
    run_differential_oracle.add_argument("--baseline-profile-id")
    run_differential_oracle.add_argument("--baseline-source-tree")
    run_differential_oracle.add_argument("--baseline-build-dir")

    bootstrap = subparsers.add_parser("bootstrap-nlmon")
    _add_module_selector_args(bootstrap, default_module_path="drivers/net/nlmon.c")
    bootstrap.add_argument("--output-dir", required=True)
    _add_kernel_tree_args(bootstrap, include_artifact_root=True)

    bootstrap_module = subparsers.add_parser("bootstrap-module")
    _add_module_selector_args(bootstrap_module)
    bootstrap_module.add_argument("--output-dir", required=True)
    _add_kernel_tree_args(bootstrap_module, include_artifact_root=True)

    refresh_artifacts = subparsers.add_parser("refresh-artifacts")
    _add_module_selector_args(refresh_artifacts)
    refresh_artifacts.add_argument("--output-dir")
    _add_kernel_tree_args(refresh_artifacts, include_artifact_root=True)

    auto_process = subparsers.add_parser("auto-process")
    auto_process.add_argument("--module-path", required=True)
    auto_process.add_argument("--output-dir", required=True)
    auto_process.add_argument("--source-tree")
    _add_kernel_tree_args(auto_process, include_artifact_root=True)

    bootstrap_benchmark = subparsers.add_parser("bootstrap-benchmark")
    _add_module_selector_args(bootstrap_benchmark)
    _add_benchmark_args(bootstrap_benchmark)
    _add_kernel_tree_args(bootstrap_benchmark, include_artifact_root=True)

    init_blind_worktree_pair = subparsers.add_parser("init-blind-worktree-pair")
    init_blind_worktree_pair.add_argument("--baseline-rev", required=True)
    init_blind_worktree_pair.add_argument("--blind-tree", required=True)
    init_blind_worktree_pair.add_argument("--reference-tree", required=True)
    init_blind_worktree_pair.add_argument("--benchmark-id")
    init_blind_worktree_pair.add_argument("--forbidden-relpath")
    init_blind_worktree_pair.add_argument("--seal-filename", default=".c2saferust-blind-seal.json")
    _add_kernel_tree_args(init_blind_worktree_pair, include_artifact_root=False)

    refresh_benchmark_summary = subparsers.add_parser("refresh-benchmark-summary")
    refresh_benchmark_summary.add_argument("--manifest")
    refresh_benchmark_summary.add_argument("--output")
    _add_module_selector_args(refresh_benchmark_summary)
    _add_benchmark_args(refresh_benchmark_summary)
    _add_kernel_tree_args(refresh_benchmark_summary, include_artifact_root=True)

    record_benchmark_provenance = subparsers.add_parser("record-benchmark-provenance")
    record_benchmark_provenance.add_argument("--manifest")
    record_benchmark_provenance.add_argument("--kind", required=True)
    record_benchmark_provenance.add_argument("--actor", default="agent")
    record_benchmark_provenance.add_argument("--label")
    record_benchmark_provenance.add_argument("--command", dest="provenance_command")
    record_benchmark_provenance.add_argument("--path", action="append")
    record_benchmark_provenance.add_argument("--note")
    record_benchmark_provenance.add_argument("--contamination-status", choices=["clean", "contaminated"])
    _add_module_selector_args(record_benchmark_provenance)
    _add_benchmark_args(record_benchmark_provenance)
    _add_kernel_tree_args(record_benchmark_provenance, include_artifact_root=True)

    build_benchmark_experiment_report = subparsers.add_parser("build-benchmark-experiment-report")
    build_benchmark_experiment_report.add_argument("--manifest")
    build_benchmark_experiment_report.add_argument("--output", required=True)
    _add_module_selector_args(build_benchmark_experiment_report)
    _add_benchmark_args(build_benchmark_experiment_report)
    _add_kernel_tree_args(build_benchmark_experiment_report, include_artifact_root=True)

    build_difference_metrics = subparsers.add_parser("build-difference-metrics")
    build_difference_metrics.add_argument("--candidate", required=True)
    build_difference_metrics.add_argument("--reference", required=True)
    build_difference_metrics.add_argument("--output", required=True)
    build_difference_metrics.add_argument("--benchmark-id")
    _add_kernel_tree_args(build_difference_metrics, include_artifact_root=False)

    collect_benchmark_portfolio = subparsers.add_parser("collect-benchmark-portfolio")
    collect_benchmark_portfolio.add_argument("--root", action="append")
    collect_benchmark_portfolio.add_argument("--output", required=True)
    collect_benchmark_portfolio.add_argument("--csv-output", required=True)

    apply_oracle_feedback = subparsers.add_parser("apply-oracle-feedback")
    _add_module_selector_args(apply_oracle_feedback)
    apply_oracle_feedback.add_argument("--run-record", required=True)
    apply_oracle_feedback.add_argument("--qemu-log")
    apply_oracle_feedback.add_argument("--output")
    _add_kernel_tree_args(apply_oracle_feedback, include_artifact_root=True)

    score_candidate_targets = subparsers.add_parser("score-candidate-targets")
    score_candidate_targets.add_argument("--output", required=True)

    return parser.parse_args()


def run_intake_kbuild(args: argparse.Namespace) -> str:
    payload = intake.build_kbuild_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_audit_bindings(args: argparse.Namespace) -> str:
    binding_payload = intake.build_binding_gap_audit(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    helper_payload = intake.build_helper_audit(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    intake.write_json(args.binding_output, binding_payload)
    intake.write_json(args.helper_output, helper_payload)
    return intake.stable_json(binding_payload)


def run_plan_external_headers(args: argparse.Namespace) -> str:
    payload = intake.build_external_header_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_plan_patches(args: argparse.Namespace) -> str:
    kbuild_payload = intake.build_kbuild_patch_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    bindings_payload = intake.build_bindings_patch_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    helpers_payload = intake.build_helpers_patch_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
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
        **_intake_selector_kwargs(args),
    )
    unsafe_payload = intake.build_unsafe_obligations(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    intake.write_json(args.abstraction_output, abstraction_payload)
    intake.write_json(args.unsafe_output, unsafe_payload)
    return intake.stable_json(abstraction_payload)


def run_plan_translation(args: argparse.Namespace) -> str:
    payload = intake.build_translation_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    intake.write_json(args.translation_output, payload)
    return intake.stable_json(payload)


def run_plan_safety_policy(args: argparse.Namespace) -> str:
    policy_payload = intake.build_safety_policy(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
    )
    discharge_payload = intake.build_soundness_discharge(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
    )
    intake.write_json(args.policy_output, policy_payload)
    intake.write_json(args.discharge_output, discharge_payload)
    return intake.stable_json(policy_payload)


def run_plan_command_semantics(args: argparse.Namespace) -> str:
    payload = intake.build_command_semantics(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_verify_safety(args: argparse.Namespace) -> str:
    payload = safety.verify_module_safety(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
    )
    context = intake.resolve_module_context(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        profile_id=getattr(args, "profile_id", None),
        profile_path=getattr(args, "profile_path", None),
        source_tree=getattr(args, "source_tree", None),
    )
    ledger_output = (
        Path(args.ledger_output)
        if getattr(args, "ledger_output", None)
        else intake._path_from_repo(
            context["repo_root"],
            intake._artifact_path_for(
                context["module_id"],
                "safety-violation-ledger.json",
                repo_root=context["repo_root"],
                artifact_root=_artifact_root_arg(args),
            ),
        )
    )
    if "violation_ledger" not in payload:
        payload["violation_ledger"] = safety.build_safety_violation_ledger(
            {
                "module_id": payload.get("module_id", context["module_id"]),
                "driver_rust_path": payload.get("driver_rust_path"),
                "violations": payload.get("violations", []),
            },
            verdict_artifact=args.output,
        )
    payload["violation_ledger_artifact"] = str(ledger_output)
    safety.write_verdict(args.output, payload)
    safety.write_violation_ledger(ledger_output, payload["violation_ledger"])
    return intake.stable_json(payload)


def run_verify_command_semantics(args: argparse.Namespace) -> str:
    payload = semantics.verify_module_command_semantics(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
    )
    semantics.write_verdict(args.output, payload)
    return intake.stable_json(payload)


def run_build_safety_violation_ledger(args: argparse.Namespace) -> str:
    verdict = json.loads(Path(args.verdict).read_text())
    payload = safety.build_safety_violation_ledger(verdict, verdict_artifact=args.verdict)
    safety.write_violation_ledger(args.output, payload)
    return intake.stable_json(payload)


def run_plan_agent_workflow(args: argparse.Namespace) -> str:
    payload = intake.build_agent_workflow_plan(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_intake_selector_kwargs(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_gate_agent_candidate(args: argparse.Namespace) -> str:
    payload = workflow.gate_agent_candidate(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
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
        **_module_selector_kwargs(args),
        output=args.output,
        qemu_log_output=args.qemu_log_output,
        build_dir=args.build_dir,
        make_llvm=args.make_llvm,
        timeout_sec=args.timeout_sec,
    )
    return intake.stable_json(payload)


def run_differential_oracle(args: argparse.Namespace) -> str:
    payload = smoke.run_differential_oracle(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
        baseline_kernel_tree=args.baseline_kernel_tree,
        baseline_module_path=args.baseline_module_path,
        baseline_profile_id=args.baseline_profile_id,
        baseline_source_tree=args.baseline_source_tree,
        output=args.output,
        build_dir=args.build_dir,
        baseline_build_dir=args.baseline_build_dir,
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
        **_intake_selector_kwargs(args),
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
        **_intake_selector_kwargs(args),
    )
    return intake.stable_json(manifest)


def run_bootstrap_benchmark(args: argparse.Namespace) -> str:
    payload = benchmark.write_benchmark_bundle(
        args.benchmark_id,
        **_benchmark_kwargs(args),
    )
    return intake.stable_json(payload)


def run_init_blind_worktree_pair(args: argparse.Namespace) -> str:
    payload = benchmark.initialize_blind_worktree_pair(
        repo_root=_kernel_tree_arg(args),
        baseline_rev=args.baseline_rev,
        blind_tree=args.blind_tree,
        reference_tree=args.reference_tree,
        benchmark_id=args.benchmark_id,
        forbidden_relpath=args.forbidden_relpath,
        seal_filename=args.seal_filename,
    )
    return intake.stable_json(payload)


def run_refresh_benchmark_summary(args: argparse.Namespace) -> str:
    manifest_path = getattr(args, "manifest", None)
    if manifest_path is None:
        context = benchmark.resolve_benchmark_context(
            args.benchmark_id,
            **_benchmark_kwargs(args),
        )
        manifest_path = context["benchmark_artifacts"]["module_manifest"]
    payload = benchmark.build_validator_ready_summary(manifest_path, output=args.output)
    return intake.stable_json(payload)


def run_record_benchmark_provenance(args: argparse.Namespace) -> str:
    payload = benchmark.record_provenance_event(
        manifest_path=args.manifest,
        benchmark_id=args.benchmark_id,
        kind=args.kind,
        actor=args.actor,
        label=args.label,
        command=args.provenance_command,
        paths=args.path,
        note=args.note,
        contamination_status=args.contamination_status,
        **_benchmark_kwargs(args),
    )
    return intake.stable_json(payload)


def run_build_benchmark_experiment_report(args: argparse.Namespace) -> str:
    manifest_path = getattr(args, "manifest", None)
    if manifest_path is None:
        manifest_path = benchmark.resolve_benchmark_manifest_path(
            benchmark_id=args.benchmark_id,
            **_benchmark_kwargs(args),
        )
    payload = benchmark.build_experiment_report(manifest_path, output=args.output)
    return intake.stable_json(payload)


def run_build_difference_metrics(args: argparse.Namespace) -> str:
    payload = benchmark.build_difference_metrics(
        args.candidate,
        args.reference,
        benchmark_id=args.benchmark_id,
        repo_root=_kernel_tree_arg(args),
    )
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def run_collect_benchmark_portfolio(args: argparse.Namespace) -> str:
    payload = benchmark.collect_benchmark_portfolio(
        args.root,
        output=args.output,
        csv_output=args.csv_output,
    )
    return intake.stable_json(payload)


def run_apply_oracle_feedback(args: argparse.Namespace) -> str:
    payload = smoke.apply_oracle_feedback(
        args.module_path,
        repo_root=_kernel_tree_arg(args),
        run_record=args.run_record,
        qemu_log=args.qemu_log,
        output=args.output,
        artifact_root=_artifact_root_arg(args),
        **_module_selector_kwargs(args),
    )
    return intake.stable_json(payload)


def run_score_candidate_targets(args: argparse.Namespace) -> str:
    payload = candidate_targets.build_candidate_target_scoreboard()
    intake.write_json(args.output, payload)
    return intake.stable_json(payload)


def _rel_or_abs(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def run_auto_process(args: argparse.Namespace) -> str:
    repo_root = Path(_kernel_tree_arg(args)).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    discovery = autoprofiler.discover_auto_profile(
        args.module_path,
        repo_root=repo_root,
        source_tree=getattr(args, "source_tree", None),
        output_dir=output_dir,
    )

    profile_path = output_dir / "auto-profile.json"
    report_path = output_dir / "auto-profile-report.json"
    intake.write_json(profile_path, discovery["generated_profile"])
    intake.write_json(report_path, discovery["report"])

    bootstrap_manifest = intake.write_planning_artifacts(
        args.module_path,
        repo_root=repo_root,
        output_dir=output_dir,
        artifact_root=_artifact_root_arg(args),
        profile_path=profile_path,
        source_tree=getattr(args, "source_tree", None),
    )

    manifest = {
        "schema_version": 1,
        "artifact_type": "auto-process-manifest",
        "module_id": discovery["generated_profile"]["module_id"],
        "profile_id": discovery["generated_profile"]["profile_id"],
        "selected_family_id": discovery["selected_family_id"],
        "selection_mode": discovery["selection_mode"],
        "generated_artifacts": intake._dedupe_preserve_order(
            [
                _rel_or_abs(repo_root, profile_path),
                _rel_or_abs(repo_root, report_path),
                *bootstrap_manifest["generated_artifacts"],
            ]
        ),
    }
    manifest_path = output_dir / "auto-process-manifest.json"
    intake.write_json(manifest_path, manifest)
    manifest["generated_artifacts"] = intake._dedupe_preserve_order(
        [*manifest["generated_artifacts"], _rel_or_abs(repo_root, manifest_path)]
    )
    intake.write_json(manifest_path, manifest)
    return intake.stable_json(manifest)


def main() -> int:
    args = parse_args()
    if args.command == "intake-kbuild":
        rendered = run_intake_kbuild(args)
    elif args.command == "audit-bindings":
        rendered = run_audit_bindings(args)
    elif args.command == "plan-external-headers":
        rendered = run_plan_external_headers(args)
    elif args.command == "plan-patches":
        rendered = run_plan_patches(args)
    elif args.command == "plan-abstractions":
        rendered = run_plan_abstractions(args)
    elif args.command == "plan-translation":
        rendered = run_plan_translation(args)
    elif args.command == "plan-safety-policy":
        rendered = run_plan_safety_policy(args)
    elif args.command == "plan-command-semantics":
        rendered = run_plan_command_semantics(args)
    elif args.command == "verify-safety":
        rendered = run_verify_safety(args)
    elif args.command == "verify-command-semantics":
        rendered = run_verify_command_semantics(args)
    elif args.command == "build-safety-violation-ledger":
        rendered = run_build_safety_violation_ledger(args)
    elif args.command == "plan-agent-workflow":
        rendered = run_plan_agent_workflow(args)
    elif args.command == "gate-agent-candidate":
        rendered = run_gate_agent_candidate(args)
    elif args.command == "run-smoke-qemu":
        rendered = run_smoke_qemu(args)
    elif args.command == "run-oracle":
        rendered = run_oracle(args)
    elif args.command == "run-differential-oracle":
        rendered = run_differential_oracle(args)
    elif args.command == "bootstrap-module":
        rendered = run_bootstrap_module(args)
    elif args.command == "refresh-artifacts":
        rendered = run_refresh_artifacts(args)
    elif args.command == "auto-process":
        rendered = run_auto_process(args)
    elif args.command == "bootstrap-benchmark":
        rendered = run_bootstrap_benchmark(args)
    elif args.command == "init-blind-worktree-pair":
        rendered = run_init_blind_worktree_pair(args)
    elif args.command == "refresh-benchmark-summary":
        rendered = run_refresh_benchmark_summary(args)
    elif args.command == "record-benchmark-provenance":
        rendered = run_record_benchmark_provenance(args)
    elif args.command == "build-benchmark-experiment-report":
        rendered = run_build_benchmark_experiment_report(args)
    elif args.command == "build-difference-metrics":
        rendered = run_build_difference_metrics(args)
    elif args.command == "collect-benchmark-portfolio":
        rendered = run_collect_benchmark_portfolio(args)
    elif args.command == "apply-oracle-feedback":
        rendered = run_apply_oracle_feedback(args)
    elif args.command == "score-candidate-targets":
        rendered = run_score_candidate_targets(args)
    else:
        rendered = run_bootstrap_nlmon(args)

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
