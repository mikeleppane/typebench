from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

from typebench.suite.runner import run_suite
from typebench.suite.services import CorpusCache, LocalBenchEngine, UvCheckerResolver

if TYPE_CHECKING:
    from typebench.suite.ports import BenchEngine, CheckerResolver, CorpusSource


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
