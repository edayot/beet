import pytest

from mecha.check import select_mcdoc_type

MCDOC_TYPES = {"26.3": "latest", "1.21": "old", "1.21.5": "middle"}


def test_select_mcdoc_type_str():
    assert select_mcdoc_type("always", "1.20") == "always"


@pytest.mark.parametrize(
    "version, expected",
    [
        ("1.20", "old"),
        ("1.21", "old"),
        ("1.21.1", "middle"),
        ("1.21.5", "middle"),
        ("1.21.6", "latest"),
        ("26.3", "latest"),
        ((1, 21, 2), "middle"),
    ],
)
def test_select_mcdoc_type(version: str | tuple[int, ...], expected: str):
    assert select_mcdoc_type(MCDOC_TYPES, version) == expected


