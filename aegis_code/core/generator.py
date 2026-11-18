"""Attack Generator – uses LLM to produce adversarial pytest suites."""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from aegis_code.core.parser import ParsedFunction, ParsedModule
from aegis_code.utils.helpers import get_llm, truncate
from aegis_code.utils.logger import get_logger

log = get_logger("generator")

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an elite adversarial security and reliability tester.
    Your job is to write aggressive pytest test files that expose bugs,
    race conditions, edge cases, and unhandled exceptions in Python code.

    RULES:
    1. Generate ONLY valid Python pytest code. No explanations outside the code block.
    2. Each test function must start with `test_`.
    3. Use `pytest.raises` for expected exceptions, raw calls for unexpected ones.
    4. Target these categories:
       a) NULL / None / empty-string inputs
       b) Boundary values (0, -1, MAX_INT, huge strings)
       c) Type confusion (passing int where str expected, etc.)
       d) Concurrent access / race conditions (use threading or asyncio)
       e) Resource exhaustion (very large inputs, deep recursion)
       f) State corruption (call methods in wrong order)
       g) SQL injection patterns (if DB calls detected)
       h) Unhandled HTTP status codes (if web endpoints detected)
    5. Import the target module correctly using its path.
    6. Add `@pytest.mark.timeout(30)` to tests that might hang.
    7. Do NOT test for expected ValidationErrors or HTTP 422s — those are handled.
    8. NEVER import or use `unittest.mock` to mock the function under test itself.
       The goal is to test the REAL function.

    OUTPUT FORMAT:
    Return ONLY a fenced Python code block like:
    ```python
    import pytest
    ...test code...
    ```
""")


def _build_function_summary(func: ParsedFunction) -> dict[str, Any]:
    """Create a compact summary of a function for the LLM prompt."""
    return {
        "name": func.qualified_name,
        "is_async": func.is_async,
        "parameters": func.parameters,
        "has_db_calls": func.has_db_calls,
        "db_call_types": [c.call_name for c in func.db_calls],
        "loop_count": len(func.loops),
        "has_nested_loops": any(l.is_nested for l in func.loops),
        "decorators": func.decorators,
        "source": truncate(func.source, 3000),
    }


def _build_module_context(module: ParsedModule) -> str:
    """Build the full LLM context for one module."""
    funcs = [_build_function_summary(f) for f in module.functions]
    context = {
        "file_path": module.file_path,
        "imports": module.imports[:30],  # cap to avoid token bloat
        "has_db_imports": module.has_db_imports,
        "classes": module.classes,
        "functions": funcs,
    }
    return json.dumps(context, indent=2, default=str)


def _extract_code_block(response_text: str) -> str:
    """Pull the Python code from the LLM response."""
    # Try fenced code block first
    pattern = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
    match = pattern.search(response_text)
    if match:
        return match.group(1).strip()

    # Try any fenced block
    pattern2 = re.compile(r"```\s*\n(.*?)```", re.DOTALL)
    match2 = pattern2.search(response_text)
    if match2:
        return match2.group(1).strip()

    # Assume the entire response is code
    return response_text.strip()


def _derive_import_path(file_path: str, target_root: str) -> str:
    """Convert a file path to a Python import path relative to the target root."""
    rel = Path(file_path).relative_to(target_root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].replace(".py", "")
    return ".".join(parts)


def generate_adversarial_tests(
    modules: list[ParsedModule],
    target_path: str,
    *,
    model: str | None = None,
    batch_size: int = 5,
) -> dict[str, str]:
    """Generate adversarial test files for the given parsed modules.

    Returns
    -------
    dict mapping test filename → test file content.
    """
    llm = get_llm(temperature=0.4, model=model)
    test_files: dict[str, str] = {}

    # Filter to modules that have interesting functions
    interesting = [
        m
        for m in modules
        if m.functions and any(
            f.is_async or f.has_db_calls or f.loops
            for f in m.functions
        )
    ]

    # If nothing is "interesting", test everything
    if not interesting:
        interesting = [m for m in modules if m.functions]

    if not interesting:
        log.warning("No functions found to generate tests for")
        return test_files

    log.info("Generating adversarial tests for %d modules", len(interesting))

    for idx, module in enumerate(interesting):
        import_path = _derive_import_path(module.file_path, target_path)
        module_context = _build_module_context(module)

        user_prompt = textwrap.dedent(f"""\
            TARGET MODULE: {module.file_path}
            IMPORT AS: {import_path}

            MODULE ANALYSIS:
            {module_context}

            Generate a comprehensive adversarial pytest test file for this module.
            Focus on the functions that are async, have DB calls, or contain loops.
            The tests should trigger UNHANDLED exceptions, not expected validation errors.
            Make sure all imports are correct and the file is self-contained.
        """)

        try:
            messages = [
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
            response = llm.invoke(messages)
            code = _extract_code_block(response.content)

            if not code or "def test_" not in code:
                log.warning(
                    "LLM did not return valid test code for %s — skipping",
                    module.file_path,
                )
                continue

            # Sanitize: ensure pytest import
            if "import pytest" not in code:
                code = "import pytest\n" + code

            test_filename = f"test_adversarial_{idx:03d}_{Path(module.file_path).stem}.py"
            test_files[test_filename] = code
            log.info("Generated %s (%d chars)", test_filename, len(code))

        except Exception as exc:
            log.error("Failed to generate tests for %s: %s", module.file_path, exc)
            continue

    log.info("Generated %d adversarial test files", len(test_files))
    return test_files
