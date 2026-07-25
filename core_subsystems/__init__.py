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

Legacy packages below are retained pending a separate migration pass.
"""

from importlib import reload

# ── Encapsulated subsystem packages ───────────────────────────────────────
from . import rust_weight_engine
from . import layer_compositor
from . import topology_cache_manager
from . import context_selection_service
from . import license_gateway
from . import debug_logging

# ── Legacy packages (retained pending migration) ───────────────────────────
from . import preferences

# Reload order: encapsulated packages first (rust_weight_engine is a
# dependency of layer_compositor, topology_cache_manager, and license_gateway),
# then legacy packages. `preferences` (legacy) now also depends on
# `debug_logging` (encapsulated) -- it imports SSPrefDebug to nest into
# SSPrefRoot.debug, so debug_logging must reload first. This is the first
# _legacy_packages -> _encapsulated dependency.
_encapsulated = (
    rust_weight_engine,
    layer_compositor,
    topology_cache_manager,
    context_selection_service,
    license_gateway,
    debug_logging,
)
_legacy_packages = (
    preferences,
)

for mod in (*_encapsulated, *_legacy_packages):
    try:
        reload(mod)
    except Exception:
        pass
