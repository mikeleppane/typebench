"""TOML config loading, discovery, and defaults < file < CLI merging.

`typebench.toml` carries checker specs, selection, track, run, and corpus
settings. Corpus precedence is CLI > file > default; suite/compare wire that
helper into execution in Task 7.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from typebench.contracts.identity import CheckerSpec, Source
from typebench.contracts.policy import Policy
from typebench.contracts.runconfig import RunConfig, merge_tool_override
from typebench.contracts.taxonomy import SizeBucket, ThreadMode

_CONFIG_NAME = "typebench.toml"
_TOP_LEVEL_KEYS = frozenset({"policy", "corpus", "projects", "buckets", "checker", "tracks", "run"})
_CHECKER_KEYS = frozenset({"tool", "version", "label", "source"})
_TRACKS_KEYS = frozenset({"thread_modes", "cores"})
_RUN_KEYS = frozenset(
    {"runs", "warmup", "timeout", "mem_runs", "measure", "calibrate", "calib_runs"}
)


@dataclass(frozen=True, kw_only=True)
class RunCliOverrides:
    """CLI-provided run knob overrides.

    `None` means "keep the file/default value", matching the other merge inputs.
    """

    runs: int | None = None
    warmup: int | None = None
    mem_runs: int | None = None
    timeout: float | None = None
    measure: bool | None = None
    calibrate: bool | None = None
    calib_runs: int | None = None


def discover_config(cwd: Path) -> Path | None:
    """Return the auto-discovered `typebench.toml` in cwd, when present."""
    candidate = cwd / _CONFIG_NAME
    return candidate if candidate.is_file() else None


def _table(value: object, name: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = f"{name} must be a table, got {value!r}"
        raise ValueError(msg)
    return cast("dict[str, object]", value)


def _reject_unknown(table: dict[str, object], known: frozenset[str], name: str) -> None:
    unknown = sorted(set(table) - known)
    if unknown:
        msg = f"unknown {name} key(s): {unknown}"
        raise ValueError(msg)


def _string_list(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{key} must be a list of strings, got {value!r}"
        raise ValueError(msg)
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = f"{key} entries must be strings, got {item!r}"
            raise ValueError(msg)
        out.append(item)
    return tuple(out)


def _int_list(value: object, key: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{key} must be a list of integers, got {value!r}"
        raise ValueError(msg)
    out: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            msg = f"{key} entries must be integers, got {item!r}"
            raise ValueError(msg)
        out.append(item)
    return tuple(out)


def _optional_string(table: dict[str, object], key: str, name: str) -> str | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{name} {key} must be a string, got {value!r}"
        raise ValueError(msg)
    return value


def _checker_specs(raw_checkers: object) -> tuple[CheckerSpec, ...]:
    if raw_checkers is None:
        return ()
    if not isinstance(raw_checkers, list):
        msg = f"checker must be an array of tables, got {raw_checkers!r}"
        raise ValueError(msg)

    specs: list[CheckerSpec] = []
    for entry in raw_checkers:
        if not isinstance(entry, dict):
            msg = f"each [[checker]] must be a table, got {entry!r}"
            raise ValueError(msg)
        table = cast("dict[str, object]", entry)
        _reject_unknown(table, _CHECKER_KEYS, "[[checker]]")

        tool = table.get("tool")
        if not isinstance(tool, str):
            msg = f"[[checker]] requires a string tool, got {tool!r}"
            raise ValueError(msg)
        source = _optional_string(table, "source", "[[checker]]")
        specs.append(
            CheckerSpec(
                tool=tool,
                version=_optional_string(table, "version", "[[checker]]"),
                label=_optional_string(table, "label", "[[checker]]"),
                source=Source(source) if source is not None else Source.PYPI,
            )
        )
    return tuple(specs)


def _thread_modes(value: object) -> tuple[ThreadMode, ...]:
    return tuple(ThreadMode(mode) for mode in _string_list(value, "tracks.thread_modes"))


def _buckets(value: object) -> tuple[SizeBucket, ...]:
    return tuple(SizeBucket(bucket) for bucket in _string_list(value, "buckets"))


def _run_int(table: dict[str, object], key: str) -> int | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"run.{key} must be an integer, got {value!r}"
        raise ValueError(msg)
    return value


def _run_float(table: dict[str, object], key: str) -> float | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        msg = f"run.{key} must be a number, got {value!r}"
        raise ValueError(msg)
    return float(value)


def _run_bool(table: dict[str, object], key: str) -> bool | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        msg = f"run.{key} must be a boolean, got {value!r}"
        raise ValueError(msg)
    return value


def load_config(path: Path) -> RunConfig:
    """Parse `typebench.toml` into a validated RunConfig."""
    raw = cast("dict[str, object]", tomllib.loads(path.read_text(encoding="utf-8")))
    _reject_unknown(raw, _TOP_LEVEL_KEYS, path.name)

    tracks = _table(raw.get("tracks"), "[tracks]")
    _reject_unknown(tracks, _TRACKS_KEYS, "[tracks]")
    run = _table(raw.get("run"), "[run]")
    _reject_unknown(run, _RUN_KEYS, "[run]")

    payload: dict[str, object] = {
        "checkers": _checker_specs(raw.get("checker")),
        "projects": _string_list(raw.get("projects"), "projects"),
        "buckets": _buckets(raw.get("buckets")),
    }
    policy = raw.get("policy")
    if policy is not None:
        if not isinstance(policy, str):
            msg = f"policy must be a string, got {policy!r}"
            raise ValueError(msg)
        payload["policy"] = Policy(policy)
    corpus = raw.get("corpus")
    if corpus is not None:
        if not isinstance(corpus, str):
            msg = f"corpus must be a string, got {corpus!r}"
            raise ValueError(msg)
        payload["corpus"] = Path(corpus)
    if "thread_modes" in tracks:
        payload["thread_modes"] = _thread_modes(tracks["thread_modes"])
    if "cores" in tracks:
        payload["cores"] = _int_list(tracks["cores"], "tracks.cores")
    for key in ("runs", "warmup", "mem_runs", "calib_runs"):
        value = _run_int(run, key)
        if value is not None:
            payload[key] = value
    timeout = _run_float(run, "timeout")
    if timeout is not None:
        payload["timeout"] = timeout
    for key in ("measure", "calibrate"):
        value = _run_bool(run, key)
        if value is not None:
            payload[key] = value

    try:
        return RunConfig.model_validate(payload)
    except ValidationError as exc:
        # Surface just the human messages, not pydantic's full dump (input echo + URL).
        details = "; ".join(error["msg"] for error in exc.errors())
        msg = f"invalid {path.name}: {details}"
        raise ValueError(msg) from exc


def merge_cli(
    config: RunConfig,
    *,
    tools: list[str] | None,
    projects: list[str] | None,
    buckets: list[str] | None,
    cores: list[int] | None,
    thread_modes: list[ThreadMode] | None = None,
    run_overrides: RunCliOverrides | None = None,
) -> RunConfig:
    """Apply CLI overrides onto a file/default RunConfig."""
    updates: dict[str, object] = {}
    if tools is not None:
        updates["checkers"] = merge_tool_override(config.checkers, tools)
    if projects is not None or buckets is not None:
        updates["projects"] = tuple(projects or ())
        updates["buckets"] = tuple(SizeBucket(bucket) for bucket in (buckets or ()))
    if cores is not None:
        updates["cores"] = tuple(cores)
    if thread_modes is not None:
        updates["thread_modes"] = tuple(thread_modes)
    if run_overrides is not None:
        for key, value in (
            ("runs", run_overrides.runs),
            ("warmup", run_overrides.warmup),
            ("mem_runs", run_overrides.mem_runs),
            ("timeout", run_overrides.timeout),
            ("measure", run_overrides.measure),
            ("calibrate", run_overrides.calibrate),
            ("calib_runs", run_overrides.calib_runs),
        ):
            if value is not None:
                updates[key] = value
    return RunConfig.model_validate(config.model_dump() | updates)


def resolve_corpus(config: RunConfig, cli_corpus: Path | None, default: Path) -> Path:
    """Resolve corpus path with CLI > file > default precedence."""
    if cli_corpus is not None:
        return cli_corpus
    if config.corpus is not None:
        return config.corpus
    return default
