from typebench.env import detect_env
from typebench.models import EnvFingerprint


def test_detect_env_returns_populated_fingerprint() -> None:
    env = detect_env()
    assert isinstance(env, EnvFingerprint)
    assert env.os  # e.g. "Linux"
    assert env.core_count >= 1
    assert env.python_version.count(".") >= 2
    assert env.cpu_model  # never empty; falls back to a placeholder
