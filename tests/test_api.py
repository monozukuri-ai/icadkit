from dataclasses import FrozenInstanceError
from importlib.metadata import version

import pytest

import icadkit


def test_native_backend_identity():
    info = icadkit.build_info()
    assert isinstance(info, icadkit.BuildInfo)
    assert info.version == icadkit.__version__ == version("icadkit")
    assert info.rust_core_version.replace("-dev.", ".dev") == info.version
    assert info.parasolid_core_version == "0.3.4"
    assert any(name.startswith("icad-") for name in info.builtin_profile_ids)
    assert "icad-sch34101-13006-r2" in info.builtin_profile_ids
    assert {
        "icad-1500137-15003-13006-r1",
        "icad-1500245-15003-13006-r1",
        "icad-1700223-16100-13006-r1",
        "icad-1700256-16100-13006-r1",
        "icad-1901315-19008-13006-r1",
        "icad-2100293-20000-13006-r1",
        "icad-2100311-20000-13006-r1",
        "icad-2401000-20000-r1",
        "icad-2401260-20000-13006-r1",
        "icad-2800000-28002-r1",
        "icad-2800188-28002-13006-r1",
        "icad-2901000-28101-r1",
        "icad-2901199-28101-13006-r1",
        "icad-3200000-32001-r1",
        "icad-3200152-32001-13006-r1",
        "icad-3200252-32001-13006-r1",
        "icad-3301000-33103-r1",
        "icad-3301231-33103-13006-r1",
    } <= set(info.builtin_profile_ids)
    assert info.builtin_profile_ids == tuple(sorted(set(info.builtin_profile_ids)))
    with pytest.raises(FrozenInstanceError):
        info.version = "changed"
