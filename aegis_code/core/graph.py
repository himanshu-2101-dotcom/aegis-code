"""LangGraph orchestration – wires Parser → Generator → Sandbox → Triage → Patcher → Verify."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import StateGraph, END

from aegis_code.core.parser import ParsedModule, parse_directory
from aegis_code.core.generator import generate_adversarial_tests
from aegis_code.core.sandbox import SandboxRunner, SandboxResult
from aegis_code.core.triage import TriagedFailure, triage
from aegis_code.core.patcher import (
    PatchResult,
    generate_patches,
    apply_patches,
    revert_patches,
)
from aegis_code.utils.logger import get_logger, step_banner, success, warn, error

log = get_logger("graph")

MAX_RETRIES = 3


# ── State schema ──────────────────────────────────────────────


def _merge_lists(a: list, b: list) -> list:
    """Reducer that concatenates two lists (used by Annotated fields)."""
    return a + b


class AegisState(TypedDict, total=False):
    # Inputs
    target_path: str

    # Parser outputs
    parsed_modules: list[dict]

    # Generator outputs
    adversarial_tests: dict[str, str]  # filename → content

    # Sandbox outputs
    sandbox_result: dict

    # Triage outputs
    triaged_failures: list[dict]

    # Patcher outputs
    patches: list[dict]
    patch_attempt: int

    # Verification
    verification_passed: bool
    final_diff: str

    # Tracking
    errors: Annotated[list[str], _merge_lists]
    status: str


# ── Node implementations ─────────────────────────────────────


def parser_node(state: AegisState) -> dict:
    """Node 1 – Parse the target directory with libcst."""
    step_banner("Parser", subtitle="Extracting AST: async funcs, DB calls, loops")

    target = Path(state["target_path"]).resolve()
    if not target.is_dir():
        return {
            "parsed_modules": [],
            "errors": [f"Target path is not a directory: {target}"],
            "status": "error",
        }

    modules = parse_directory(target)
    module_dicts = [m.to_dict() for m in modules]

    total_funcs = sum(len(m.functions) for m in modules)
    if total_funcs == 0:
        return {
            "parsed_modules": module_dicts,
            "errors": ["No Python functions found in target directory"],
            "status": "error",
        }

    return {
        "parsed_modules": module_dicts,
        "status": "parsed",
    }


def generator_node(state: AegisState) -> dict:
    """Node 2 – Generate adversarial pytest files."""
    step_banner("Attack Generator", subtitle="Creating adversarial test suites")

    parsed = state.get("parsed_modules", [])
    if not parsed:
        return {
            "adversarial_tests": {},
            "errors": ["No parsed modules to generate tests from"],
            "status": "error",
        }

    # Reconstruct ParsedModule objects from dicts
    from aegis_code.core.parser import ParsedFunction, ParsedModule, DBCallInfo, LoopInfo

    modules: list[ParsedModule] = []
    for md in parsed:
        functions = []
        for fd in md.get("functions", []):
            db_calls = [DBCallInfo(**dc) for dc in fd.get("db_calls", [])]
            loops = [LoopInfo(**li) for li in fd.get("loops", [])]
            pf = ParsedFunction(
                name=fd["name"],
                qualified_name=fd["qualified_name"],
                is_async=fd["is_async"],
                file_path=fd["file_path"],
                start_line=fd["start_line"],
                end_line=fd["end_line"],
                parameters=fd["parameters"],
                decorators=fd["decorators"],
                db_calls=db_calls,
                loops=loops,
                source=fd.get("source", ""),
            )
            functions.append(pf)

        pm = ParsedModule(
            file_path=md["file_path"],
            imports=md.get("imports", []),
            functions=functions,
            classes=md.get("classes", []),
            has_db_imports=md.get("has_db_imports", False),
        )
        modules.append(pm)

    target_path = state["target_path"]
    test_files = generate_adversarial_tests(modules, target_path)

    if not test_files:
        return {
            "adversarial_tests": {},
            "errors": ["Generator produced no test files"],
            "status": "error",
        }

    return {
        "adversarial_tests": test_files,
        "status": "generated",
    }


def sandbox_node(state: AegisState) -> dict:
    """Node 3 – Run tests in isolated Docker container."""
    step_banner("Sandbox", subtitle="Executing tests in Docker (network disabled)")

    target = Path(state["target_path"]).resolve()
    test_files = state.get("adversarial_tests", {})

    if not test_files:
        return {
            "sandbox_result": {"exit_code": 0, "stdout": "", "stderr": "No tests to run"},
            "status": "no_tests",
        }

    try:
        runner = SandboxRunner()
    except RuntimeError as exc:
        return {
            "sandbox_result": {
                "exit_code": -1,
                "stdout": "",
                "stderr": str(exc),
                "error": str(exc),
            },
            "errors": [f"Docker unavailable: {exc}"],
            "status": "error",
        }

    # Detect existing test directories
    existing_tests = None
    for candidate in ("tests", "test", "Tests"):
        if (target / candidate).is_dir():
            existing_tests = candidate
            break

    # Only run existing tests during verification (patch_attempt > 0)
    is_verification = state.get("patch_attempt", 0) > 0
    result = runner.run_tests(
        target,
        test_files,
        existing_tests_dir=existing_tests if is_verification else None,
    )

    return {
        "sandbox_result": {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "duration_seconds": result.duration_seconds,
            "error": result.error,
            "passed": result.passed,
        },
        "status": "sandbox_complete",
    }


def triage_node(state: AegisState) -> dict:
    """Node 4 – Filter results to actionable failures."""
    step_banner("Triage", subtitle="Classifying failures by severity")

    sr = state.get("sandbox_result", {})
    if sr.get("passed", False):
        success("All tests passed — no failures to triage")
        return {
            "triaged_failures": [],
            "status": "all_passed",
        }

    failures = triage(
        sr.get("stdout", ""),
        sr.get("stderr", ""),
        timed_out=sr.get("timed_out", False),
    )

    if not failures:
        warn("No actionable failures found in test output")
        return {
            "triaged_failures": [],
            "status": "no_actionable_failures",
        }

    return {
        "triaged_failures": [f.to_dict() for f in failures],
        "status": "triaged",
    }


def patcher_node(state: AegisState) -> dict:
    """Node 5 – Generate and apply patches."""
    attempt = state.get("patch_attempt", 0) + 1
    step_banner("Patcher", subtitle=f"Synthesizing fix (attempt {attempt}/{MAX_RETRIES})")

    triaged = state.get("triaged_failures", [])
    if not triaged:
        return {
            "patches": [],
            "patch_attempt": attempt,
            "status": "nothing_to_patch",
        }

    # Reconstruct TriagedFailure objects
    from aegis_code.core.triage import Severity

    failures = [
        TriagedFailure(
            test_name=f["test_name"],
            exception_type=f["exception_type"],
            traceback=f["traceback"],
            severity=Severity(f["severity"]),
            file_path=f.get("file_path", ""),
            line_number=f.get("line_number", 0),
            root_cause_hint=f.get("root_cause_hint", ""),
        )
        for f in triaged
    ]

    target_path = state["target_path"]
    patches = generate_patches(failures, target_path, attempt=attempt)
    applied = apply_patches(patches)

    successful = [p for p in applied if p.success]
    if not successful:
        warn("No patches were successfully generated")
        return {
            "patches": [p.to_dict() for p in applied],
            "patch_attempt": attempt,
            "status": "patch_failed",
        }

    success(f"Applied {len(successful)} patch(es)")
    return {
        "patches": [p.to_dict() for p in applied],
        "patch_attempt": attempt,
        "status": "patched",
    }


def verification_node(state: AegisState) -> dict:
    """Node 6 – Re-run sandbox with adversarial + existing tests."""
    step_banner("Verification", subtitle="Re-running all tests with patches applied")

    # Delegate to sandbox_node — it checks patch_attempt to include existing tests
    return sandbox_node(state)


# ── Routing functions ─────────────────────────────────────────


def should_continue_after_triage(state: AegisState) -> str:
    """After triage, decide whether to patch or finish."""
    failures = state.get("triaged_failures", [])
    status = state.get("status", "")

    if not failures or status in ("all_passed", "no_actionable_failures"):
        return "finish"

    return "patch"


def should_verify_or_finish(state: AegisState) -> str:
    """After patching, decide whether to verify."""
    patches = state.get("patches", [])
    any_success = any(p.get("success", False) for p in patches)

    if not any_success:
        return "finish"

    return "verify"


def should_retry_or_finish(state: AegisState) -> str:
    """After verification, decide whether to retry patching or accept."""
    sr = state.get("sandbox_result", {})
    attempt = state.get("patch_attempt", 0)

    if sr.get("passed", False):
        return "accept"

    if attempt >= MAX_RETRIES:
        warn(f"Max retries ({MAX_RETRIES}) reached")
        return "accept"

    return "retry"


def accept_node(state: AegisState) -> dict:
    """Final node – compile the diff and mark completion."""
    step_banner("Complete", subtitle="Generating final diff")

    patches = state.get("patches", [])
    successful_patches = [p for p in patches if p.get("success", False)]

    combined_diff = "\n".join(p.get("diff", "") for p in successful_patches if p.get("diff"))

    sr = state.get("sandbox_result", {})
    passed = sr.get("passed", False)

    if passed and combined_diff:
        success("All tests pass with patches applied!")
    elif combined_diff:
        warn("Patches generated but some tests still fail")
    else:
        warn("No patches were produced")

    return {
        "final_diff": combined_diff,
        "verification_passed": passed,
        "status": "completed",
    }


# ── Graph construction ────────────────────────────────────────


def build_graph() -> StateGraph:
    """Construct and compile the Aegis-Code LangGraph workflow.

    Flow:
        parser → generator → sandbox → triage
            ├── (no failures) → accept → END
            └── (failures) → patcher
                    ├── (no patch) → accept → END
                    └── (patched) → verification
                            ├── (pass or max retries) → accept → END
                            └── (fail, retries left) → triage → patcher → …
    """
    workflow = StateGraph(AegisState)

    # Add nodes
    workflow.add_node("parser", parser_node)
    workflow.add_node("generator", generator_node)
    workflow.add_node("sandbox", sandbox_node)
    workflow.add_node("triage", triage_node)
    workflow.add_node("patcher", patcher_node)
    workflow.add_node("verification", verification_node)
    workflow.add_node("accept", accept_node)

    # Set entry point
    workflow.set_entry_point("parser")

    # Linear edges
    workflow.add_edge("parser", "generator")
    workflow.add_edge("generator", "sandbox")
    workflow.add_edge("sandbox", "triage")

    # Conditional: after triage → patch or finish
    workflow.add_conditional_edges(
        "triage",
        should_continue_after_triage,
        {
            "patch": "patcher",
            "finish": "accept",
        },
    )

    # Conditional: after patcher → verify or finish
    workflow.add_conditional_edges(
        "patcher",
        should_verify_or_finish,
        {
            "verify": "verification",
            "finish": "accept",
        },
    )

    # Conditional: after verification → retry or accept
    workflow.add_conditional_edges(
        "verification",
        should_retry_or_finish,
        {
            "retry": "triage",
            "accept": "accept",
        },
    )

    # Terminal
    workflow.add_edge("accept", END)

    return workflow


def compile_graph():
    """Build and compile the graph, ready for invocation."""
    graph = build_graph()
    return graph.compile()


def run_scan(target_path: str) -> AegisState:
    """Execute the full Aegis scan pipeline on *target_path*.

    Returns the final state dict.
    """
    app = compile_graph()
    initial_state: AegisState = {
        "target_path": str(Path(target_path).resolve()),
        "parsed_modules": [],
        "adversarial_tests": {},
        "sandbox_result": {},
        "triaged_failures": [],
        "patches": [],
        "patch_attempt": 0,
        "verification_passed": False,
        "final_diff": "",
        "errors": [],
        "status": "starting",
    }

    final_state = app.invoke(initial_state)
    return final_state
