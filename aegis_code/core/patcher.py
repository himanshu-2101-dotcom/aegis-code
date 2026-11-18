"""Patcher node – synthesizes code patches using libcst and LLM."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import libcst as cst
from langchain_core.messages import HumanMessage, SystemMessage

from aegis_code.core.triage import TriagedFailure
from aegis_code.utils.helpers import get_llm, read_source, unified_diff, truncate
from aegis_code.utils.logger import get_logger

log = get_logger("patcher")

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert Python developer specializing in bug fixing and defensive programming.
    Your job is to patch Python source code to fix bugs found by adversarial testing.

    RULES:
    1. Return ONLY the complete patched source file inside a fenced code block.
    2. Preserve the original code structure as much as possible.
    3. Add proper error handling (try/except), input validation, and type checks.
    4. For race conditions, add appropriate locking (threading.Lock, asyncio.Lock).
    5. For DB issues, add transaction management and connection error handling.
    6. For boundary issues, add input validation and bounds checking.
    7. Do NOT remove existing functionality — only add guards and fixes.
    8. Do NOT add new dependencies unless absolutely necessary.
    9. Add brief inline comments explaining each fix with the prefix "# AEGIS-FIX:".
    10. The patched code MUST be syntactically valid Python.

    OUTPUT FORMAT:
    ```python
    <complete patched source file>
    ```
""")


@dataclass
class PatchResult:
    """Result of a patching attempt."""
    file_path: str
    original_code: str
    patched_code: str
    diff: str
    failures_addressed: list[str]
    success: bool
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "diff": self.diff,
            "failures_addressed": self.failures_addressed,
            "success": self.success,
            "error": self.error,
        }


def _extract_code_block(response_text: Any) -> str:
    """Extract the Python code block from the LLM response."""
    if isinstance(response_text, list):
        parts = []
        for item in response_text:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
            elif hasattr(item, "text"):
                parts.append(getattr(item, "text"))
            else:
                parts.append(str(item))
        response_text = "\n".join(parts)
    elif not isinstance(response_text, str):
        response_text = str(response_text)

    pattern = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
    match = pattern.search(response_text)
    if match:
        return match.group(1).strip()

    pattern2 = re.compile(r"```\s*\n(.*?)```", re.DOTALL)
    match2 = pattern2.search(response_text)
    if match2:
        return match2.group(1).strip()

    return response_text.strip()


def _validate_syntax(code: str) -> tuple[bool, str]:
    """Validate that *code* is syntactically valid Python using libcst."""
    try:
        cst.parse_module(code)
        return True, ""
    except cst.ParserSyntaxError as exc:
        return False, f"Syntax error at line {exc.raw_line}: {exc.message}"


def _group_failures_by_file(
    failures: list[TriagedFailure],
    target_path: str,
) -> dict[str, list[TriagedFailure]]:
    """Group failures by the source file they relate to."""
    grouped: dict[str, list[TriagedFailure]] = {}

    for failure in failures:
        # Try to determine the source file from the traceback
        file_path = failure.file_path

        if not file_path or not Path(file_path).exists():
            # Try to extract from traceback
            matches = re.findall(r"(/\S+\.py)", failure.traceback)
            for m in matches:
                p = Path(m)
                if p.exists() and str(p).startswith(target_path):
                    file_path = str(p)
                    break

        if not file_path:
            # Try relative paths
            matches = re.findall(r"(\S+\.py)", failure.traceback)
            for m in matches:
                candidate = Path(target_path) / m
                if candidate.exists():
                    file_path = str(candidate)
                    break

        if file_path:
            grouped.setdefault(file_path, []).append(failure)
        else:
            log.warning(
                "Could not determine source file for failure: %s",
                failure.test_name,
            )

    return grouped


