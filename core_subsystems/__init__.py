"""SuperSkinPro core_subsystems — decoupled implementation services.

This layer holds implementation logic that does not require a live
``bpy.context`` or Blender operator lifecycle to function. Modules here may
still accept Blender objects (e.g., ``bpy.types.Mesh``) as parameters and
reference ``bpy.types`` for type annotations, but they must never call
``bpy.context``, register Blender handlers, or invoke ``bpy.ops``.

Import Invariants
-----------------
1. ``core/`` may import from ``core_subsystems/`` directly (no facade needed).
2. Intra-subsystem imports are permitted only in strict one-way dependency
   chains between encapsulated packages. Circular imports are forbidden.
3. ``features/`` must NOT import from ``core_subsystems/`` directly; all access
   goes through ``CoreFacade``.

Encapsulated Subsystem Packages
--------------------------------
Each sub-package exposes exactly ONE public class through its ``__init__.py``.
Callers interact exclusively with that class; private submodules are
implementation details and must not be imported from outside the package.

    rust_weight_engine/        -> RustWeightEngine
    layer_compositor/          -> LayerCompositor
    topology_cache_manager/    -> TopologyCacheManager
    context_selection_service/ -> ContextSelectionService
    license_gateway/           -> LicenseGateway
    debug_logging/             -> DebugLogService
    profiler/                  -> ProfilerService
    dev_records/               -> DevRecordsService
    support_report/            -> SupportReportService

Legacy packages below are retained pending a separate migration pass.
"""

from importlib import reload

# ── Encapsulated subsystem packages ───────────────────────────────────────
# `dev_records`, `profiler`, and `rust_weight_engine` import first -- all
# three are leaf dependencies of packages reloaded after them (see the
# reload-order comment below for why this ordering is load-bearing for
# `profiler`/`rust_weight_engine`; `dev_records` itself is stateless, so its
# own position is not load-bearing, only that it exists before `profiler`,
# which now imports it).
from . import dev_records
from . import profiler
from . import rust_weight_engine
from . import layer_compositor
from . import topology_cache_manager
from . import context_selection_service
from . import license_gateway
from . import debug_logging
from . import support_report     # depends on dev_records + debug_logging -- must sit after both in _encapsulated below

# ── Legacy packages (retained pending migration) ───────────────────────────
from . import preferences

# Reload order: encapsulated packages first (rust_weight_engine is a
# dependency of layer_compositor, topology_cache_manager, and license_gateway;
# profiler is a dependency of layer_compositor's codec.py), then legacy
# packages. `preferences` (legacy) now also depends on `debug_logging`
# (encapsulated) -- it imports SSPrefDebug to nest into SSPrefRoot.debug, so
# debug_logging must reload first. This is the first
# _legacy_packages -> _encapsulated dependency.
#
# `profiler` MUST reload before `layer_compositor` here, not just within its
# own package. Even though both packages individually apply the
# reload-before-bind fix in their own __init__.py, `layer_compositor`'s
# internal reload cascade (reload(_codec) inside layer_compositor/__init__.py)
# re-executes codec.py's `from ..profiler import ProfilerService` line every
# time THIS loop reloads `layer_compositor` -- which rebinds codec.py's
# ProfilerService reference to whatever `core_subsystems.profiler.
# ProfilerService` CURRENTLY is at that moment in the loop. If `profiler`
# hasn't had its own turn yet (i.e. it sits later in this tuple), codec.py's
# reference gets built from the PREVIOUS reload pass's class object, while
# every other consumer (CoreFacade, core/ui_controller/pipeline.py) picks up
# the FINAL one once `profiler` does reload later in this same pass --
# leaving codec.py's ProfilerService.record() calls writing into a
# disconnected class object nobody ever reads from again. Verified by
# simulation outside Blender (reorder reproduces/fixes the identity
# mismatch deterministically); this is a real, provable dependency-ordering
# requirement, not a cosmetic preference -- do not reorder this tuple
# without re-checking every current core_subsystems consumer of `profiler`.
_encapsulated = (
    dev_records,
    profiler,
    rust_weight_engine,
    layer_compositor,
    topology_cache_manager,
    context_selection_service,
    license_gateway,
    debug_logging,
    support_report,
)
_legacy_packages = (
    preferences,
)

for mod in (*_encapsulated, *_legacy_packages):
    try:
        reload(mod)
    except Exception:
        pass
