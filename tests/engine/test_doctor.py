import ast
from pathlib import Path

from typebench.engine.doctor import Tier, run_doctor


def _present_which(binary: str) -> str | None:
    return f"/usr/bin/{binary}"


def _probe(argv: list[str]) -> str | None:
    return f"{argv[0]}-1.2.3"


def test_run_doctor_all_present_and_healthy() -> None:
    checks = run_doctor(which=_present_which, probe=_probe, resource_capable=lambda: True)
    by = {c.name: c for c in checks}
    # Every documented tool (including the capability row) has an entry.
    assert {
        "uv",
        "git",
        "mypy",
        "pyright",
        "pyrefly",
        "ty",
        "node",
        "hyperfine",
        "tokei",
        "taskset",
        "systemd-run",
    } <= set(by)
    assert all(c.present and c.healthy for c in checks)
    assert by["uv"].tier is Tier.REQUIRED
    assert by["git"].tier is Tier.REQUIRED
    assert by["mypy"].tier is Tier.PER_TOOL
    assert by["node"].tier is Tier.PER_TOOL
    assert by["hyperfine"].tier is Tier.OPTIONAL
    # pyright row folds in the node runtime it will use.
    assert "node" in (by["pyright"].version or "")


def test_run_doctor_pyright_unhealthy_when_node_missing() -> None:
    # pyright binary present but node absent: present, NOT healthy, never renders ok.
    def which(binary: str) -> str | None:
        return None if binary == "node" else f"/usr/bin/{binary}"

    by = {c.name: c for c in run_doctor(which=which, probe=_probe, resource_capable=lambda: True)}
    assert by["pyright"].present is True
    assert by["pyright"].healthy is False
    assert "MISSING" in (by["pyright"].version or "")
    assert by["node"].present is False


def test_run_doctor_missing_required() -> None:
    def which(binary: str) -> str | None:
        return None if binary == "uv" else f"/usr/bin/{binary}"

    by = {c.name: c for c in run_doctor(which=which, probe=_probe, resource_capable=lambda: True)}
    assert by["uv"].present is False
    assert by["uv"].healthy is False
    assert by["uv"].version is None
    assert by["git"].present is True


def test_run_doctor_present_but_failed_version_probe_is_unhealthy() -> None:
    # A REQUIRED tool on PATH whose `--version` fails (broken/incompatible binary)
    # must NOT read healthy — otherwise `doctor --check` false-passes and the table
    # prints `ok` with no version.
    def probe(argv: list[str]) -> str | None:
        return None if argv[0] == "uv" else f"{argv[0]}-1.2.3"

    by = {
        c.name: c
        for c in run_doctor(which=_present_which, probe=probe, resource_capable=lambda: True)
    }
    assert by["uv"].present is True
    assert by["uv"].version is None
    assert by["uv"].healthy is False


def test_run_doctor_capability_row_uses_injected_resource_capable() -> None:
    # The systemd-run/cgroup row reflects resource_capable(), never a bare which().
    off = {
        c.name: c
        for c in run_doctor(which=_present_which, probe=_probe, resource_capable=lambda: False)
    }
    on = {
        c.name: c
        for c in run_doctor(which=_present_which, probe=_probe, resource_capable=lambda: True)
    }
    assert off["systemd-run"].present is False and off["systemd-run"].healthy is False
    assert on["systemd-run"].present is True and on["systemd-run"].healthy is True


def test_doctor_module_does_not_import_adapters() -> None:
    # Layering guard via AST: a substring check false-positives on the module
    # docstring (which mentions "adapters"). Assert no real import of typebench.adapters.
    tree = ast.parse(Path("src/typebench/engine/doctor.py").read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.startswith("typebench.adapters")]
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("typebench.adapters")
        ):
            offenders.append(node.module)
    assert not offenders, f"engine/doctor.py must not import adapters: {offenders}"
