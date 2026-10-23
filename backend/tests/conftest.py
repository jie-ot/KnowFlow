"""All regressions default to disposable storage and forbid live model traffic."""

import httpx
import pytest

from app import config
from app.llm import gateway


@pytest.fixture(autouse=True)
def disposable_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test-default.db")
    monkeypatch.setattr(config, "MATERIALS_DIR", tmp_path / "materials")

    def unexpected_model_request(_request):
        pytest.fail("A regression attempted an unmocked model request; live model calls are forbidden")

    # A test may replace this with its own response-producing MockTransport.
    monkeypatch.setattr(gateway, "TRANSPORT", httpx.MockTransport(unexpected_model_request))
