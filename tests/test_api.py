from dataclasses import FrozenInstanceError
from importlib.metadata import version

import pytest

import icadkit


def test_native_backend_identity():
    info = icadkit.build_info()
    assert isinstance(info, icadkit.BuildInfo)
    assert info.version == icadkit.__version__ == version("icadkit")
    assert info.rust_core_version.replace("-dev.", ".dev") == info.version
    assert info.parasolid_core_version == "0.3.1"
    assert any(name.startswith("icad-") for name in info.builtin_profile_ids)
    assert "icad-sch34101-13006-r2" in info.builtin_profile_ids
    assert info.builtin_profile_ids == tuple(sorted(set(info.builtin_profile_ids)))
    with pytest.raises(FrozenInstanceError):
        info.version = "changed"
