from collections.abc import Callable
from pathlib import Path

import pytest

from typebench.contracts.models import EnvFingerprint

type EnvFactory = Callable[..., EnvFingerprint]


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def make_env() -> EnvFactory:
    def _make_env(**overrides: object) -> EnvFingerprint:
        defaults: dict[str, object] = {
            "os": "Linux",
            "kernel": "6.6",
            "cpu_model": "Test CPU",
            "core_count": 8,
            "python_version": "3.12.0",
        }
        return EnvFingerprint.model_validate(defaults | overrides)

    return _make_env
