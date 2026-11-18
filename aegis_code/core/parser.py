"""LibCST-based parser – extracts async functions, DB calls, and loop boundaries."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Sequence

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from aegis_code.utils.helpers import iter_python_files, read_source
from aegis_code.utils.logger import get_logger

log = get_logger("parser")

# Known DB / ORM call patterns (module-level names and attribute accesses)
_DB_CALL_NAMES: set[str] = {
    "execute",
    "executemany",
    "fetchone",
    "fetchall",
    "fetchmany",
    "commit",
    "rollback",
    "cursor",
    "connect",
    "session",
    "add",
    "query",
    "filter",
    "filter_by",
    "get",
    "delete",
    "merge",
    "flush",
    "refresh",
    "scalar",
    "all",
    "first",
    "one",
    "one_or_none",
    "run_sync",
}

_DB_MODULE_HINTS: set[str] = {
    "sqlite3",
    "sqlalchemy",
    "asyncpg",
    "aiosqlite",
    "databases",
    "tortoise",
    "peewee",
    "psycopg2",
    "psycopg",
    "pymongo",
    "motor",
    "redis",
}


# ── Data classes ──────────────────────────────────────────────


@dataclass
class LoopInfo:
    kind: str  # "for" | "while" | "async_for" | "comprehension"
    line: int
    has_break: bool = False
    has_continue: bool = False
    is_nested: bool = False


@dataclass
class DBCallInfo:
    call_name: str
    line: int
    module_hint: str = ""


@dataclass
class ParsedFunction:
    name: str
    qualified_name: str
    is_async: bool
    file_path: str
    start_line: int
    end_line: int
    parameters: list[str]
    decorators: list[str]
    db_calls: list[DBCallInfo] = field(default_factory=list)
    loops: list[LoopInfo] = field(default_factory=list)
    source: str = ""

    @property
    def has_db_calls(self) -> bool:
        return len(self.db_calls) > 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedModule:
    file_path: str
    imports: list[str]
    functions: list[ParsedFunction]
    classes: list[str]
    has_db_imports: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ── CST Visitor ───────────────────────────────────────────────


class _AegisVisitor(cst.CSTVisitor):
    """Single-pass visitor that collects functions, DB calls, and loops."""

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, file_path: str, source_lines: list[str]) -> None:
        self.file_path = file_path
        self.source_lines = source_lines

        self.imports: list[str] = []
        self.functions: list[ParsedFunction] = []
        self.classes: list[str] = []
        self.has_db_imports: bool = False

        # stacks for context
        self._func_stack: list[ParsedFunction] = []
        self._class_stack: list[str] = []
        self._loop_depth: int = 0

    # ── imports ───────────────────────────────────────────────

    def visit_Import(self, node: cst.Import) -> bool | None:
        if isinstance(node.names, cst.ImportStar):
            return None
        if isinstance(node.names, Sequence):
            for alias in node.names:
                mod_name = _dotted_name(alias.name)
                self.imports.append(mod_name)
                if mod_name in _DB_MODULE_HINTS:
                    self.has_db_imports = True
        return None

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool | None:
        if node.module:
            mod_name = _dotted_name(node.module)
            self.imports.append(mod_name)
            if any(hint in mod_name for hint in _DB_MODULE_HINTS):
                self.has_db_imports = True
        return None

    # ── classes ───────────────────────────────────────────────

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self._class_stack.append(node.name.value)
        self.classes.append(node.name.value)
        return True

    def leave_ClassDef(self, node: cst.ClassDef) -> None:
        if self._class_stack:
            self._class_stack.pop()

    # ── functions ─────────────────────────────────────────────

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        pos = self.get_metadata(PositionProvider, node)
        start_line = pos.start.line
        end_line = pos.end.line

        params = _extract_params(node.params)
        decorators = [_decorator_name(d) for d in node.decorators]

        qualified = node.name.value
        if self._class_stack:
            qualified = ".".join(self._class_stack) + "." + qualified

        source_text = "\n".join(
            self.source_lines[start_line - 1 : end_line]
        )

        pf = ParsedFunction(
            name=node.name.value,
            qualified_name=qualified,
            is_async=isinstance(node.asynchronous, cst.Asynchronous),
            file_path=self.file_path,
            start_line=start_line,
            end_line=end_line,
            parameters=params,
            decorators=decorators,
            source=source_text,
        )
        self._func_stack.append(pf)
        return True

    def leave_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self._func_stack:
            pf = self._func_stack.pop()
            self.functions.append(pf)

    # ── DB calls ──────────────────────────────────────────────

    def visit_Call(self, node: cst.Call) -> bool | None:
        call_name, module_hint = _resolve_call_name(node.func)
        if call_name and call_name in _DB_CALL_NAMES:
            pos = self.get_metadata(PositionProvider, node)
            info = DBCallInfo(
                call_name=call_name,
                line=pos.start.line,
                module_hint=module_hint,
            )
            if self._func_stack:
                self._func_stack[-1].db_calls.append(info)
        return None

    # ── loops ─────────────────────────────────────────────────

    def visit_For(self, node: cst.For) -> bool | None:
        self._enter_loop(node, "for")
        return True

    def leave_For(self, node: cst.For) -> None:
        self._leave_loop()

    def visit_While(self, node: cst.While) -> bool | None:
        self._enter_loop(node, "while")
        return True

    def leave_While(self, node: cst.While) -> None:
        self._leave_loop()

    def _enter_loop(self, node: cst.For | cst.While, kind: str) -> None:
        pos = self.get_metadata(PositionProvider, node)
        is_nested = self._loop_depth > 0
        loop = LoopInfo(kind=kind, line=pos.start.line, is_nested=is_nested)
        # Detect break / continue inside the loop body by wrapping it in
        # a temporary Module so we can use the full .walk() traversal.
        checker = _BreakContinueChecker()
        try:
            checker.check(node.body)
        except Exception:
            pass
        loop.has_break = checker.has_break
        loop.has_continue = checker.has_continue

        if self._func_stack:
            self._func_stack[-1].loops.append(loop)
        self._loop_depth += 1

    def _leave_loop(self) -> None:
        self._loop_depth = max(0, self._loop_depth - 1)


class _BreakContinueChecker:
    """Recursively search a CST subtree for Break and Continue nodes."""

    def __init__(self) -> None:
        self.has_break = False
        self.has_continue = False

    def check(self, node: cst.CSTNode) -> None:
        """Walk the node tree looking for Break/Continue (skip nested loops)."""
        if isinstance(node, cst.Break):
            self.has_break = True
            return
        if isinstance(node, cst.Continue):
            self.has_continue = True
            return
        # Do not recurse into nested For/While loops
        if isinstance(node, (cst.For, cst.While)):
            return
        # Recurse into all children
        for child in node.children:
            self.check(child)


# ── Helpers ───────────────────────────────────────────────────


def _dotted_name(node: cst.BaseExpression) -> str:
    if isinstance(node, cst.Attribute):
        return _dotted_name(node.value) + "." + node.attr.value
    if isinstance(node, cst.Name):
        return node.value
    return ""


def _resolve_call_name(
    func: cst.BaseExpression,
) -> tuple[str, str]:
    """Return (call_name, module_hint) for a Call.func node."""
    if isinstance(func, cst.Name):
        return func.value, ""
    if isinstance(func, cst.Attribute):
        attr_name = func.attr.value
        module_hint = _dotted_name(func.value)
        return attr_name, module_hint
    return "", ""


def _extract_params(params: cst.Parameters) -> list[str]:
    result: list[str] = []
    for p in (*params.params, *params.kwonly_params):
        result.append(p.name.value)
    if params.star_arg and isinstance(params.star_arg, cst.Param):
        result.append(f"*{params.star_arg.name.value}")
    if params.star_kwarg:
        result.append(f"**{params.star_kwarg.name.value}")
    return result


def _decorator_name(dec: cst.Decorator) -> str:
    return _dotted_name(dec.decorator) if hasattr(dec.decorator, "value") else str(dec.decorator)


# ── Public API ────────────────────────────────────────────────


def parse_file(file_path: Path) -> ParsedModule:
    """Parse a single Python file and return structured analysis."""
    source = read_source(file_path)
    source_lines = source.splitlines()

    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        log.warning("Syntax error in %s: %s — skipping", file_path, exc)
        return ParsedModule(
            file_path=str(file_path),
            imports=[],
            functions=[],
            classes=[],
        )

    wrapper = MetadataWrapper(tree)
    visitor = _AegisVisitor(str(file_path), source_lines)
    wrapper.visit(visitor)

    return ParsedModule(
        file_path=str(file_path),
        imports=visitor.imports,
        functions=visitor.functions,
        classes=visitor.classes,
        has_db_imports=visitor.has_db_imports,
    )


def parse_directory(target_path: Path) -> list[ParsedModule]:
    """Walk *target_path* and parse every Python file."""
    results: list[ParsedModule] = []
    files = list(iter_python_files(target_path))
    log.info("Parsing %d Python files in %s", len(files), target_path)

    for fp in files:
        mod = parse_file(fp)
        results.append(mod)
        fn_count = len(mod.functions)
        db_count = sum(len(f.db_calls) for f in mod.functions)
        if fn_count:
            log.debug(
                "  %s → %d funcs, %d DB calls",
                fp.name,
                fn_count,
                db_count,
            )

    total_funcs = sum(len(m.functions) for m in results)
    async_funcs = sum(
        1 for m in results for f in m.functions if f.is_async
    )
    db_funcs = sum(1 for m in results for f in m.functions if f.has_db_calls)
    log.info(
        "Parse complete: %d functions (%d async, %d with DB calls)",
        total_funcs,
        async_funcs,
        db_funcs,
    )
    return results


def parsed_to_json(modules: list[ParsedModule]) -> str:
    """Serialize the parse results to a JSON string for LLM context."""
    return json.dumps(
        [m.to_dict() for m in modules],
        indent=2,
        default=str,
    )
