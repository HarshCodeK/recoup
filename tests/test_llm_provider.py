"""Tests for OpenAICompatibleProvider — kilo / OpenAI / etc.

Network calls are mocked via unittest.mock; the real provider is exercised
end-to-end in scripts/live_llm_smoke.py (skipped if no API key).
"""
import os
import pytest
from unittest.mock import patch, MagicMock


class _FakeResp:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._json


def _make_provider(monkeypatch, **env_overrides):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "poolside/laguna-s-2.1:free")
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    # Import lazily so env overrides land first
    from src.diagnosis import OpenAICompatibleProvider
    return OpenAICompatibleProvider()


def test_provider_requires_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    from src.diagnosis import OpenAICompatibleProvider
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        OpenAICompatibleProvider()


def test_provider_requires_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://x.com/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "m")
    from src.diagnosis import OpenAICompatibleProvider
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAICompatibleProvider()


def test_provider_requires_model(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://x.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    from src.diagnosis import OpenAICompatibleProvider
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        OpenAICompatibleProvider()


def test_provider_uses_deterministic_temperature(monkeypatch):
    """Diagnosis must be reproducible — temperature forced to 0.0."""
    p = _make_provider(monkeypatch)
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs.get("json", {}))
        return _FakeResp({"choices": [{"message": {"content": "{}"}}]})

    with patch("httpx.post", side_effect=fake_post):
        p.complete("system", "user")
    assert captured["temperature"] == 0.0
    assert captured["model"] == "poolside/laguna-s-2.1:free"
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"


def test_provider_returns_message_content(monkeypatch):
    p = _make_provider(monkeypatch)
    body = {"choices": [{"message": {"content": '{"diagnosis_class":"REFUSED","confidence":0.3,"reasoning":"x"}'}}]}
    with patch("httpx.post", return_value=_FakeResp(body)):
        out = p.complete("sys", "usr")
    assert "REFUSED" in out


def test_provider_raises_on_unexpected_shape(monkeypatch):
    p = _make_provider(monkeypatch)
    body = {"choices": []}  # missing content
    with patch("httpx.post", return_value=_FakeResp(body)):
        with pytest.raises(RuntimeError, match="unexpected chat-completions response"):
            p.complete("sys", "usr")


def test_provider_raises_on_http_error(monkeypatch):
    p = _make_provider(monkeypatch)
    with patch("httpx.post", return_value=_FakeResp({}, status_code=500)):
        with pytest.raises(RuntimeError, match="http 500"):
            p.complete("sys", "usr")


def test_default_provider_returns_stub_without_env(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from src.diagnosis import get_default_provider, StubProvider
    assert isinstance(get_default_provider(), StubProvider)


def test_default_provider_picks_openai_route_when_env_set(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    from src.diagnosis import get_default_provider, OpenAICompatibleProvider
    assert isinstance(get_default_provider(), OpenAICompatibleProvider)