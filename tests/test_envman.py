import shutil
import subprocess
from pathlib import Path

import pytest

from typebench.corpus import CorpusProject, SizeBucket
from typebench.envman import (
    _SIDECAR,
    PrepareError,
    RunOut,
    _clone,
    _fingerprint,
    _freeze,
    _install,
    _make_venv,
    _normalize_locked_freeze,
    lock_hash,
    prepare_project,
    run_subprocess,
)


class _FakeRunner:
    """Records calls and returns canned outputs keyed by argv[:2]."""

    def __init__(
        self, outs: dict[tuple[str, ...], RunOut] | None = None, head_sha: str = "abc123"
    ) -> None:
        self.calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []
        self._outs = outs or {}
        self._head_sha = head_sha

    def __call__(self, argv: list[str], cwd: Path | None, env: dict[str, str] | None) -> RunOut:
        self.calls.append((argv, cwd, env))
        if "rev-parse" in argv:
            return RunOut(0, self._head_sha, "")
        return self._outs.get(tuple(argv[:2]), RunOut(0, "", ""))


def test_make_venv_builds_uv_command_and_returns_python(tmp_path: Path) -> None:
    run = _FakeRunner()
    venv = tmp_path / "venv"
    py = _make_venv("3.12", venv, run)
    argv = run.calls[0][0]
    assert argv[:2] == ["uv", "venv"]
    assert "--python" in argv and "3.12" in argv
    assert py.endswith("/venv/bin/python")


def test_install_activates_venv_for_each_recipe_command(tmp_path: Path) -> None:
    run = _FakeRunner()
    venv = tmp_path / "venv"
    repo = tmp_path / "repo"
    _install(("uv pip install .", "uv pip install ./extra"), repo, venv, run)
    assert len(run.calls) == 2
    first_argv, first_cwd, first_env = run.calls[0]
    assert first_argv == ["uv", "pip", "install", "."]  # shlex-split recipe
    assert first_cwd == repo
    assert first_env is not None
    assert first_env["VIRTUAL_ENV"] == str(venv)
    assert first_env["PATH"].startswith(f"{venv / 'bin'}:")
    assert "UV_CONSTRAINT" not in first_env  # no lock -> unconstrained


def test_install_pins_uv_constraint_when_locked(tmp_path: Path) -> None:
    run = _FakeRunner()
    venv = tmp_path / "venv"
    repo = tmp_path / "repo"
    lock = tmp_path / "lock.txt"
    lock.write_text("idna==3.0\n")
    _install(("uv pip install .",), repo, venv, run, constraints=lock)
    _argv, _cwd, env = run.calls[0]
    assert env is not None
    assert env["UV_CONSTRAINT"] == str(lock)  # reproducible deps (spec §83/§85)


def test_freeze_returns_sorted_lines(tmp_path: Path) -> None:
    out = RunOut(0, "idna==3.0\nhttpcore==1.0.0\n", "")
    run = _FakeRunner({("uv", "pip"): out})
    frozen = _freeze("/v/bin/python", run)
    assert frozen == ("httpcore==1.0.0", "idna==3.0")


def test_lock_hash_is_order_independent_and_stable() -> None:
    a = lock_hash(("idna==3.0", "httpcore==1.0.0"))
    b = lock_hash(("httpcore==1.0.0", "idna==3.0"))
    assert a == b  # sorted before hashing
    assert a == lock_hash(("idna==3.0", "httpcore==1.0.0"))  # deterministic
    assert a != lock_hash(("idna==3.1", "httpcore==1.0.0"))  # content-sensitive


