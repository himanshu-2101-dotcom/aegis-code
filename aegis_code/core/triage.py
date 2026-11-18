"""Triage node – filters sandbox results to actionable failures."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from aegis_code.utils.logger import get_logger

log = get_logger("triage")


class Severity(str, Enum):
    CRITICAL = "critical"   # Unhandled 500s, state corruption
    HIGH = "high"           # Unexpected exceptions, timeouts
    MEDIUM = "medium"       # Logic errors, assertion failures
    IGNORED = "ignored"     # Expected validation errors (422, ValueError, etc.)


@dataclass
class TriagedFailure:
    """A single triaged test failure."""

    test_name: str
    exception_type: str
    traceback: str
    severity: Severity
    file_path: str = ""
    line_number: int = 0
    root_cause_hint: str = ""

    @property
    def actionable(self) -> bool:
        return self.severity != Severity.IGNORED

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "exception_type": self.exception_type,
            "traceback": self.traceback,
            "severity": self.severity.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "root_cause_hint": self.root_cause_hint,
        }


# ── Patterns for classification ───────────────────────────────

_IGNORED_EXCEPTIONS = {
    "ValidationError",
    "RequestValidationError",
    "ValueError",
    "HTTPException",  # only when status == 422
    "pydantic.ValidationError",
    "pydantic_core._pydantic_core.ValidationError",
    "marshmallow.exceptions.ValidationError",
}

_CRITICAL_PATTERNS = [
    re.compile(r"status[_ ]?code.*500", re.IGNORECASE),
    re.compile(r"Internal\s+Server\s+Error", re.IGNORECASE),
    re.compile(r"state\s+corrupt", re.IGNORECASE),
    re.compile(r"data\s*(base|integrity)\s*error", re.IGNORECASE),
    re.compile(r"segmentation\s+fault", re.IGNORECASE),
    re.compile(r"double\s+free", re.IGNORECASE),
    re.compile(r"deadlock", re.IGNORECASE),
    re.compile(r"race\s+condition", re.IGNORECASE),
]

_TIMEOUT_PATTERNS = [
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed?\s*out", re.IGNORECASE),
    re.compile(r"TimeoutError"),
    re.compile(r"asyncio\.TimeoutError"),
]

_422_PATTERN = re.compile(r"(422|Unprocessable\s+Entity)", re.IGNORECASE)

# ── Parsing helpers ───────────────────────────────────────────

_FAILURE_BLOCK_RE = re.compile(
    r"_{3,}\s*(FAILURES|ERRORS)\s*_{3,}"
    r"(.*?)"
    r"(?=_{3,}|={3,}\s*short\s+test\s+summary|$)",
    re.DOTALL,
)

_SINGLE_FAILURE_RE = re.compile(
    r"_{3,}\s*(?P<test_name>\S+)\s*_{3,}"
    r"(?P<body>.*?)"
    r"(?=_{3,}|\Z)",
    re.DOTALL,
)

_EXCEPTION_LINE_RE = re.compile(
    r"^(?:E\s+)?(?P<exc_type>[\w.]+(?:Error|Exception|Fault|Timeout))\s*[:(\s]",
    re.MULTILINE,
)

_SHORT_SUMMARY_RE = re.compile(
    r"FAILED\s+(?P<test>\S+)\s*-\s*(?P<exc>[\w.]+)",
)

_FILE_LINE_RE = re.compile(
    r"(?P<path>[^\s]+\.py):(?P<line>\d+)",
)


# ── Core logic ────────────────────────────────────────────────


def _classify_severity(
    exception_type: str,
    traceback_text: str,
) -> Severity:
    """Assign a severity level to a failure based on its exception and traceback."""

    # Check if this is an expected validation / 422 error
    base_exc = exception_type.rsplit(".", 1)[-1] if "." in exception_type else exception_type

    if base_exc in _IGNORED_EXCEPTIONS:
        # Only ignore HTTPException if it's a 422
        if base_exc == "HTTPException" and not _422_PATTERN.search(traceback_text):
            pass  # Not a 422, continue classification
        else:
            return Severity.IGNORED

    if _422_PATTERN.search(traceback_text) and base_exc in {"HTTPException", "RequestValidationError"}:
        return Severity.IGNORED

    # Critical: 500s, state corruption, deadlocks
    for pattern in _CRITICAL_PATTERNS:
        if pattern.search(traceback_text):
            return Severity.CRITICAL

    # High: timeouts
    for pattern in _TIMEOUT_PATTERNS:
        if pattern.search(traceback_text) or pattern.search(exception_type):
            return Severity.HIGH

    # Medium: everything else (assertion errors, unexpected exceptions)
    return Severity.MEDIUM


def _extract_root_cause(traceback_text: str) -> str:
    """Extract a one-line root cause hint from the traceback."""
    lines = traceback_text.strip().splitlines()
    # Walk from the bottom to find the first "E " line or the last exception line
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("E "):
            return stripped[2:].strip()
        if re.match(r"[\w.]+(?:Error|Exception)", stripped):
            return stripped
    return lines[-1].strip() if lines else "Unknown"


def _extract_file_location(traceback_text: str) -> tuple[str, int]:
    """Try to extract the source file and line from the traceback."""
    matches = list(_FILE_LINE_RE.finditer(traceback_text))
    if matches:
        last = matches[-1]
        return last.group("path"), int(last.group("line"))
    return "", 0


def parse_pytest_output(output: str) -> list[TriagedFailure]:
    """Parse raw pytest stdout/stderr into a list of ``TriagedFailure`` objects."""
    failures: list[TriagedFailure] = []

    # Strategy 1: Parse the verbose failure blocks
    failure_section_match = _FAILURE_BLOCK_RE.search(output)
    if failure_section_match:
        section = failure_section_match.group(0)
        for m in _SINGLE_FAILURE_RE.finditer(section):
            test_name = m.group("test_name").strip()
            body = m.group("body").strip()

            exc_match = _EXCEPTION_LINE_RE.search(body)
            exception_type = exc_match.group("exc_type") if exc_match else "UnknownError"

            file_path, line_no = _extract_file_location(body)
            severity = _classify_severity(exception_type, body)
            root_cause = _extract_root_cause(body)

            failures.append(
                TriagedFailure(
                    test_name=test_name,
                    exception_type=exception_type,
                    traceback=body,
                    severity=severity,
                    file_path=file_path,
                    line_number=line_no,
                    root_cause_hint=root_cause,
                )
            )

    # Strategy 2: Fallback to short test summary
    if not failures:
        for m in _SHORT_SUMMARY_RE.finditer(output):
            test_name = m.group("test")
            exception_type = m.group("exc")
            severity = _classify_severity(exception_type, output)

            failures.append(
                TriagedFailure(
                    test_name=test_name,
                    exception_type=exception_type,
                    traceback=output[-2000:],  # last 2k chars as context
                    severity=severity,
                    root_cause_hint=f"{exception_type} in {test_name}",
                )
            )

    return failures


def triage(
    stdout: str,
    stderr: str,
    *,
    timed_out: bool = False,
) -> list[TriagedFailure]:
    """Main triage entry point: parse + classify + filter.

    Returns only *actionable* failures (severity != IGNORED).
    """
    combined = f"{stdout}\n{stderr}"

    # Handle timeout as a critical failure
    if timed_out:
        failures = [
            TriagedFailure(
                test_name="<entire test suite>",
                exception_type="TimeoutError",
                traceback=combined[-3000:],
                severity=Severity.CRITICAL,
                root_cause_hint="Test suite exceeded execution timeout",
            )
        ]
    else:
        failures = parse_pytest_output(combined)

    total = len(failures)
    actionable = [f for f in failures if f.actionable]
    ignored = total - len(actionable)

    log.info(
        "Triage: %d total failures → %d actionable, %d ignored",
        total,
        len(actionable),
        ignored,
    )

    for f in actionable:
        log.info(
            "  [%s] %s — %s: %s",
            f.severity.value.upper(),
            f.test_name,
            f.exception_type,
            f.root_cause_hint[:120],
        )

    return actionable
