from __future__ import annotations

import json
from pathlib import Path
import urllib.request

from agents import runtime_config
from agents.semantic_extractor.llm_client import LLMClientFactory, OpenAICompatibleClient
from agents.semantic_stage.pipeline import _make_llm_client


def _clear_llm_env(monkeypatch):
    for name in runtime_config.LLM_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_project_root_env_is_resolved_without_exposing_values(tmp_path, monkeypatch):
    _clear_llm_env(monkeypatch)
    root = tmp_path
    (root / '.env').write_text('FEATURE_CRAWLER_LLM_API_KEY=test-only\nFEATURE_CRAWLER_LLM_BASE_URL=https://unit.test/v1\nFEATURE_CRAWLER_LLM_MODEL=test-model\n', encoding='utf-8')
    status = runtime_config.llm_config_status(root)

    assert status["env_path"] == str(root / ".env")
    assert status["env_found"] is True
    # The assertion checks only safe presence/readiness booleans.
    assert status["api_key_present"] is True
    assert status["base_url_present"] is True
    assert status["model_present"] is True
    assert status["provider_ready"] is True
    assert "api_key" not in status


def test_cli_status_pipeline_replay_and_llm_client_share_loader(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "FEATURE_CRAWLER_LLM_API_KEY=unit-test-key\n"
        "FEATURE_CRAWLER_LLM_BASE_URL=https://unit.test/v1\n"
        "FEATURE_CRAWLER_LLM_MODEL=unit-model\n",
        encoding="utf-8",
    )
    _clear_llm_env(monkeypatch)
    monkeypatch.setattr(runtime_config, "project_root", lambda: tmp_path)

    # This is the same safe readiness calculation used by the semantic CLI.
    cli_status = runtime_config.llm_config_status()
    assert cli_status["provider_ready"] is True

    # A direct offline replay call loads the same .env before reading model
    # state or constructing the provider client.
    client, pipeline_status, model = _make_llm_client(
        run_llm=True,
        provider="openai_compatible",
        model=None,
        timeout=1,
        max_retries=0,
    )
    assert client is not None
    assert pipeline_status["ready"] is True
    assert model == "unit-model"

    # The actual client readiness check sees the same environment; no network
    # call is made by this test.
    direct_client = LLMClientFactory.create({"provider": "openai_compatible"})
    assert direct_client._check_ready() is None


def test_semantic_client_can_disable_environment_proxy(monkeypatch):
    monkeypatch.setenv("FEATURE_CRAWLER_LLM_API_KEY", "unit-test-key")
    monkeypatch.setenv("FEATURE_CRAWLER_LLM_BASE_URL", "https://unit.test/v1")
    monkeypatch.setenv("FEATURE_CRAWLER_LLM_MODEL", "unit-model")
    monkeypatch.setenv("FEATURE_CRAWLER_HTTP_TRUST_ENV", "0")

    opener_calls = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "{}"}}],
            }).encode("utf-8")

    class _Opener:
        def open(self, _request, timeout):
            assert timeout == 3
            return _Response()

    def fake_build_opener(*handlers):
        opener_calls.append(handlers)
        return _Opener()

    monkeypatch.setattr(urllib.request, "build_opener", fake_build_opener)
    client = OpenAICompatibleClient({"timeout": 3, "max_retries": 0})

    result = client.call("unit prompt", "unit-feature")

    assert result["_status"] == "ok"
    assert len(opener_calls) == 1
    assert len(opener_calls[0]) == 1
    assert isinstance(opener_calls[0][0], urllib.request.ProxyHandler)
    assert opener_calls[0][0].proxies == {}
