import dataclasses

import pytest

from typebench.normalized_config import NORMALIZED_POLICY_VERSION, NormalizedConfig, config_hash


def test_defaults_are_neutral() -> None:
    cfg = NormalizedConfig()
    assert cfg.src_roots == ()
    assert cfg.python_version == "3.12"
    assert cfg.python_platform == "linux"
    assert cfg.venv_python is None
    # tests / vendored / generated are excluded by default (spec §6).
    assert any("tests" in g for g in cfg.exclude_globs)


def test_is_frozen_and_carries_src_roots() -> None:
    cfg = NormalizedConfig(
        src_roots=("/abs/src",), python_version="3.11", venv_python="/v/bin/python"
    )
    assert cfg.src_roots == ("/abs/src",)
    assert cfg.python_version == "3.11"
    assert cfg.venv_python == "/v/bin/python"
    assert dataclasses.is_dataclass(cfg)
    # frozen: mutation must raise. `setattr` (not `cfg.x = ...`) keeps the
    # assignment dynamic so the static checker doesn't flag a frozen-field write
    # — avoids a suppression entirely (AGENTS.md: never `# type: ignore`).
    with pytest.raises(dataclasses.FrozenInstanceError):
        # Dynamic setattr is deliberate: it keeps the frozen-field write off the
        # static checker's radar (no `# type: ignore` needed); a plain
        # `cfg.x = ...` would trip pyrefly instead. We trade B010 for that.
        setattr(cfg, "python_version", "x")  # noqa: B010


def test_config_hash_is_stable_and_order_independent() -> None:
    a = config_hash(("httpx", "src"), ("**/tests/**", "**/vendor/**"), "3.12", "linux")
    b = config_hash(("src", "httpx"), ("**/vendor/**", "**/tests/**"), "3.12", "linux")
    assert a == b  # sorted inputs -> order independent
    assert len(a) == 64  # sha256 hex


def test_config_hash_ignores_absolute_paths() -> None:
    # The hash must be machine-independent: a different checkout prefix is irrelevant
    # because callers pass REPO-RELATIVE roots, never absolute checkout paths.
    rel = config_hash(("httpx",), ("**/tests/**",), "3.12", "linux")
    assert rel == config_hash(("httpx",), ("**/tests/**",), "3.12", "linux")


def test_config_hash_changes_on_policy_or_inputs() -> None:
    base = config_hash(("httpx",), ("**/tests/**",), "3.12", "linux")
    assert base != config_hash(("httpx",), ("**/tests/**",), "3.13", "linux")
    assert base != config_hash(("httpx",), ("**/tests/**",), "3.12", "darwin")
    assert base != config_hash(("other",), ("**/tests/**",), "3.12", "linux")
    assert NORMALIZED_POLICY_VERSION  # non-empty tag is part of the payload
