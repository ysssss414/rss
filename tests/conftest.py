import pytest


@pytest.fixture(autouse=True)
def isolated_legacy_test_paths(request, tmp_path, monkeypatch):
    if hasattr(request.module, "TEST_TEMP_ROOT"):
        monkeypatch.setattr(request.module, "TEST_TEMP_ROOT", tmp_path)
