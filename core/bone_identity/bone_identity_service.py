
from ..layer_storage.storage_service import LayerStorageService
from ..layer_storage.temp_vg_bridge import DEFORM_GEN_KEY, temp_id_to_bone
from ...core_subsystems.debug_logging import DebugLogService
from . import orphan_resolver

_scan_cache: dict = {}
_scan_cache_key: dict = {}

_ORPHAN_PREVIEW_VG = "__ssp_orphan_preview"


def _find_armature(obj):
    return next(
        (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object),
        None,
    )


def _mapping_has_orphan_ids(storage, obj) -> bool:
    real_vg_count = storage.real_vg_count(obj)
    return any(k >= real_vg_count for k in temp_id_to_bone(obj))


def _read_live_active_layer(storage, obj):
    from ..layer_storage.temp_vg_bridge import (
        has_temp_vgs, read_active_layer_from_mesh,
    )
    if not has_temp_vgs(obj) or not _mapping_has_orphan_ids(storage, obj):
        return None, None
    layer_dict, _, active_idx = read_active_layer_from_mesh(obj)
    return active_idx, layer_dict


def _compute_signature(storage, obj, arm_obj) -> tuple:
    vg_names = tuple(sorted(storage.get_local_mapping(obj)[0]))
    layer_blobs = tuple(sorted(storage.harvest_layer_data_map().items()))
    arm_names = tuple(sorted(b.name for b in arm_obj.data.bones)) if arm_obj else ()
    deform_gen = obj.get(DEFORM_GEN_KEY, 0) if _mapping_has_orphan_ids(storage, obj) else None
    return (vg_names, hash(layer_blobs), arm_names, deform_gen)


class BoneIdentityService:
    def __init__(self, context, obj=None):
        self.ctx = context
        self.obj = obj if obj is not None else (context.active_object if context else None)
        if not self.obj or self.obj.type != 'MESH':
            raise ValueError("No active mesh object")
        self.storage = LayerStorageService(self.obj.data)
        self.arm_obj = _find_armature(self.obj)

    def scan_readonly(self) -> list:
        key = self.obj.data.name
        sig = _compute_signature(self.storage, self.obj, self.arm_obj)
        if _scan_cache_key.get(key) == sig and key in _scan_cache:
            return _scan_cache[key]

        live_override = _read_live_active_layer(self.storage, self.obj)
        result = orphan_resolver.scan_orphans(
            self.storage, self.obj, self.arm_obj, live_override=live_override,
        )
        _scan_cache[key] = result
        _scan_cache_key[key] = sig
        return result

    def backfill_and_scan(self) -> list:
        live_override = _read_live_active_layer(self.storage, self.obj)
        result = orphan_resolver.scan_orphans(
            self.storage, self.obj, self.arm_obj, live_override=live_override,
        )
        orphan_resolver.backfill_uuid_map(self.storage, self.obj, self.arm_obj)
        key = self.obj.data.name
        _scan_cache[key] = result
        _scan_cache_key[key] = _compute_signature(self.storage, self.obj, self.arm_obj)
        return result

    def delete_bone(self, source_name: str, layer_index: int = None):
        from ..facade import CoreFacade
        facade = CoreFacade(self.ctx)
        meta_list = self.storage.read_meta_list()
        orphan_resolver.delete_bone_weights(
            self.storage, meta_list, source_name, layer_index=layer_index
        )
        facade.finish()

    def preview_orphan_weight(self, orphan_name: str):
        from ..layer_storage.temp_vg_bridge import is_wp_session
        if is_wp_session(self.obj):
            self._preview_orphan_weight_session(orphan_name)
            return

        weights = orphan_resolver.composite_orphan_weight(self.storage, self.obj, orphan_name)

        vg = self.obj.vertex_groups.get(_ORPHAN_PREVIEW_VG)
        num_verts = len(self.obj.data.vertices)
        if vg is not None:
            vg.remove(range(num_verts))
        else:
            vg = self.obj.vertex_groups.new(name=_ORPHAN_PREVIEW_VG)

        for v_idx, w in weights.items():
            vg.add([v_idx], w, 'REPLACE')

        self.obj.vertex_groups.active_index = vg.index

        DebugLogService.log(
            "bone_id",
            f"preview_orphan_weight(): obj={self.obj.name!r} orphan_name={orphan_name!r} "
            f"OBJECT mode -- preview_vg_index={vg.index} weighted_verts={len(weights)}",
        )

    def _preview_orphan_weight_session(self, orphan_name: str):
        from ..layer_storage.temp_vg_bridge import weight_vg_name, get_active_layer_from_meta

        id_to_bone = temp_id_to_bone(self.obj)

        active_idx = get_active_layer_from_meta(self.obj)
        vg_idx = next((k for k, v in id_to_bone.items() if v == orphan_name), None)
        temp_vg = (
            self.obj.vertex_groups.get(weight_vg_name(active_idx, vg_idx))
            if vg_idx is not None and active_idx >= 0 else None
        )

        DebugLogService.log(
            "bone_id",
            f"preview_orphan_weight(): obj={self.obj.name!r} orphan_name={orphan_name!r} "
            f"WEIGHT_PAINT session -- ssp_meta_map has {len(id_to_bone)} entries, resolved "
            f"vg_idx={vg_idx!r} temp_vg_found={temp_vg is not None}",
        )

        if temp_vg is not None:
            self.obj.vertex_groups.active_index = temp_vg.index

    def clear_orphan_weight_preview(self):
        vg = self.obj.vertex_groups.get(_ORPHAN_PREVIEW_VG)
        if vg is not None:
            self.obj.vertex_groups.remove(vg)

    @staticmethod
    def get_scan_for_object(obj) -> list:
        if not obj or obj.type != 'MESH':
            return []
        try:
            return BoneIdentityService(None, obj=obj).scan_readonly()
        except ValueError:
            return []

    @staticmethod
    def clear_scan_cache():
        _scan_cache.clear()
        _scan_cache_key.clear()
