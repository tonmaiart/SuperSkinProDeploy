"""Interface utilities sub-package — context switches, handlers, caches,
GPU primitives, and shared operator dispatch.

Module-level imports are restricted to gpu_utils (no core/ deps).
utils.py and op_exec.py are imported lazily by interface._ensure_deferred()
to avoid circular imports when core/shaders/shader_utils triggers loading
during core/ initialisation.
"""

from importlib import reload

# gpu_utils is safe — only imports ctypes and gpu (Blender built-ins)
from . import gpu_utils

# icons is also safe — only imports os and bpy.utils.previews, no core/ or
# core_subsystems/ deps -- same "load eagerly, not deferred" treatment as
# gpu_utils above.
from . import icons

for _pkg in (gpu_utils, icons):
    try:
        reload(_pkg)
    except Exception:
        pass

# utils and op_exec depend on core/ or core_subsystems/ — imported lazily
# via interface._ensure_deferred().  The lazy modules are:
#   - utils:   needs core.facade, core_subsystems.*
#   - op_exec: needs interface.registry (already loaded), core.facade


def register():
    """Called by interface.register(). Leaf modules are already loaded.

    Must delegate to utils.py's own register() -- this subpackage-level
    function used to be a no-op `pass`, so utils.py's depsgraph_update_post
    / load_post handlers (sync_layers_to_ui_collection,
    sync_bones_to_ui_collection, _reflatten_if_vg_names_changed) defined a
    register() that looked correct but was never actually invoked from
    anywhere, so those handlers were never appended to bpy.app.handlers at
    all. Traced via the orphan-bone-rename investigation: the Deform Bones
    mirror collection only ever got populated by
    deform_bone_viewer/ui.py's narrow self-heal timer fallback, never by
    this (supposedly primary) sync path.
    """
    icons.register()
    from . import utils as _leaf
    _leaf.register()


def unregister():
    """Called by interface.unregister(). Leaf modules are already loaded."""
    from . import utils as _leaf
    _leaf.unregister()
    icons.unregister()
