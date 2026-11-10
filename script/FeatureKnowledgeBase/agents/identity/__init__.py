"""Generic identity candidate and unit production primitives.

Production imports are lazy so the profile bootstrap can reuse the structural
quality primitives without creating a profile -> owner -> identity cycle.
"""
from .scope import (
    build_local_feature_evidence,
    decide_scope,
    infer_owner_semantic_role,
    infer_page_semantic_role,
)

__all__ = [
    "run_identity_owner_path",
    "build_identity_occurrences",
    "decide_scope",
    "build_local_feature_evidence",
    "infer_owner_semantic_role",
    "infer_page_semantic_role",
]


def __getattr__(name: str):
    if name in {"run_identity_owner_path", "build_identity_occurrences"}:
        from .production import build_identity_occurrences, run_identity_owner_path
        return {
            "run_identity_owner_path": run_identity_owner_path,
            "build_identity_occurrences": build_identity_occurrences,
        }[name]
    raise AttributeError(name)
