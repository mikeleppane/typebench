from pathlib import Path

import pytest

from typebench.corpus import CorpusProject, SizeBucket, load_suite

_SUITE = Path(__file__).parent.parent / "corpus" / "suite.toml"


def test_load_suite_reads_httpx_entry() -> None:
    projects = load_suite(_SUITE)
    names = {p.name for p in projects}
    assert "httpx" in names
    httpx = next(p for p in projects if p.name == "httpx")
    assert httpx.sha == "80960fa31918d7663c3f4c3ad61661cf0e80628f"
    assert httpx.size_bucket is SizeBucket.SMALL
    assert httpx.src_roots == ("httpx",)
    assert httpx.install == ("uv pip install .",)
    assert httpx.python_platform == "linux"  # §6 locks platform too
    # load_suite resolves the repo-relative constraints to an absolute path so
    # preflight works from any CWD (spec §83 lock).
    assert httpx.constraints is not None
    assert Path(httpx.constraints).is_absolute()
    assert httpx.constraints.endswith("corpus/locks/httpx-0.28.0.txt")


def test_load_suite_resolves_constraints_cwd_independent() -> None:
    # The lock is found regardless of the process CWD: load_suite anchors the
    # repo-relative `constraints` at the repo root (the suite's grandparent), not CWD.
    httpx = next(p for p in load_suite(_SUITE) if p.name == "httpx")
    assert httpx.constraints is not None
    assert Path(httpx.constraints).is_file()  # the committed lock actually exists


def test_effective_excludes_merges_defaults_then_entry() -> None:
    proj = CorpusProject(
        name="x",
        repo_url="https://example.invalid/x",
        sha="0" * 40,
        tag="v1",
        size_bucket=SizeBucket.SMALL,
        python_version="3.12",
        src_roots=("x",),
        install=("uv pip install .",),
        exclude_globs=("**/extra/**",),
    )
    eff = proj.effective_excludes()
    assert "**/tests/**" in eff  # §6 default preserved
    assert "**/extra/**" in eff  # entry extension appended
    assert eff[-1] == "**/extra/**"


def test_corpus_rejects_non_dir_segment_exclude_glob() -> None:
    # The counter + mypy adapter derive an excluded DIRECTORY-NAME set from these
    # globs, so a file glob or scoped path would silently miscount. Reject it.
    with pytest.raises(ValueError, match="dir-segment"):
        CorpusProject(
            name="x",
            repo_url="u",
            sha="0" * 40,
            tag="v1",
            size_bucket=SizeBucket.SMALL,
            python_version="3.12",
            src_roots=("x",),
            install=("uv pip install .",),
            exclude_globs=("**/*.pyi",),  # a file glob, not **/<dir>/**
        )


def test_load_suite_rejects_unknown_field(tmp_path: Path) -> None:
    bad = tmp_path / "suite.toml"
    bad.write_text(
        '[[project]]\nname = "x"\nrepo_url = "u"\nsha = "s"\ntag = "t"\n'
        'size_bucket = "small"\npython_version = "3.12"\nsrc_roots = ["x"]\n'
        'install = ["uv pip install ."]\nbogus = 1\n'
    )
    with pytest.raises(ValueError, match="bogus"):
        load_suite(bad)


def test_load_suite_rejects_duplicate_names(tmp_path: Path) -> None:
    dup = tmp_path / "suite.toml"
    entry = (
        '[[project]]\nname = "x"\nrepo_url = "u"\nsha = "s"\ntag = "t"\n'
        'size_bucket = "small"\npython_version = "3.12"\nsrc_roots = ["x"]\n'
        'install = ["uv pip install ."]\n'
    )
    dup.write_text(entry + entry)
    with pytest.raises(ValueError, match="duplicate"):
        load_suite(dup)
