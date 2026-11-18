"""Shared helper utilities for Aegis-Code."""

from __future__ import annotations

import difflib
import hashlib
import os
import textwrap
from pathlib import Path
from typing import Iterator


# ── LLM provider resolution ──────────────────────────────────

def resolve_llm_provider() -> tuple[str, str]:
    """Determine which LLM backend to use from environment variables.

    Priority:
      1. ``AEGIS_LLM_PROVIDER`` explicit override (``openai`` | ``gemini``)
      2. Whichever API key is set first: ``OPENAI_API_KEY`` then ``GOOGLE_API_KEY``

    Returns
    -------
    (provider, api_key) – e.g. ``("openai", "sk-...")``.

    Raises
    ------
    RuntimeError
        If no API key is found.
    """
    provider_override = os.getenv("AEGIS_LLM_PROVIDER", "").lower().strip()

    if provider_override == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise RuntimeError(
                "AEGIS_LLM_PROVIDER=openai but OPENAI_API_KEY is not set."
            )
        return "openai", key

    if provider_override == "gemini":
        key = os.getenv("GOOGLE_API_KEY", "")
        if not key:
            raise RuntimeError(
                "AEGIS_LLM_PROVIDER=gemini but GOOGLE_API_KEY is not set."
            )
        return "gemini", key

    # Auto-detect
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        return "openai", openai_key

    google_key = os.getenv("GOOGLE_API_KEY", "")
    if google_key:
        return "gemini", google_key

    raise RuntimeError(
        "No LLM API key detected. Set OPENAI_API_KEY or GOOGLE_API_KEY, "
        "and optionally AEGIS_LLM_PROVIDER to 'openai' or 'gemini'."
    )


def get_llm(*, temperature: float = 0.2, model: str | None = None):
    """Return a LangChain chat model for the resolved provider."""
    provider, api_key = resolve_llm_provider()
    env_model = os.getenv("AEGIS_MODEL")

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model or env_model or "gpt-4o",
            temperature=temperature,
            api_key=api_key,
        )

    # Gemini
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model or env_model or "gemini-3.8-flash",
        temperature=temperature,
        google_api_key=api_key,
        max_retries=3,
    )


# ── File discovery ────────────────────────────────────────────

PYTHON_SUFFIXES = {".py"}
IGNORE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    "egg-info",
}


def iter_python_files(root: Path) -> Iterator[Path]:
    """Yield all ``*.py`` files under *root*, skipping common noise dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for fname in sorted(filenames):
            p = Path(dirpath) / fname
            if p.suffix in PYTHON_SUFFIXES:
                yield p


def read_source(path: Path) -> str:
    """Read a file and return its text, replacing surrogate errors."""
    return path.read_text(encoding="utf-8", errors="surrogateescape")


# ── Diff helpers ──────────────────────────────────────────────

def unified_diff(original: str, patched: str, filename: str = "file.py") -> str:
    """Return a unified diff string."""
    orig_lines = original.splitlines(keepends=True)
    patch_lines = patched.splitlines(keepends=True)
    diff = difflib.unified_diff(
        orig_lines,
        patch_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def content_hash(text: str) -> str:
    """SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()


# ── Text helpers ──────────────────────────────────────────────

def truncate(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n... [truncated] ...\n" + text[-half:]


def indent(text: str, prefix: str = "    ") -> str:
    return textwrap.indent(text, prefix)
