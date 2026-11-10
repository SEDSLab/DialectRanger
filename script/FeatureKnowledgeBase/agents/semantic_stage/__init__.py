"""Identity-group semantic stage for the cold-start pipeline.

This package is intentionally separate from the historical semantic extractor.
It consumes the authoritative Identity Quality artifacts and stops at the
evidence-grounded Semantic Feature boundary.
"""

from agents.semantic_stage.pipeline import finalize_semantic_stage, run_semantic_stage

__all__ = ["run_semantic_stage", "finalize_semantic_stage"]
