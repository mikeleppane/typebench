import shutil
from pathlib import Path

import pytest

from typebench import counting
from typebench.contracts.config import DEFAULT_EXCLUDES
from typebench.counting import count_code_loc, count_first_party, first_party_files


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


def test_first_party_files_returns_the_canonical_set(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    _write(pkg / "a.py", "x = 1\n")
    _write(pkg / "tests" / "t.py", "assert True\n")
    files = first_party_files([pkg], DEFAULT_EXCLUDES)
    assert [f.name for f in files] == ["a.py"]


@pytest.mark.skipif(shutil.which("tokei") is None, reason="tokei not installed")
def test_count_code_loc_excludes_comments_and_blanks(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("# comment\n\nx = 1\ny = 2\n", encoding="utf-8")
    assert count_code_loc([f]) == 2


def test_count_code_loc_returns_none_without_tokei(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = tmp_path / "m.py"
    f.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(counting.shutil, "which", lambda _name: None, raising=True)
    assert count_code_loc([f]) is None


def test_count_code_loc_none_on_empty_input(tmp_path: Path) -> None:
    assert count_code_loc([]) is None
