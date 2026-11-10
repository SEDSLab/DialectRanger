"""Generic type-backed Feature Target Admission primitives."""

from .evidence import build_typed_feature_evidence
from .decision import decide_type

__all__ = ["build_typed_feature_evidence", "decide_type"]
