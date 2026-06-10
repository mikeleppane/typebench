"""Per-version frozen checker environments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from typebench.contracts.identity import CheckerRuntime, CheckerSpec, Source
from typebench.corpus.envman import PrepareError, Runner, RunOut, lock_hash, run_subprocess

__all__ = ["PrepareError", "RunOut", "Runner", "cache_status", "prepare_checker"]

_SIDECAR = "checker.json"


def _fingerprint(spec: CheckerSpec, python_version: str, python_platform: str) -> str:
    """Cache-validity key for axes not fully captured by the human dir name."""
    payload = "\x00".join(
        [
            spec.tool,
            spec.version or "latest",
            spec.label or "",
            spec.source.value,
            python_version,
            python_platform,
        ]
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _checker_dir(cache_root: Path, checker_id: str, fingerprint: str) -> Path:
    """Per-identity env dir with a short fingerprint suffix."""
    return cache_root / "checkers" / f"{checker_id}-{fingerprint[:12]}"


def _check(out: RunOut, what: str) -> RunOut:
    if out.returncode != 0:
        detail = (out.stderr.strip() or out.stdout.strip())[-500:]
        msg = f"{what} failed (exit {out.returncode}): {detail}"
        raise PrepareError(msg)
    return out


def _venv_python(venv: Path) -> str:
    """Absolute path to the venv interpreter, without following the symlink."""
    return os.path.abspath(venv / "bin" / "python")  # noqa: PTH100 - non-symlink-following


def _venv_binary(venv: Path, tool: str) -> str:
    """Absolute path to the tool entry point inside the per-version venv."""
    return str((venv / "bin" / tool).absolute())


def _install_spec(spec: CheckerSpec) -> str:
    """Return the uv install target for pinned or latest resolution."""
    return f"{spec.tool}=={spec.version}" if spec.version is not None else spec.tool


def _resolved_version(tool: str, frozen: tuple[str, ...], declared: str | None) -> str:
    """Read the exact installed checker version from the frozen dependency set."""
    normalized_tool = tool.replace("_", "-").lower()
    for line in frozen:
        name, separator, version = line.partition("==")
        if separator and name.replace("_", "-").lower() == normalized_tool:
            return version.strip()
    if declared is not None:
        return declared
    msg = f"could not resolve installed version of {tool!r} from freeze"
    raise PrepareError(msg)


def _resolved_checker_id(spec: CheckerSpec, resolved_version: str) -> str:
    """Runtime matrix key with `latest` replaced by the exact resolved version."""
    base = f"{spec.tool}@{resolved_version}"
    return f"{base}+{spec.label}" if spec.label else base


def prepare_checker(
    spec: CheckerSpec,
    cache_root: Path,
    *,
    install_source: str,
    python_version: str = "3.12",
    python_platform: str = "linux",
    run: Runner = run_subprocess,
) -> CheckerRuntime:
    """Build or reuse a frozen checker venv and return its resolved runtime."""
    if spec.source is not Source.PYPI:
        msg = f"checkerenv builds only the 'pypi' source; got {spec.source.value!r}"
        raise PrepareError(msg)

    cache_root = cache_root.resolve()
    declared_id = spec.checker_id()
    fingerprint = _fingerprint(spec, python_version, python_platform)
    dest = _checker_dir(cache_root, declared_id, fingerprint)
    sidecar = dest / _SIDECAR

    if sidecar.is_file() and spec.version is not None:
        try:
            data = _read_sidecar(sidecar)
            sidecar_fingerprint = data.pop("fingerprint", None)
            cached = _runtime_from_sidecar(data)
        except (ValueError, OSError, TypeError):
            shutil.rmtree(dest, ignore_errors=True)
        else:
            if sidecar_fingerprint == fingerprint and Path(cached.binary).exists():
                return cached
            shutil.rmtree(dest, ignore_errors=True)

    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)

    venv = dest / "venv"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _check(
            run(["uv", "venv", "--python", python_version, str(venv)], None, None),
            "uv venv",
        )
        venv_python = _venv_python(venv)
        install_spec = _install_spec(spec)
        _check(
            run(["uv", "pip", "install", "--python", venv_python, install_spec], None, None),
            f"uv pip install {install_spec}",
        )
        freeze = _check(
            run(["uv", "pip", "freeze", "--python", venv_python], None, None),
            "uv pip freeze",
        )
        frozen = tuple(sorted(line for line in freeze.stdout.splitlines() if line.strip()))
        version = _resolved_version(spec.tool, frozen, spec.version)
        runtime = CheckerRuntime(
            checker_id=_resolved_checker_id(spec, version),
            tool=spec.tool,
            binary=_venv_binary(venv, spec.tool),
            version=version,
            lock_hash=lock_hash(frozen),
            install_source=install_source,
        )
        _write_sidecar_atomic(sidecar, runtime, fingerprint)
        return runtime
    except PrepareError:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def cache_status(
    spec: CheckerSpec,
    cache_root: Path,
    *,
    python_version: str = "3.12",
    python_platform: str = "linux",
) -> tuple[str, str | None]:
    """Return the checker env cache state without building anything."""
    if spec.version is None:
        return ("will-build", None)

    cache_root = cache_root.resolve()
    fingerprint = _fingerprint(spec, python_version, python_platform)
    dest = _checker_dir(cache_root, spec.checker_id(), fingerprint)
    sidecar = dest / _SIDECAR
    if not sidecar.is_file():
        return ("will-build", None)

    try:
        data = _read_sidecar(sidecar)
    except (OSError, ValueError):
        return ("will-build", None)

    version = data.get("version")
    return ("cache-hit", str(version) if version else None)


def _read_sidecar(sidecar: Path) -> dict[str, object]:
    """Read the raw sidecar object without coercing values."""
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = "checker sidecar is not an object"
        raise ValueError(msg)
    out: dict[str, object] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            msg = f"checker sidecar key is not a string: {key!r}"
            raise ValueError(msg)
        out[key] = value
    return out


def _runtime_from_sidecar(data: dict[str, object]) -> CheckerRuntime:
    return CheckerRuntime(
        checker_id=_sidecar_str(data, "checker_id"),
        tool=_sidecar_str(data, "tool"),
        binary=_sidecar_str(data, "binary"),
        version=_sidecar_str(data, "version"),
        lock_hash=_sidecar_str(data, "lock_hash"),
        install_source=_sidecar_str(data, "install_source"),
    )


def _sidecar_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"checker sidecar field {key!r} must be a string"
        raise ValueError(msg)
    return value


def _write_sidecar_atomic(sidecar: Path, runtime: CheckerRuntime, fingerprint: str) -> None:
    """Write the completion marker last via temp file and atomic rename."""
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    tmp = sidecar.with_suffix(".json.tmp")
    data = {**asdict(runtime), "fingerprint": fingerprint}
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(sidecar)
