"""SuperSkinPro Preferences — core coordinator.

Bridges core_subsystems/preferences (pure I/O + PropertyGroups) with the
Blender lifecycle hooks and temp-VG recovery logic that must live in core
because they depend on core/layer_storage.
"""

import bpy

from ...core_subsystems.preferences import property_groups

# PreferencesService is deliberately NOT imported at module scope here.
# core.preferences (this package) is reloaded as part of core/__init__.py's
# own cascade, which can run before or interleave with core_subsystems'
# cascade reloading core_subsystems.preferences.preferences_service depending
# on which package's chain of `from X import Y` statements triggers the
# other's first import — a module-level binding here can end up pointing at
# a PreferencesService class that's a different object identity than the one
# still-executing code elsewhere resolves to, which silently breaks the
# `_loading` reentrancy guard (each class has its own independent `_loading`
# attribute) and produces exactly the kind of torn mid-populate write that
# guard exists to prevent — see docs/bug-history for the mirror-keywords
# persistence report this was diagnosed from. Importing inside the handler
# instead resolves the name at call time, after all reload cascades settle.


@bpy.app.handlers.persistent
def _superskin_prefs_load_handler(dummy):
    """Re-populate WindowManager.superskin_prefs from user.json after every
    file load. ``superskin_prefs`` is SKIP_SAVE (see
    core_subsystems/preferences/property_groups.py), so opening any .blend
    file hands it a brand-new WindowManager ID-block whose PointerProperty
    starts out at type-defaults — without this handler, every file open/new
    would silently blank out the user's customized Preferences until the addon
    is disabled and re-enabled.
    """
    from ...core_subsystems.preferences import PreferencesService
    PreferencesService.load()
    _recover_orphaned_temp_vgs()


def _recover_orphaned_temp_vgs():
    """If a .blend was saved mid-Edit with temp VGs present, bake them back."""
    try:
        from ..layer_storage.temp_vg_bridge import (
            has_temp_vgs, read_temp_vgs_to_layer, delete_temp_vgs
        )
        from ..layer_storage.storage_service import LayerStorageService
        from ..facade.write import purge_zeroed_orphans_after_bake

        for obj in bpy.data.objects:
            if obj.type != 'MESH' or not has_temp_vgs(obj):
                continue
            storage = LayerStorageService(obj.data)
            if not storage.has_layer_system():
                delete_temp_vgs(obj)
                continue
            layer_dict, mask_dict, active_idx = read_temp_vgs_to_layer(obj)
            old_layer_dict = storage.read_layer_dict(active_idx)
            storage.write_layer_dict(active_idx, layer_dict)
            if mask_dict:
                storage.write_mask_dict(active_idx, mask_dict)
            else:
                storage.delete_mask_property(active_idx)
            purge_zeroed_orphans_after_bake(storage, obj, old_layer_dict, layer_dict)
            delete_temp_vgs(obj)
    except Exception as e:
        print(f"[SuperSkinPro] Warning: temp VG recovery failed: {e}")


def register():
    property_groups.register()
    # Initial PreferencesService.load() is deferred to the root __init__.py
    # register() — after features.register() has run so all feature-domain
    # preference extensions are registered with UnifiedRegistry before the
    # first load attempt.
    bpy.app.handlers.load_post.append(_superskin_prefs_load_handler)


def unregister():
    try:
        bpy.app.handlers.load_post.remove(_superskin_prefs_load_handler)
    except Exception:
        pass
    property_groups.unregister()