def test_install_raises_prepare_error_on_nonzero(tmp_path: Path) -> None:
    run = _FakeRunner({("uv", "pip"): RunOut(1, "", "boom")})
    with pytest.raises(PrepareError, match="boom"):
        _install(("uv pip install .",), tmp_path / "repo", tmp_path / "venv", run)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_clone_checks_out_pinned_sha_from_local_repo(tmp_path: Path) -> None:
    # Build a local upstream repo (offline), tag it, then clone via file://.
    upstream = tmp_path / "upstream"
    upstream.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(upstream), *args], check=True, capture_output=True)

    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (upstream / "httpx").mkdir()
    (upstream / "httpx" / "__init__.py").write_text("VERSION = '1'\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    git("tag", "v1")
    sha = subprocess.run(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    repo = tmp_path / "repo"
    _clone(f"file://{upstream}", "v1", sha, repo, run_subprocess)
    assert (repo / "httpx" / "__init__.py").read_text() == "VERSION = '1'\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_clone_rejects_sha_mismatch(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    subprocess.run(["git", "init", "-q", str(upstream)], check=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(upstream), "config", key, value], check=True)
    (upstream / "f.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(upstream), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(upstream), "commit", "-q", "-m", "c"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(upstream), "tag", "v1"], check=True)
    with pytest.raises(PrepareError, match="SHA mismatch"):
        _clone(f"file://{upstream}", "v1", "0" * 40, tmp_path / "repo", run_subprocess)


def _httpx_entry(**over: object) -> CorpusProject:
    base: dict[str, object] = {
        "name": "demo",
        "repo_url": "file:///does/not/matter",
        "sha": "abc123",
        "tag": "v1",
        "size_bucket": SizeBucket.SMALL,
        "python_version": "3.12",
        "src_roots": ("pkg",),
        "install": ("uv pip install .",),
    }
    return CorpusProject.model_validate({**base, **over})


class _CloningRunner(_FakeRunner):
    """Fake runner that materializes clone and venv artifacts."""

    def __call__(self, argv: list[str], cwd: Path | None, env: dict[str, str] | None) -> RunOut:
        out = super().__call__(argv, cwd, env)
        if "checkout" in argv and "-C" in argv:
            repo = Path(argv[argv.index("-C") + 1])
            pkg = repo / "pkg"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "a.py").write_text("x = 1\n")
            (pkg / "b.py").write_text("y = 2\n")
            (pkg / "tests").mkdir(exist_ok=True)
            (pkg / "tests" / "t.py").write_text("assert True\n")  # excluded
        if argv[:2] == ["uv", "venv"]:
            venv_bin = Path(argv[-1]) / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            (venv_bin / "python").write_text("#!/bin/sh\n")
        return out


def test_prepare_project_assembles_prepared_with_canonical_count(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    run = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\nhttpcore==1.0.0\n", "")})
    prepared = prepare_project(_httpx_entry(), cache, run=run)

    assert prepared.name == "demo"
    assert prepared.python_platform == "linux"
    assert prepared.canonical_files == 2  # tests/t.py excluded
    assert prepared.canonical_loc == 2
    assert prepared.frozen == ("httpcore==1.0.0", "idna==3.0")
    assert prepared.venv_python.endswith("/venv/bin/python")
    assert prepared.src_roots[0].endswith("/repo/pkg")
    assert Path(prepared.checkout).is_absolute()
    assert prepared.lock_hash
    assert prepared.fingerprint


def test_prepare_project_is_idempotent_via_sidecar(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    run1 = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    first = prepare_project(_httpx_entry(), cache, run=run1)
    assert run1.calls

    run2 = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    second = prepare_project(_httpx_entry(), cache, run=run2)
    assert run2.calls == []
    assert second == first


def test_prepare_project_rebuilds_when_recipe_changes(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    run1 = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    first = prepare_project(_httpx_entry(), cache, run=run1)

    run2 = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    second = prepare_project(_httpx_entry(install=("uv pip install . --no-deps",)), cache, run=run2)
    assert run2.calls
    assert second.fingerprint != first.fingerprint


def test_prepare_project_cleans_partial_dest_on_failure(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    bad = _CloningRunner({("uv", "pip"): RunOut(1, "", "install boom")})
    with pytest.raises(PrepareError, match="boom"):
        prepare_project(_httpx_entry(), cache, run=bad)
    assert not (cache / "demo@abc123").exists()

    good = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    prepared = prepare_project(_httpx_entry(), cache, run=good)
    assert prepared.canonical_files == 2


def test_prepare_project_rejects_missing_src_root(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    run = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    with pytest.raises(PrepareError, match="src_root"):
        prepare_project(_httpx_entry(src_roots=("ghost",)), cache, run=run)
    assert not (cache / "demo@abc123").exists()


def test_prepare_project_verifies_constraints_lock(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    lock = tmp_path / "lock.txt"
    lock.write_text("idna==9.9.9\n")
    entry = _httpx_entry(constraints=str(lock))
    run = _CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    with pytest.raises(PrepareError, match="lock drift"):
        prepare_project(entry, cache, run=run)


def test_fingerprint_changes_with_scope_fields() -> None:
    # Editing src_roots or exclude_globs for an existing <name>@<sha> entry must
    # invalidate the cache, or preflight would reuse a stale canonical denominator.
    base = _fingerprint(_httpx_entry(), None)
    assert _fingerprint(_httpx_entry(src_roots=("other",)), None) != base
    assert _fingerprint(_httpx_entry(exclude_globs=("**/docs/**",)), None) != base


def test_normalize_locked_freeze_matches_pep503_normalized_name() -> None:
    # Corpus name uses an underscore; freeze/lock spell it with a hyphen. PEP 503
    # normalization must still match and replace the local `file:` ref with the
    # pinned version (else the file: line survives -> drift check always fails).
    entry = _httpx_entry(name="typing_extensions")
    constraints = "idna==3.0\ntyping-extensions==4.9.0\n"
    frozen = ("idna==3.0", "typing-extensions @ file:///tmp/te")
    assert _normalize_locked_freeze(entry, frozen, constraints) == (
        "idna==3.0",
        "typing-extensions==4.9.0",
    )


def test_run_subprocess_missing_binary_returns_nonzero_not_raises() -> None:
    # A missing git/uv must surface as a nonzero RunOut (-> PrepareError -> cache
    # cleanup), never an unhandled OSError traceback.
    out = run_subprocess(["typebench-nonexistent-binary-xyz"], None, None)
    assert out.returncode != 0
    assert "typebench-nonexistent-binary-xyz" in out.stderr


def test_prepare_project_rebuilds_on_corrupt_sidecar(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    first = prepare_project(
        _httpx_entry(), cache, run=_CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    )
    # Simulate an interrupted sidecar write; a re-prepare must rebuild cleanly,
    # not escape with a ValidationError.
    (cache / "demo@abc123" / _SIDECAR).write_text("{ this is not valid json")
    second = prepare_project(
        _httpx_entry(), cache, run=_CloningRunner({("uv", "pip"): RunOut(0, "idna==3.0\n", "")})
    )
    assert second.canonical_files == first.canonical_files == 2
