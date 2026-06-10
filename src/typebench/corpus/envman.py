"""Environment management for corpus preparation and preflight.

Clones a corpus project at its pinned SHA, builds an isolated uv venv against
the pinned Python, installs deps via the explicit recipe, and freezes the
resolved versions. All subprocess calls go through an injectable ProcessHost so
command construction is unit-testable offline.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from typebench.contracts.models import PreparedProject
from typebench.corpus.counting import count_code_loc, count_first_party, first_party_files
from typebench.engine.proc import SYSTEM_HOST

if TYPE_CHECKING:
    from collections.abc import Sequence

    from typebench.contracts.proc import ProcessHost, RawRun
    from typebench.corpus.catalog import CorpusProject


class PrepareError(RuntimeError):
    """A preparation step failed."""


def _check(out: RawRun, what: str) -> RawRun:
    if out.exit_code != 0:
        detail = (out.stderr.strip() or out.stdout.strip())[-500:]
        msg = f"{what} failed (exit {out.exit_code}): {detail}"
        raise PrepareError(msg)
    return out


def _venv_python(venv: Path) -> str:
    # abspath, NOT resolve: venv/bin/python is a symlink to the base interpreter;
    # resolving it walks out of the venv and breaks each tool's venv derivation.
    return os.path.abspath(  # noqa: PTH100 - need non-symlink-following abspath
        venv / "bin" / "python"
    )


def _clone(url: str, tag: str, sha: str, repo: Path, host: ProcessHost) -> None:
    """Shallow-fetch the release tag and check out the pinned SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    repo_str = str(repo)
    _check(host.run(["git", "init", "-q", repo_str]), "git init")
    _check(host.run(["git", "-C", repo_str, "remote", "add", "origin", url]), "git remote add")
    _check(
        host.run(
            [
                "git",
                "-C",
                repo_str,
                "fetch",
                "--depth",
                "1",
                "origin",
                f"refs/tags/{tag}:refs/tags/{tag}",
            ]
        ),
        "git fetch",
    )
    _check(host.run(["git", "-C", repo_str, "checkout", "-q", tag]), "git checkout")
    head = _check(host.run(["git", "-C", repo_str, "rev-parse", "HEAD"]), "git rev-parse")
    if head.stdout.strip() != sha:
        msg = f"SHA mismatch after checkout: HEAD={head.stdout.strip()} expected={sha}"
        raise PrepareError(msg)


def _make_venv(python_version: str, venv: Path, host: ProcessHost) -> str:
    """Build the per-project venv and return its interpreter path."""
    _check(
        host.run(["uv", "venv", "--python", python_version, str(venv)]),
        "uv venv",
    )
    return _venv_python(venv)


def _install(
    recipe: Sequence[str],
    repo: Path,
    venv: Path,
    host: ProcessHost,
    *,
    constraints: Path | None = None,
) -> None:
    """Run each install command with the prepared venv active."""
    env = {
        **os.environ,
        "VIRTUAL_ENV": str(venv),
        "PATH": f"{venv / 'bin'}:{os.environ.get('PATH', '')}",
    }
    if constraints is not None:
        env["UV_CONSTRAINT"] = str(constraints)
    for command in recipe:
        _check(host.run(shlex.split(command), cwd=repo, env=env), f"install: {command!r}")


def _freeze(venv_python: str, host: ProcessHost) -> tuple[str, ...]:
    """Return the venv's resolved package versions, sorted."""
    out = _check(host.run(["uv", "pip", "freeze", "--python", venv_python]), "uv pip freeze")
    return tuple(sorted(line for line in out.stdout.splitlines() if line.strip()))


