"""KHU model-informed deployment policy proof of concept.

The package intentionally has no dependency on FCA planning decisions. It uses
FCA's static scenario database only as a source of resource-context
distributions for a platform-neutral CPU/GPU deployment benchmark.
"""

from .contracts import POLICY_SCHEMA_VERSION

__all__ = ["POLICY_SCHEMA_VERSION"]
