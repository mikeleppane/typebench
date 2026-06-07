import typebench


def test_package_has_version() -> None:
    assert isinstance(typebench.__version__, str)
    assert typebench.__version__
