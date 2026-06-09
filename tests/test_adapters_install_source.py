from typebench.adapters.mypy import MypyAdapter
from typebench.adapters.pyrefly import PyreflyAdapter
from typebench.adapters.pyright import PyrightAdapter
from typebench.adapters.stub import StubAdapter
from typebench.adapters.ty import TyAdapter


def test_each_adapter_declares_an_install_source() -> None:
    assert MypyAdapter().install_source == "PyPI wheel (mypyc-compiled)"
    assert PyrightAdapter().install_source == "npm + Node"
    assert PyreflyAdapter().install_source == "PyPI wheel (Rust)"
    assert TyAdapter().install_source == "PyPI wheel (Rust)"
    assert StubAdapter().install_source == "builtin"
