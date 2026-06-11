import hashlib
from pathlib import Path

from typebench.contracts.identity import CheckerSpec, Source
from typebench.suite.services import PathResolver


def test_path_resolver_stamps_binary_and_sha256_lock_hash(tmp_path: Path) -> None:
    fake_bin = tmp_path / "stubbin"
    fake_bin.write_bytes(b"\x7fELF fake checker bytes")

    handle = PathResolver(str(fake_bin)).resolve(
        CheckerSpec(tool="stub", label="pr", source=Source.PATH)
    )

    assert handle.binary == str(fake_bin.resolve())
    assert handle.runtime is not None
    assert handle.runtime.install_source == "prebuilt binary (path)"
    assert handle.runtime.version == "stub-1.0"
    assert handle.checker_id == "stub@stub-1.0+pr"
    expected = "sha256:" + hashlib.sha256(b"\x7fELF fake checker bytes").hexdigest()
    assert handle.runtime.lock_hash == expected


def test_path_resolver_falls_back_to_path_version_when_probe_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "notreallymypy"
    fake_bin.write_text("#!/bin/false\n")

    handle = PathResolver(str(fake_bin)).resolve(
        CheckerSpec(tool="mypy", label="pr", source=Source.PATH)
    )

    assert handle.runtime is not None
    assert handle.runtime.version == "path"
    assert handle.checker_id == "mypy@path+pr"


def test_path_resolver_checker_id_has_no_suffix_without_label(tmp_path: Path) -> None:
    fake_bin = tmp_path / "stubbin"
    fake_bin.write_bytes(b"x")
    handle = PathResolver(str(fake_bin)).resolve(CheckerSpec(tool="stub", source=Source.PATH))
    assert handle.checker_id == "stub@stub-1.0"
