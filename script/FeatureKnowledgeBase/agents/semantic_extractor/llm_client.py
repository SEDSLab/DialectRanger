from __future__ import annotations

import json
import os

from agents.runtime_config import load_project_env


class LLMClientFactory:
    @staticmethod
    def create(config: dict) -> object:
        provider = config.get("provider", "mock")

        if provider == "mock":
            from agents.semantic_extractor.mock_llm import MockLLMClient
            return MockLLMClient(config)

        if provider == "openai_compatible":
            # Keep direct stage/library callers on the same project .env
            # contract as the CLI entry points.  Only presence is inspected;
            # secret values are never logged or persisted here.
            if config.get("load_project_env", True):
                load_project_env()
            return OpenAICompatibleClient(config)

        raise ValueError(f"Unsupported semantic API provider: {provider}")


class OpenAICompatibleClient:
    def __init__(self, config: dict):
        # Direct construction is also supported by a few semantic adapters;
        # make the readiness check deterministic for those callers too.
        if config.get("load_project_env", True):
            load_project_env()
        self.config = config
        self.model = config.get("model", "")
        self.temperature = config.get("temperature", 0.0)
        self.max_retries = config.get("max_retries", 2)
        self.timeout = config.get("timeout", 60)
        self.api_key_env = config.get("api_key_env", "FEATURE_CRAWLER_LLM_API_KEY")
        self.base_url_env = config.get("base_url_env", "FEATURE_CRAWLER_LLM_BASE_URL")

    @property
    def provider_name(self) -> str:
        return "openai_compatible"

    def _get_credentials(self) -> tuple[str | None, str | None, str | None]:
        api_key = os.environ.get(self.api_key_env)
        base_url = os.environ.get(self.base_url_env)
        model = self.model or os.environ.get("FEATURE_CRAWLER_LLM_MODEL", "")
        return api_key, base_url, model

    def _check_ready(self) -> str | None:
        api_key, base_url, model = self._get_credentials()
        if not api_key:
            return f"Missing API key: env var {self.api_key_env} not set"
        if not base_url:
            return f"Missing base URL: env var {self.base_url_env} not set"
        if not model:
            return "Missing model name: set semantic_extractor.model in config"
        return None

    @staticmethod
    def _trust_environment() -> bool:
        """Return whether urllib may inherit process proxy settings.

        Discovery already exposes this project-wide switch for its requests
        session.  The semantic client uses urllib, so it must apply the same
        contract explicitly instead of silently inheriting a system proxy.
        """

        value = os.environ.get("FEATURE_CRAWLER_HTTP_TRUST_ENV", "1")
        return value.strip().lower() not in {"0", "false", "no", "off"}

    def _opener(self):
        import urllib.request

        if self._trust_environment():
            return urllib.request.build_opener()
        # An empty ProxyHandler is urllib's equivalent of requests.Session
        # trust_env = False: it prevents HTTP(S)_PROXY and ALL_PROXY from
        # being consulted for this provider call.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, prompt: str, feature_id: str) -> dict:
        not_ready = self._check_ready()
        if not_ready:
            return {
                "feature_id": feature_id,
                "_raw_response": "",
                "_status": "skipped",
                "_error": not_ready,
            }

        api_key, base_url, model = self._get_credentials()

        if "system_prompt" in self.config:
            system_prompt = self.config["system_prompt"]
        else:
            from agents.semantic_extractor.prompt_builder import SYSTEM_PROMPT
            system_prompt = SYSTEM_PROMPT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                import urllib.request
                import urllib.error

                body = json.dumps({
                    "model": model,
                    "messages": messages,
                    "temperature": self.temperature,
                }).encode("utf-8")

                url = base_url.rstrip("/") + "/chat/completions"
                req = urllib.request.Request(url, data=body)
                req.add_header("Content-Type", "application/json")
                req.add_header("Authorization", f"Bearer {api_key}")

                with self._opener().open(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    data = json.loads(raw)
                    content = data["choices"][0]["message"]["content"]

                return {
                    "feature_id": feature_id,
                    "_raw_response": content,
                    "_status": "ok",
                    "_retry_count": attempt,
                    # Token accounting is safe to persist; never persist or
                    # print request headers, API keys, or the raw transport.
                    "usage": data.get("usage"),
                }

            except Exception as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    continue

        return {
            "feature_id": feature_id,
            "_raw_response": "",
            "_status": "error",
            "_error": last_error,
            "_retry_count": self.max_retries,
        }
