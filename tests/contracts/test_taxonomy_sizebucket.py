from typebench.contracts.taxonomy import SizeBucket


def test_sizebucket_members() -> None:
    assert [b.value for b in SizeBucket] == ["small", "medium", "large", "giant"]


def test_sizebucket_is_str_enum() -> None:
    assert SizeBucket.SMALL == "small"
