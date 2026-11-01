"""Shared helpers and the generator configuration of the CHIA loop.

Nothing in this module imports ``ray`` or ``chia``; the nodes, the offline
replay, and the loop driver share it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def unwrap(value: Any) -> Any:
    """Return the payload of a value that arrived over a chained ``ObjectRef``.

    Passing one node's ref straight into the next is how CHIA expresses an edge,
    but with the profiler enabled the upstream node returns a
    ``chia.trace.profiler._ProfiledResult`` wrapper carrying worker metadata.
    ``get()`` strips it on the driver; a downstream node receives it intact
    because Ray resolves the ref without CHIA in the path. Nodes therefore call
    this on any argument that may come from another node.
    """
    if type(value).__name__ == "_ProfiledResult" and hasattr(value, "value"):
        return value.value
    return value


def _asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


def _default_svapshot_root() -> str:
    import os

    for key in ("SVAPSHOT_CHECKOUT", "SVAPSHOT_ROOT"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "external", "svapshot")


@dataclass
class SvapshotConfig:
    """Everything needed to invoke the SVApshot generator that is not part of the DUT."""

    svapshot_root: str = field(default_factory=_default_svapshot_root)
    interpreter: str = "python3"
    llm_model: str = "gemini-2.5-pro"
    execution: str = "validation"
    verbosity: str = "low"
    formal_tool: str = "vcformal"
    assertion_source: str = "rtl"
    timeout_s: int = 3600
