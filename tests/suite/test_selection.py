import pytest

from typebench.contracts.identity import CheckerSpec
from typebench.contracts.runconfig import RunConfig
from typebench.contracts.taxonomy import SizeBucket
from typebench.corpus.catalog import CorpusProject
from typebench.suite.selection import SelectionError, resolve_selection


def _entry(name: str, bucket: SizeBucket) -> CorpusProject:
    return CorpusProject(
        name=name,
        repo_url="https://x",
        sha="S",
        tag="v1",
        size_bucket=bucket,
        python_version="3.12",
        src_roots=("pkg",),
        install=("uv pip install .",),
    )


_CORPUS = [
    _entry("httpx", SizeBucket.SMALL),
    _entry("sqlalchemy", SizeBucket.LARGE),
    _entry("numpy", SizeBucket.LARGE),
]


def _cfg(
    *,
    projects: tuple[str, ...] = (),
    buckets: tuple[SizeBucket, ...] = (),
) -> RunConfig:
    return RunConfig(checkers=(CheckerSpec(tool="mypy"),), projects=projects, buckets=buckets)


def test_empty_selection_is_whole_corpus() -> None:
    assert resolve_selection(_cfg(), _CORPUS) == ["httpx", "sqlalchemy", "numpy"]


def test_projects_are_explicit_names() -> None:
    assert resolve_selection(_cfg(projects=("numpy",)), _CORPUS) == ["numpy"]


def test_buckets_expand_to_member_projects() -> None:
    assert resolve_selection(_cfg(buckets=(SizeBucket.LARGE,)), _CORPUS) == [
        "sqlalchemy",
        "numpy",
    ]


def test_projects_and_buckets_union_without_duplicates() -> None:
    out = resolve_selection(_cfg(projects=("numpy",), buckets=(SizeBucket.LARGE,)), _CORPUS)
    # numpy is in both; appears once. Corpus order preserved.
    assert out == ["sqlalchemy", "numpy"]


def test_unknown_project_name_is_loud_error() -> None:
    with pytest.raises(SelectionError, match="ghost"):
        resolve_selection(_cfg(projects=("ghost",)), _CORPUS)
