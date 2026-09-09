from alis.build import lro


def test_version_is_exposed() -> None:
    assert lro.__version__
