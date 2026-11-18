# Aegis-Code

> **AST-driven regression & adversarial bug fixer** — powered by LangGraph, libcst, and Docker.

Aegis-Code automatically finds bugs in your Python projects by:

1. **Parsing** your codebase with `libcst` to extract async functions, database calls, and loop boundaries.
2. **Generating** aggressive adversarial `pytest` test suites that target edge cases, race conditions, and unhandled exceptions.
3. **Running** those tests inside an isolated Docker container with network disabled and resource limits.
4. **Triaging** the results — filtering out expected validation errors and surfacing real bugs (500s, state corruption, timeouts).
5. **Patching** the code using `libcst` + LLM-synthesized fixes.
6. **Verifying** the patches by re-running all tests (adversarial + existing) in the sandbox, with up to 3 retry cycles.

## Architecture

```
┌─────────┐    ┌────────────┐    ┌─────────┐    ┌────────┐    ┌─────────┐
│ Parser  │───▸│ Generator  │───▸│ Sandbox │───▸│ Triage │───▸│ Patcher │
│ (libcst)│    │ (LLM)      │    │ (Docker)│    │        │    │ (LLM +  │
│         │    │             │    │         │    │        │    │  libcst)│
└─────────┘    └────────────┘    └─────────┘    └────────┘    └────┬────┘
                                                                   │
                                     ┌──── Retry (max 3) ◂────────┘
                                     │
                                     ▾
                               ┌────────────┐    ┌────────┐
                               │Verification│───▸│  Done  │
                               │  (Sandbox) │    │(git diff)│
                               └────────────┘    └────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- Docker running locally
- An LLM API key (OpenAI or Google Gemini)

### Installation

```bash
# Clone the repository
git clone https://github.com/<your-user>/aegis-code.git
cd aegis-code

# Install dependencies
pip install -r requirements.txt

# Or install as a package
pip install -e .
```

### Configuration

Set your LLM API key as an environment variable:

```bash
# For OpenAI
export OPENAI_API_KEY="sk-..."

# For Google Gemini
export GOOGLE_API_KEY="AIza..."

# Optionally force a specific provider
export AEGIS_LLM_PROVIDER="openai"  # or "gemini"
```

### Usage

```bash
# Run a scan on a target project
aegis scan --path ./my-flask-app

# Dry run (parse + generate tests only, no Docker)
aegis scan --path ./my-flask-app --dry-run

# Save the diff to a file
aegis scan --path ./my-flask-app --output fix.patch

# Verbose logging
aegis scan --path ./my-flask-app --verbose

# View scan history
aegis history

# Clean up Docker images
aegis clean
```

## Project Structure

```
aegis-code/
├── aegis_code/
│   ├── __init__.py
│   ├── cli.py              # Typer CLI entrypoint
│   ├── core/
│   │   ├── __init__.py
│   │   ├── parser.py       # libcst AST analysis
│   │   ├── generator.py    # LLM-powered test generation
│   │   ├── sandbox.py      # Docker sandbox runner
│   │   ├── triage.py       # Failure classification
│   │   ├── patcher.py      # LLM + libcst patching
│   │   └── graph.py        # LangGraph orchestration
│   └── utils/
│       ├── __init__.py
│       ├── logger.py        # Rich logging
│       ├── db.py            # SQLite state storage
│       └── helpers.py       # LLM resolution, file utils
├── tests/
│   └── ...
├── requirements.txt
├── pyproject.toml
└── README.md
```

## How It Works

### Parser Node

Uses `libcst` to perform a full AST analysis of the target codebase. Extracts:
- Async function signatures and bodies
- Database call patterns (SQLAlchemy, sqlite3, asyncpg, etc.)
- Loop boundaries (for/while/async for) with break/continue detection
- Class hierarchies and decorators

### Attack Generator Node

Sends the parsed AST information to an LLM with a carefully crafted system prompt that instructs it to generate adversarial tests targeting:
- Null/None/empty inputs
- Boundary values and type confusion
- Race conditions and concurrent access
- Resource exhaustion
- State corruption
- SQL injection patterns

### Sandbox Node

Runs tests inside an isolated `python:3.11-slim` Docker container with:
- Network disabled during test execution
- CPU and memory limits (configurable)
- Automatic dependency detection and caching
- Timeout enforcement

### Triage Node

Classifies test failures into severity levels:
- **Critical**: 500 errors, state corruption, deadlocks
- **High**: Timeouts, unexpected exceptions
- **Medium**: Assertion failures, logic errors
- **Ignored**: Expected HTTP 422s, handled `ValueError`s

### Patcher Node

For each actionable failure, generates a code patch using the LLM + `libcst`:
- Adds error handling, input validation, and type checks
- Adds locking for race conditions
- Adds transaction management for DB issues
- Validates syntax before applying
- Marks fixes with `# AEGIS-FIX:` comments

### Verification Loop

Re-runs the sandbox with **both** adversarial tests and the target's existing test suite. Retries up to 3 times if tests fail. On success, produces a final `git diff`.

## License

MIT
