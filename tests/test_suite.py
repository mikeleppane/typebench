import pytest

from typebench.models import ThreadMode
from typebench.suite import SuiteCell, build_matrix, shard


def test_build_matrix_is_project_major() -> None:
    cells = build_matrix(["a", "b"], ["mypy", "ty"], [ThreadMode.ALL_CORES, ThreadMode.ONE_CORE])
    assert len(cells) == 8
    assert cells[0] == SuiteCell("a", "mypy", ThreadMode.ALL_CORES)
    assert all(isinstance(c, SuiteCell) for c in cells)


def test_shard_partitions_disjointly_and_covers_all() -> None:
    cells = build_matrix(["a", "b", "c"], ["mypy", "ty"], [ThreadMode.ALL_CORES])
    s0 = shard(cells, 0, 3)
    s1 = shard(cells, 1, 3)
    s2 = shard(cells, 2, 3)
    assert set(s0) | set(s1) | set(s2) == set(cells)
    assert not (set(s0) & set(s1))
    assert len(s0) + len(s1) + len(s2) == len(cells)


def test_shard_total_one_is_identity() -> None:
    cells = build_matrix(["a"], ["mypy"], [ThreadMode.ALL_CORES])
    assert shard(cells, 0, 1) == cells


def test_shard_rejects_bad_index() -> None:
    cells = build_matrix(["a"], ["mypy"], [ThreadMode.ALL_CORES])
    with pytest.raises(ValueError):
        shard(cells, 3, 3)
    with pytest.raises(ValueError):
        shard(cells, 0, 0)