def generate_patches(
    failures: list[TriagedFailure],
    target_path: str,
    *,
    model: str | None = None,
    attempt: int = 1,
) -> list[PatchResult]:
    """Generate patches for the given triaged failures.

    Groups failures by source file and generates one patch per file.

    Returns
    -------
    List of ``PatchResult`` objects.
    """
    if not failures:
        log.info("No failures to patch")
        return []

    llm = get_llm(temperature=0.1, model=model)
    grouped = _group_failures_by_file(failures, target_path)

    if not grouped:
        log.warning("Could not map any failures to source files")
        return []

    log.info(
        "Patching %d files for %d failures (attempt %d)",
        len(grouped),
        len(failures),
        attempt,
    )

    results: list[PatchResult] = []

    for file_path, file_failures in grouped.items():
        try:
            original_code = read_source(Path(file_path))
        except Exception as exc:
            log.error("Cannot read %s: %s", file_path, exc)
            results.append(
                PatchResult(
                    file_path=file_path,
                    original_code="",
                    patched_code="",
                    diff="",
                    failures_addressed=[f.test_name for f in file_failures],
                    success=False,
                    error=str(exc),
                )
            )
            continue

        # Build failure descriptions
        failure_descriptions = []
        for f in file_failures:
            failure_descriptions.append(
                f"- Test: {f.test_name}\n"
                f"  Exception: {f.exception_type}\n"
                f"  Severity: {f.severity.value}\n"
                f"  Root cause: {f.root_cause_hint}\n"
                f"  Traceback (excerpt):\n"
                f"    {truncate(f.traceback, 1500)}"
            )
        failures_text = "\n\n".join(failure_descriptions)

        user_prompt = textwrap.dedent(f"""\
            SOURCE FILE: {file_path}
            PATCH ATTEMPT: {attempt} of 3

            ORIGINAL CODE:
            ```python
            {truncate(original_code, 8000)}
            ```

            FAILURES TO FIX:
            {failures_text}

            Generate the complete patched version of this file that fixes all
            the issues above. Remember to add "# AEGIS-FIX:" comments.
        """)

        try:
            messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
            response = llm.invoke(messages)
            patched_code = _extract_code_block(response.content)

            if not patched_code:
                raise ValueError("LLM returned empty code")

            # Validate syntax
            is_valid, syntax_err = _validate_syntax(patched_code)
            if not is_valid:
                log.warning("Patch has syntax errors: %s — requesting fix", syntax_err)
                # One-shot retry for syntax
                fix_prompt = (
                    f"The patched code has a syntax error:\n{syntax_err}\n\n"
                    f"Here is the broken patch:\n```python\n{patched_code}\n```\n\n"
                    "Please fix the syntax error and return the corrected complete file."
                )
                fix_response = llm.invoke(
                    [
                        SystemMessage(content=_SYSTEM_PROMPT),
                        HumanMessage(content=fix_prompt),
                    ]
                )
                patched_code = _extract_code_block(fix_response.content)
                is_valid, syntax_err = _validate_syntax(patched_code)
                if not is_valid:
                    raise ValueError(f"Patch still has syntax errors after retry: {syntax_err}")

            diff = unified_diff(original_code, patched_code, filename=file_path)

            if not diff.strip():
                log.warning("Patch for %s produced no diff — LLM returned identical code", file_path)
                results.append(
                    PatchResult(
                        file_path=file_path,
                        original_code=original_code,
                        patched_code=patched_code,
                        diff="",
                        failures_addressed=[f.test_name for f in file_failures],
                        success=False,
                        error="No changes produced",
                    )
                )
                continue

            results.append(
                PatchResult(
                    file_path=file_path,
                    original_code=original_code,
                    patched_code=patched_code,
                    diff=diff,
                    failures_addressed=[f.test_name for f in file_failures],
                    success=True,
                )
            )
            log.info("Patch generated for %s (%d lines changed)", file_path, diff.count("\n"))

        except Exception as exc:
            log.error("Patch generation failed for %s: %s", file_path, exc)
            results.append(
                PatchResult(
                    file_path=file_path,
                    original_code=original_code,
                    patched_code="",
                    diff="",
                    failures_addressed=[f.test_name for f in file_failures],
                    success=False,
                    error=str(exc),
                )
            )

    return results


def apply_patches(patches: list[PatchResult]) -> list[PatchResult]:
    """Write patched code back to disk for the successful patches.

    Returns the same list with any write errors recorded.
    """
    applied: list[PatchResult] = []
    for patch in patches:
        if not patch.success or not patch.patched_code:
            applied.append(patch)
            continue
        try:
            path = Path(patch.file_path)
            path.write_text(patch.patched_code, encoding="utf-8")
            log.info("Applied patch to %s", patch.file_path)
            applied.append(patch)
        except Exception as exc:
            patch.success = False
            patch.error = f"Failed to write patch: {exc}"
            log.error("Write failed for %s: %s", patch.file_path, exc)
            applied.append(patch)
    return applied


def revert_patches(patches: list[PatchResult]) -> None:
    """Restore original code for all patches (rollback)."""
    for patch in patches:
        if patch.original_code and Path(patch.file_path).exists():
            try:
                Path(patch.file_path).write_text(
                    patch.original_code, encoding="utf-8"
                )
                log.debug("Reverted %s", patch.file_path)
            except Exception as exc:
                log.error("Revert failed for %s: %s", patch.file_path, exc)
