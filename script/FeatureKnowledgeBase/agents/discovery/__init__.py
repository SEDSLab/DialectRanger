"""Generic discovery stages."""

from .generic_stage import GenericDiscovery, run_generic_discovery, run_scoped_discovery
from .http_transport import (
    GenericFetchProfile,
    PROFILE_ANY_REPRESENTATION,
    PROFILE_BROAD_DOCUMENT,
    PROFILE_DEFAULT,
    fetch_with_negotiation,
)
from .scope_resolver import ScopeResolver, resolve_feature_scope

__all__ = [
    "GenericDiscovery",
    "GenericFetchProfile",
    "PROFILE_ANY_REPRESENTATION",
    "PROFILE_BROAD_DOCUMENT",
    "PROFILE_DEFAULT",
    "run_generic_discovery",
    "run_scoped_discovery",
    "fetch_with_negotiation",
    "ScopeResolver",
    "resolve_feature_scope",
]
