"""profiler -- encapsulated in-memory performance-timing subsystem.

Public surface: only ``ProfilerService`` is exported from this package.
``profile_section`` (the context manager) is intentionally not part of this
public surface -- see core/facade/__init__.py's ``profile_section()``
classmethod, the one sanctioned place that reaches into the private
``profiler_service`` submodule directly (legal because core/ is the layer
licensed to reach core_subsystems/ internals).

IMPORTANT reload-ordering note (do not "simplify" this back to the usual
import-then-reload template used elsewhere in core_subsystems/): this
package's two classes (ProfilerService and profile_section) live in the SAME
module and profile_section.__enter__ references ProfilerService by bare name
-- i.e. it resolves against profiler_service.py's OWN module globals at call
time, not against whatever this __init__.py happens to have bound locally.
If the export below were bound BEFORE reload() runs (the pattern every other
single-class subsystem in this codebase uses safely), reload() would rebind
profiler_service.py's internal ProfilerService to a NEW class object while
this package's exported name kept pointing at the pre-reload one --
CoreFacade.set_profiler_enabled()/get_profiler_stats() (which import via this
package) would then silently operate on a different, disconnected
ProfilerService than the one profile_section actually checks/writes,
manifesting as "Enabled is checked but nothing ever gets recorded." Reload
MUST happen before the export binding here so both consumers end up with the
identical, final class object.
"""

from importlib import reload

from . import profiler_service as _ps

for _mod in (_ps,):
    try:
        reload(_mod)
    except Exception:
        pass

__all__ = ["ProfilerService"]

from .profiler_service import ProfilerService