def lock_hash(frozen: tuple[str, ...]) -> str:
    """A stable, order-independent hash of the frozen versions."""
    payload = "\n".join(sorted(frozen)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_SIDECAR = "prepared.json"


def _resolve_constraints(entry: CorpusProject) -> tuple[Path | None, str | None]:
    """Resolve the entry's checked-in constraints lock to an absolute path."""
    if entry.constraints is None:
        return None, None
    path = Path(entry.constraints).resolve()
    if not path.is_file():
        msg = f"constraints lock not found: {entry.constraints}"
        raise PrepareError(msg)
    return path, path.read_text(encoding="utf-8")


def _fingerprint(entry: CorpusProject, constraints_text: str | None) -> str:
    """Cache-validation key for inputs not already in the <name>@<sha> path.

    Includes src_roots and the effective excludes: those drive the canonical
    denominator (and each tool's analyzed set), so editing them for an existing
    <name>@<sha> entry must invalidate the cache. Otherwise a suite edit would
    silently reuse a stale PreparedProject and preflight would compare tools
    against the old denominator (a neutrality defect)."""
    payload = "\n".join(
        [
            entry.sha,
            entry.python_version,
            entry.python_platform,
            "\x00".join(entry.install),
            "\x00".join(entry.src_roots),
            "\x00".join(entry.effective_excludes()),
            constraints_text or "",
        ]
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cache_valid(cached: PreparedProject, fingerprint: str) -> bool:
    """Check fingerprint and live paths before reusing a sidecar."""
    return (
        cached.fingerprint == fingerprint
        and Path(cached.checkout).exists()
        and Path(cached.venv_python).exists()
    )


def _locked_lines(constraints_text: str) -> tuple[str, ...]:
    return tuple(sorted(line.strip() for line in constraints_text.splitlines() if line.strip()))


def _canonical_name(name: str) -> str:
    """PEP 503 normalized distribution name: case-insensitive, with runs of
    `-`/`_`/`.` collapsed to a single `-`. So `Typing_Extensions`, `typing.extensions`,
    and `typing-extensions` all compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize_locked_freeze(
    entry: CorpusProject, frozen: tuple[str, ...], constraints_text: str | None
) -> tuple[str, ...]:
    """Replace uv's local-project direct URL with the stable locked version line.

    `uv pip freeze` records a local install as `name @ file:///...`, which is not
    suitable for a committed constraints lock or cache fingerprint. When a lock is
    declared, use its `name==version` line for the project package and keep the
    rest of the freeze intact. Names are matched PEP 503-normalized, so a corpus
    `name` like `typing_extensions` still matches a freeze/lock line spelled
    `typing-extensions` (otherwise the local `file:` line would survive and the
    drift check would always fail).
    """
    if constraints_text is None:
        return frozen
    target = _canonical_name(entry.name)
    locked_by_name = {
        _canonical_name(line.split("==", 1)[0]): line
        for line in _locked_lines(constraints_text)
        if "==" in line
    }
    replacement = locked_by_name.get(target)
    if replacement is None:
        return frozen

    def _is_local_project(line: str) -> bool:
        # uv emits the local install as "<dist-name> @ file:///..." (no version).
        if " @ " not in line:
            return False
        return _canonical_name(line.split(" @ ", 1)[0].strip()) == target

    return tuple(sorted(replacement if _is_local_project(line) else line for line in frozen))


def _backfill_code_loc(
    cached: PreparedProject, sidecar: Path, *, host: ProcessHost = SYSTEM_HOST
) -> PreparedProject:
    """Recompute tokei code-LOC on a cache hit when it is missing (a cache built
    before tokei integration, or before tokei was installed). The clone + venv are
    still valid, so only the cheap derived count is refreshed — not the whole
    clone/install. Stays None (physical fallback) if tokei is still unavailable or
    the file-set reconciliation fails."""
    if cached.canonical_code_loc is not None:
        return cached
    roots = [Path(root) for root in cached.src_roots]
    code_loc = count_code_loc(first_party_files(roots, cached.exclude_globs), host=host)
    if code_loc is None:
        return cached
    refreshed = cached.model_copy(update={"canonical_code_loc": code_loc})
    sidecar.write_text(refreshed.model_dump_json(indent=2), encoding="utf-8")
    return refreshed


def prepare_project(
    entry: CorpusProject,
    cache_root: Path,
    *,
    host: ProcessHost = SYSTEM_HOST,
) -> PreparedProject:
    """Clone, build venv, install, freeze, verify lock, count, and cache."""
    # Resolve to absolute up front: _install runs the recipe with cwd=repo, so a
    # relative venv/repo (e.g. the default relative `.typebench-cache`) would make
    # VIRTUAL_ENV/PATH resolve against the repo dir instead of the caller's CWD —
    # deps would install nowhere and the freeze would come back empty.
    cache_root = cache_root.resolve()
    dest = cache_root / f"{entry.name}@{entry.sha}"
    sidecar = dest / _SIDECAR
    constraints_path, constraints_text = _resolve_constraints(entry)
    fingerprint = _fingerprint(entry, constraints_text)

    if sidecar.exists():
        try:
            cached = PreparedProject.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except (ValidationError, ValueError, OSError):
            # A corrupt/partial sidecar (e.g. an interrupted write) must not wedge
            # the cache with an uncaught error — treat it like a stale entry.
            shutil.rmtree(dest, ignore_errors=True)
        else:
            if _cache_valid(cached, fingerprint):
                return _backfill_code_loc(cached, sidecar, host=host)
            shutil.rmtree(dest, ignore_errors=True)

    repo = dest / "repo"
    venv = dest / "venv"
    try:
        _clone(entry.repo_url, entry.tag, entry.sha, repo, host)
        venv_python = _make_venv(entry.python_version, venv, host)
        _install(entry.install, repo, venv, host, constraints=constraints_path)
        frozen = _normalize_locked_freeze(entry, _freeze(venv_python, host), constraints_text)
        if constraints_text is not None:
            locked = _locked_lines(constraints_text)
            if lock_hash(frozen) != lock_hash(locked):
                msg = f"dependency lock drift: resolved set != {entry.constraints}"
                raise PrepareError(msg)

        excludes = entry.effective_excludes()
        roots = [repo / root for root in entry.src_roots]
        missing = [str(root) for root in roots if not root.exists()]
        if missing:
            msg = f"src_root(s) missing under checkout: {missing}"
            raise PrepareError(msg)
        counted = count_first_party(roots, excludes)
        if counted.files == 0:
            msg = f"no first-party Python files counted under src_roots={entry.src_roots}"
            raise PrepareError(msg)
        code_loc = count_code_loc(first_party_files(roots, excludes), host=host)

        prepared = PreparedProject(
            name=entry.name,
            checkout=str(repo.resolve()),
            venv_python=venv_python,
            src_roots=tuple(str(root.resolve()) for root in roots),
            exclude_globs=excludes,
            python_version=entry.python_version,
            python_platform=entry.python_platform,
            sha=entry.sha,
            lock_hash=lock_hash(frozen),
            frozen=frozen,
            canonical_files=counted.files,
            canonical_loc=counted.loc,
            canonical_code_loc=code_loc,
            fingerprint=fingerprint,
        )
        sidecar.write_text(prepared.model_dump_json(indent=2), encoding="utf-8")
        return prepared
    except PrepareError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
