from importlib.metadata import distribution
from importlib.resources import files


def test_installed_type_markers_and_runtime_dependencies():
    package = files("icadkit")
    assert package.joinpath("py.typed").is_file()
    assert package.joinpath("_core.pyi").is_file()
    assert distribution("icadkit").requires == [
        "parasolid-kit[occt]==0.2.0 ; extra == 'preview'"
    ]


def test_installed_license_metadata():
    metadata = distribution("icadkit").metadata
    assert metadata["License-Expression"] == (
        "PolyForm-Noncommercial-1.0.0 AND MIT AND Apache-2.0"
    )
    assert set(metadata.get_all("License-File", [])) == {
        "LICENSE",
        "COMMERCIAL-LICENSE.md",
        "THIRD_PARTY_NOTICES.md",
        "LICENSES/PolyForm-Noncommercial-1.0.0.md",
        "LICENSES/Apache-2.0.txt",
        "LICENSES/parasolid-core-MIT.txt",
        "LICENSES/rust-dependencies.txt",
    }
