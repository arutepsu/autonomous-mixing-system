"""Local filesystem location interpretation: read-only resolution of artifact locations against an explicit root."""

from .locations import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)

__all__ = [
    "LocationResolutionError",
    "LocationResolutionFailureCode",
    "ResolvedArtifactLocation",
    "resolve_artifact_location",
]
