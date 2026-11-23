"""Model intelligence package for Local AI OS.

Current state (Phase 13 entry):
- ``SharedIntelligence`` is the universal model-intelligence facade.
- ``model_provider`` provides the existing provider ABC and placeholder providers.

Not present on this branch (documented, not fabricated):
- ``matlab_os.intelligence.resource_scheduler``
- ``matlab_os.intelligence.model_registry``

Those submodules existed in earlier development snapshots and are referenced by
some Phase 9 code through lazy/exception-guarded imports, but they are not part
of the current working tree on this branch.  See docs/roadmap/ for the Phase 13
plan and the blocker investigation evidence under staging/.
"""

from .engine import SharedIntelligence
from . import model_provider

__all__ = ["SharedIntelligence", "model_provider"]

__all__ = ["SharedIntelligence", "model_provider"]
