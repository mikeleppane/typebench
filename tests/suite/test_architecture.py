from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, override

from typebench.suite.runner import run_suite
from typebench.suite.services import CorpusCache, LocalBenchEngine, UvCheckerResolver

if TYPE_CHECKING:
    from typebench.suite.ports import BenchEngine, CheckerResolver, CorpusSource

_TAXONOMY_ENUMS = {"ResultClass", "ThreadMode"}
_FORBIDDEN_LAYER_IMPORTS: dict[str, set[str]] = {
    "contracts": {"engine", "adapters", "corpus", "suite", "cli"},
    "engine": {"adapters", "corpus", "suite", "cli"},
    "adapters": {"suite", "cli"},
    "corpus": {"suite", "cli"},
    "suite": {"cli"},
    "cli": set(),
}


def test_run_suite_signature_stays_small_and_port_based() -> None:
    params = inspect.signature(run_suite).parameters
    assert list(params) == [
        "config",
        "corpus",
        "resolver",
        "engine",
        "generated_at",
        "shard_index",
        "shard_total",
    ]
    assert "Callable" not in str(inspect.signature(run_suite))


def test_suite_services_conform_to_ports() -> None:
    corpus: CorpusSource = CorpusCache(Path("corpus/suite.toml"), Path("typebench-cache"))
    resolver: CheckerResolver = UvCheckerResolver(Path("typebench-cache"))
    engine: BenchEngine = LocalBenchEngine()

    assert corpus.version()
    assert resolver is not None
    assert engine is not None


def test_process_boundaries_stay_behind_allowed_engine_modules() -> None:
    allowed_subprocess = {
        Path("src/typebench/engine/proc.py"),
        Path("src/typebench/engine/wrapper.py"),
        Path("src/typebench/engine/timing.py"),
        Path("src/typebench/engine/measure.py"),
    }
    allowed_which = {
        Path("src/typebench/engine/proc.py"),
        Path("src/typebench/engine/measure.py"),
    }
    subprocess_offenders: list[str] = []
    which_offenders: list[str] = []

    for path in sorted(Path("src/typebench").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if _imports_subprocess(node) and path not in allowed_subprocess:
                subprocess_offenders.append(str(path))
            if _calls_shutil_which(node) and path not in allowed_which:
                which_offenders.append(str(path))

    assert not subprocess_offenders
    assert not which_offenders


def test_adapters_import_taxonomy_enums_not_models() -> None:
    offenders: list[str] = []

    for path in sorted(Path("src/typebench/adapters").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "typebench.contracts.models":
                for alias in node.names:
                    if alias.name in _TAXONOMY_ENUMS:
                        offenders.append(f"{path} -> {alias.name}")

    assert not offenders, "\n".join(offenders)


def test_runtime_imports_respect_layering() -> None:
    offenders: list[str] = []

    for path in sorted(Path("src/typebench").rglob("*.py")):
        source_layer = _layer_for_path(path)
        if source_layer is None:
            continue

        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _runtime_internal_imports(tree):
            imported_layer = _layer_for_module(module)
            if imported_layer in _FORBIDDEN_LAYER_IMPORTS[source_layer]:
                offenders.append(f"{path} -> {module}")

    assert not offenders, "\n".join(offenders)


def test_measured_path_imports_stay_pydantic_free() -> None:
    code = (
        "import sys\n"
        "import typebench.engine.wrapper\n"
        "import typebench.engine.measure\n"
        "import typebench.engine.calibration\n"
        "import typebench.contracts.taxonomy\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'pydantic')\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def _imports_subprocess(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name == "subprocess" for alias in node.names)
    return isinstance(node, ast.ImportFrom) and node.module == "subprocess"


def _calls_shutil_which(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "shutil"
        and node.func.attr == "which"
    )


def _runtime_internal_imports(tree: ast.AST) -> list[str]:
    visitor = _RuntimeInternalImportVisitor()
    visitor.visit(tree)
    return visitor.imports


class _RuntimeInternalImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.imports: list[str] = []

    @override
    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.startswith("typebench."):
                self.imports.append(alias.name)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None and node.module.startswith("typebench."):
            self.imports.append(node.module)


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _layer_for_path(path: Path) -> str | None:
    relative = path.relative_to("src/typebench")
    first_part = relative.parts[0]
    if first_part == "cli.py":
        return "cli"
    if first_part in _FORBIDDEN_LAYER_IMPORTS:
        return first_part
    return None


def _layer_for_module(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != "typebench":
        return None
    if parts[1] == "cli":
        return "cli"
    if parts[1] in _FORBIDDEN_LAYER_IMPORTS:
        return parts[1]
    return None
