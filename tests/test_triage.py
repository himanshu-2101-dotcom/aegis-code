"""Tests for the Aegis-Code triage module."""

import pytest

from aegis_code.core.triage import (
    Severity,
    TriagedFailure,
    parse_pytest_output,
    triage,
    _classify_severity,
)


class TestClassifySeverity:
    def test_value_error_is_ignored(self):
        assert _classify_severity("ValueError", "some traceback") == Severity.IGNORED

    def test_validation_error_is_ignored(self):
        assert _classify_severity("ValidationError", "") == Severity.IGNORED

    def test_http_422_is_ignored(self):
        tb = "HTTPException: 422 Unprocessable Entity"
        assert _classify_severity("HTTPException", tb) == Severity.IGNORED

    def test_http_500_is_critical(self):
        tb = "Response status_code: 500 Internal Server Error"
        assert _classify_severity("RuntimeError", tb) == Severity.CRITICAL

    def test_deadlock_is_critical(self):
        tb = "detected a deadlock in the connection pool"
        assert _classify_severity("OperationalError", tb) == Severity.CRITICAL

    def test_timeout_is_high(self):
        assert _classify_severity("TimeoutError", "operation timed out") == Severity.HIGH

    def test_assertion_error_is_medium(self):
        assert _classify_severity("AssertionError", "assert 1 == 2") == Severity.MEDIUM

    def test_generic_exception_is_medium(self):
        assert _classify_severity("RuntimeError", "something broke") == Severity.MEDIUM


class TestTriage:
    def test_triage_timeout(self):
        result = triage("", "timeout", timed_out=True)
        assert len(result) == 1
        assert result[0].severity == Severity.CRITICAL

    def test_triage_filters_ignored(self):
        output = (
            "___ FAILURES ___\n"
            "___ test_validation ___\n"
            "E   ValueError: invalid input\n"
            "____________________________\n"
        )
        result = triage(output, "")
        # ValueError should be ignored, so no actionable failures
        assert all(f.severity != Severity.IGNORED for f in result)

    def test_triage_keeps_critical(self):
        output = (
            "FAILED test_crash.py::test_crash - RuntimeError: Internal Server Error status_code 500"
        )
        result = triage(output, "")
        assert len(result) >= 1
        assert any(f.severity == Severity.CRITICAL for f in result)


class TestParsePytestOutput:
    def test_parses_short_summary(self):
        output = "FAILED test_foo.py::test_bar - RuntimeError: boom"
        failures = parse_pytest_output(output)
        assert len(failures) >= 1
        assert failures[0].exception_type == "RuntimeError"

    def test_empty_output_gives_no_failures(self):
        failures = parse_pytest_output("")
        assert len(failures) == 0
