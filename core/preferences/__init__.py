
import bpy

from ...core_subsystems.preferences import property_groups



@bpy.app.handlers.persistent
def _superskin_prefs_load_handler(dummy):
    from ...core_subsystems.preferences import PreferencesService
    PreferencesService.load()
    _recover_orphaned_temp_vgs()


def _recover_orphaned_temp_vgs():
    try:
        from ..layer_storage.temp_vg_bridge import (
            has_temp_vgs, has_layer_cache, read_temp_vgs_to_layer, delete_temp_vgs,
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
            for layer in storage.read_meta_list():
                l_idx = layer.get("index")
                if l_idx is None or not has_layer_cache(obj, l_idx):
                    continue
                layer_dict, mask_dict, _ = read_temp_vgs_to_layer(obj, layer_idx=l_idx)
                old_layer_dict = storage.read_layer_dict(l_idx)
                storage.write_layer_dict(l_idx, layer_dict)
                if mask_dict:
                    storage.write_mask_dict(l_idx, mask_dict)
                else:
                    storage.delete_mask_property(l_idx)
                purge_zeroed_orphans_after_bake(storage, obj, old_layer_dict, layer_dict)
            delete_temp_vgs(obj)
    except Exception as e:
        print(f"[SuperSkinPro] Warning: temp VG recovery failed: {e}")


def register():
    property_groups.register()
    bpy.app.handlers.load_post.append(_superskin_prefs_load_handler)


def unregister():
    try:
        bpy.app.handlers.load_post.remove(_superskin_prefs_load_handler)
    except Exception:
        pass
    property_groups.unregister()
