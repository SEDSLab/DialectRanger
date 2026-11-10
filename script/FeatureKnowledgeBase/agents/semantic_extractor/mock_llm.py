from __future__ import annotations


class MockLLMClient:
    def __init__(self, config: dict):
        self.config = config
        self.model = config.get("model", "mock-semantic-extractor")

    @property
    def provider_name(self) -> str:
        return "mock"

    def call(self, prompt: str, feature_id: str) -> dict:
        return {
            "feature_id": feature_id,
            "description_claims": [],
            "constraints": [],
            "example_roles": [],
            "semantic_notes": [],
            "warnings": ["mock_provider_no_semantic_enrichment"],
            "_raw_response": '{"feature_id":"' + feature_id + '","description_claims":[],"constraints":[],"example_roles":[],"semantic_notes":[],"warnings":["mock_provider_no_semantic_enrichment"]}',
            "_status": "ok",
        }
