import pytest

from typebench.contracts.taxonomy import ResultClass, ThreadMode, is_constrained

_SUCCESS = {ResultClass.CLEAN, ResultClass.DIAGNOSTICS}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(ThreadMode.CONSTRAINED, True), (ThreadMode.ALL_CORES, False)],
)
def test_is_constrained(mode: ThreadMode, expected: bool) -> None:
    assert is_constrained(mode) is expected


def test_is_constrained_covers_every_member() -> None:
    # Calling every member must not raise (no assert_never tripped at runtime).
    for mode in ThreadMode:
        assert isinstance(is_constrained(mode), bool)


@pytest.mark.parametrize("rc", list(ResultClass))
def test_is_measured_success_covers_every_member(rc: ResultClass) -> None:
    # Every member resolves to a bool with no assert_never tripped at runtime,
    # and only clean/diagnostics are successes.
    assert rc.is_measured_success is (rc in _SUCCESS)
