from pathlib import Path

import pytest

from typebench.contracts.configfile import discover_config, load_config, merge_cli, resolve_corpus
from typebench.contracts.identity import CheckerSpec
from typebench.contracts.policy import Policy
from typebench.contracts.runconfig import RunConfig
from typebench.contracts.taxonomy import SizeBucket, ThreadMode

_TOML = """\
policy = "standard"
corpus = "corpus/suite.toml"
projects = ["httpx", "sqlalchemy"]
buckets = ["large"]

[tracks]
thread_modes = ["constrained"]
cores = [1, 4, 8]

[[checker]]
tool = "mypy"
version = "1.18.2"

[[checker]]
tool = "mypy"
version = "1.19.0"
label = "rc"

[[checker]]
tool = "pyright"
version = "1.1.410"

[run]
runs = 5
warmup = 2
mem_runs = 1
"""


def test_load_config_parses_array_of_tables(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text(_TOML, encoding="utf-8")
    cfg = load_config(path)
    assert cfg.checkers == (
        CheckerSpec(tool="mypy", version="1.18.2"),
        CheckerSpec(tool="mypy", version="1.19.0", label="rc"),
        CheckerSpec(tool="pyright", version="1.1.410"),
    )
    assert cfg.policy is Policy.STANDARD
    assert cfg.corpus == Path("corpus/suite.toml")
    assert cfg.projects == ("httpx", "sqlalchemy")
    assert cfg.buckets == (SizeBucket.LARGE,)
    assert cfg.thread_modes == (ThreadMode.CONSTRAINED,)
    assert cfg.cores == (1, 4, 8)
    assert cfg.runs == 5 and cfg.warmup == 2 and cfg.mem_runs == 1


def test_load_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text('bogus = 1\n[[checker]]\ntool = "mypy"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        load_config(path)


def test_load_config_rejects_unknown_checker_key(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text('[[checker]]\ntool = "mypy"\nbogus = 1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        load_config(path)


def test_load_config_rejects_non_string_checker_version(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text('[[checker]]\ntool = "mypy"\nversion = 1.19\n', encoding="utf-8")
    with pytest.raises(ValueError, match="version"):
        load_config(path)


def test_load_config_rejects_unknown_tracks_key(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text('[[checker]]\ntool = "mypy"\n[tracks]\nbogus = 1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        load_config(path)


def test_load_config_rejects_unknown_run_key(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text('[[checker]]\ntool = "mypy"\n[run]\nbogus = 1\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bogus"):
        load_config(path)


def test_discover_config_finds_typebench_toml_in_cwd(tmp_path: Path) -> None:
    (tmp_path / "typebench.toml").write_text('[[checker]]\ntool = "mypy"\n', encoding="utf-8")
    assert discover_config(tmp_path) == tmp_path / "typebench.toml"


def test_discover_config_returns_none_when_absent(tmp_path: Path) -> None:
    assert discover_config(tmp_path) is None


def test_merge_cli_tool_override_replaces_whole_checker_group(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text(_TOML, encoding="utf-8")
    cfg = load_config(path)
    merged = merge_cli(cfg, tools=["mypy@1.19.0"], projects=None, buckets=None, cores=None)
    assert merged.checkers == (CheckerSpec(tool="mypy", version="1.19.0"),)
    assert merged.projects == ("httpx", "sqlalchemy")
    assert merged.buckets == (SizeBucket.LARGE,)


def test_merge_cli_selection_replaces_file_selection_as_a_unit(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text(_TOML, encoding="utf-8")
    cfg = load_config(path)
    merged = merge_cli(cfg, tools=None, projects=["numpy"], buckets=None, cores=None)
    assert merged.projects == ("numpy",)
    assert merged.buckets == ()


def test_merge_cli_cores_override(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text(_TOML, encoding="utf-8")
    cfg = load_config(path)
    merged = merge_cli(cfg, tools=None, projects=None, buckets=None, cores=[1, 8])
    assert merged.cores == (1, 8)


def test_merge_cli_thread_modes_override(tmp_path: Path) -> None:
    path = tmp_path / "typebench.toml"
    path.write_text(_TOML, encoding="utf-8")
    cfg = load_config(path)
    merged = merge_cli(
        cfg,
        tools=None,
        projects=None,
        buckets=None,
        cores=None,
        thread_modes=[ThreadMode.CONSTRAINED],
    )
    assert merged.thread_modes == (ThreadMode.CONSTRAINED,)


def test_resolve_corpus_uses_cli_over_file_over_default(tmp_path: Path) -> None:
    cfg = load_config_from_text(tmp_path, 'corpus = "from-file.toml"\n[[checker]]\ntool = "mypy"\n')
    assert resolve_corpus(cfg, Path("from-cli.toml"), Path("default.toml")) == Path("from-cli.toml")
    assert resolve_corpus(cfg, None, Path("default.toml")) == Path("from-file.toml")

    no_file = load_config_from_text(tmp_path, '[[checker]]\ntool = "mypy"\n')
    assert resolve_corpus(no_file, None, Path("default.toml")) == Path("default.toml")


def load_config_from_text(tmp_path: Path, text: str) -> RunConfig:
    path = tmp_path / "typebench-test-config.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)
