from pathlib import Path

from typebench.counting import count_first_party
from typebench.normalized_config import DEFAULT_EXCLUDES


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_counts_only_python_files_under_roots(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    _write(pkg / "a.py", "x = 1\ny = 2\n")
    _write(pkg / "b.py", "z = 3\n")
    _write(pkg / "README.md", "not python\n")
    fc = count_first_party([pkg], DEFAULT_EXCLUDES)
    assert fc.files == 2
    assert fc.loc == 3  # 2 lines + 1 line


def test_excludes_tests_and_pycache(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    _write(pkg / "a.py", "x = 1\n")
    _write(pkg / "tests" / "test_a.py", "assert True\n")
    _write(pkg / "__pycache__" / "a.cpython-312.pyc.py", "garbage\n")
    fc = count_first_party([pkg], DEFAULT_EXCLUDES)
    assert fc.files == 1  # only a.py; tests/ and __pycache__/ dropped


def test_empty_root_counts_zero(tmp_path: Path) -> None:
    fc = count_first_party([tmp_path / "missing"], DEFAULT_EXCLUDES)
    assert fc.files == 0
    assert fc.loc == 0


def test_multiple_roots_are_summed(tmp_path: Path) -> None:
    _write(tmp_path / "one" / "a.py", "a = 1\n")
    _write(tmp_path / "two" / "b.py", "b = 2\n")
    fc = count_first_party([tmp_path / "one", tmp_path / "two"], DEFAULT_EXCLUDES)
    assert fc.files == 2
