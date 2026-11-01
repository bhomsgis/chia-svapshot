"""CHIA loop that rewrites RTL under a frozen SVApshot contract and measures the result.

The loop stages a module, proves a snapshot of assertions with VC Formal,
asks an LLM for an optimised, expanded, or trimmed rewrite, re-proves the
retained properties, measures Sky130 area with Yosys, and checks equivalence
with Yosys EQY. ``chia_svapshot.llm`` selects the model provider.
"""

from .state import SvapshotConfig
from .ppa_state import (
    ElabResult,
    FrozenSnapshot,
    PpaDesign,
    PpaMeasure,
    PpaScore,
    ReproveResult,
    RetainResult,
    RewriteResult,
)

__all__ = [
    "SvapshotConfig",
    "PpaDesign",
    "ElabResult",
    "PpaMeasure",
    "FrozenSnapshot",
    "RewriteResult",
    "RetainResult",
    "ReproveResult",
    "PpaScore",
]
