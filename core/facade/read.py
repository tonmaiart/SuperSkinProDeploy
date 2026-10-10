
from ...core_subsystems.context_selection_service import ContextSelectionService
from ...core_subsystems.profiler.profiler_service import profile_section
from ...core_subsystems.topology_cache_manager import TopologyCacheManager


class ReadFacadeMixin:
    """Mixin providing read-only access to layer, mesh, and bone state."""

    def _sync_paint_to_storage(self) -> None:
        from ..layer_storage.temp_vg_bridge import is_wp_session, pull_temp_to_storage
        from .write import _live_stroke, _paint_sync_pending
        name = self.obj.name
        if name not in _paint_sync_pending or name in _live_stroke or not is_wp_session(self.obj):
            return
        pull_temp_to_storage(self.obj, self.storage)
        _paint_sync_pending.discard(name)

    def get_active_layer_dict(self) -> dict:
        self._sync_paint_to_storage()
        return self.storage.read_active_layer_dict()

    def is_paint_session(self) -> bool:
        from ..layer_storage.temp_vg_bridge import is_wp_session
        return is_wp_session(self.obj)

    def pull_paint_to_storage(self) -> bool:
        from ..layer_storage.temp_vg_bridge import has_temp_vgs, pull_temp_to_storage
        if not has_temp_vgs(self.obj) or not self.storage.has_layer_system():
            return False
        return pull_temp_to_storage(self.obj, self.storage)

    def get_active_mask_dict(self) -> dict:
        self._sync_paint_to_storage()
        return self.storage.read_active_mask_dict()

    def get_active_layer_index(self) -> int:
        return self.active_layer_index

    def get_meta_list(self) -> list:
        return self.storage.read_meta_list()

    def get_flattened_mask_dict(self, layer_index: int) -> dict:
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC
        meta_list = self.storage.read_meta_list()
        num_verts = self.get_num_verts()
        mask_dicts_map = {
            l["index"]: self.storage.read_mask_dict(l["index"]) for l in meta_list
        }
        return _LC.get_layer_cut_mask(meta_list, mask_dicts_map, layer_index, num_verts)

    def get_selected_verts(self) -> list[int]:
        if hasattr(self, '_cached_sel_verts'):
            return self._cached_sel_verts
        result = ContextSelectionService.get_selected_verts(self.obj, self.mesh)
        self._cached_sel_verts = result
        return result

    def get_active_vg_id(self):
        return self._active_vg_id()

    def get_active_vg_name(self) -> str:
        return self.active_vg_name_of(self.obj)

    def get_vertex_groups(self):
        return self.obj.vertex_groups

    def get_mesh(self):
        return self.mesh

    def get_obj(self):
        return self.obj

    def is_mask_context(self) -> bool:
        try:
            return ContextSelectionService.is_mask_context(self.ctx.scene)
        except Exception:
            return False

    def get_local_mapping(self) -> tuple[dict[str, int], dict[int, str]]:
        return TopologyCacheManager.get_local_mapping(self.obj, self.storage)

    def get_bone_locks(self, layer_index: int = None) -> dict:
        from ..ui_controller import layer_crud
        return layer_crud.get_bone_locks(self, layer_index)

    def get_vertex_coordinates(self) -> list:
        return self.storage.get_vertex_coordinates()

    def get_num_verts(self) -> int:
        return len(self.mesh.vertices)

    def get_selected_bones_pool(self) -> set:
        storage = self.obj.superskin_storage
        return {n for n in storage.selected_names.split(",") if n}

    def get_selected_bones_pool_string(self) -> str:
        names = self.get_selected_bones_pool()
        return f",{','.join(sorted(names))}," if names else ","

    def read_active_layer(self) -> dict:
        layer_int = self.read_active_layer_int()
        id_to_bone = self._id_to_bone
        return {
            v_idx: {id_to_bone[b]: w for b, w in weights.items() if b in id_to_bone}
            for v_idx, weights in layer_int.items()
        }

    def read_active_layer_int(self) -> dict:
        bone_to_id, id_to_bone = self.storage.get_unified_mapping(self.obj)
        self._bone_to_id = bone_to_id
        self._id_to_bone = id_to_bone
        return self._read_active_layer_int(bone_to_id)

    def get_unified_mapping(self) -> tuple:
        if hasattr(self, '_bone_to_id') and hasattr(self, '_id_to_bone'):
            return self._bone_to_id, self._id_to_bone
        bone_to_id, id_to_bone = self.storage.get_unified_mapping(self.obj)
        self._bone_to_id = bone_to_id
        self._id_to_bone = id_to_bone
        return bone_to_id, id_to_bone

    def get_locks_by_id(self) -> dict:
        return self._locks_by_id()

    def get_deform_bone_ids(self) -> set:
        bone_to_id, _ = self.get_unified_mapping()
        return set(bone_to_id.values())

    def get_cached_mesh_neighbors(self) -> dict[int, list[int]]:
        return TopologyCacheManager.get_cached_mesh_neighbors(self.mesh, self.storage)

    def _read_active_layer_int(self, bone_to_id: dict) -> dict:
        from ...core_subsystems.rust_weight_engine import RustWeightEngine as _RWE_local
        from ..layer_storage.temp_vg_bridge import read_stored_layer_readonly

        with profile_section("core.facade.read_layer.sync_paint"):
            self._sync_paint_to_storage()
        with profile_section("core.facade.read_layer.from_arrays"):
            arrays = self.storage.read_layer_arrays(self.storage.get_active_layer_index())
            if arrays is not None:
                from ...core_subsystems.layer_compositor import LayerCompositor as _LC_local
                return _LC_local.int_rows(arrays, bone_to_id)
        with profile_section("core.facade.read_layer.decode"):
            raw = read_stored_layer_readonly(self.storage, self.storage.get_active_layer_index())
        with profile_section("core.facade.read_layer.to_int", len(raw)):
            return _RWE_local.map_layer_to_int(raw, bone_to_id)

    def _locks_by_id(self) -> dict:
        name_locks = self._layer_mgr.get_bone_locks(
            self.storage.read_meta_list(), self.active_layer_index
        )
        bone_to_id, _ = self.storage.get_unified_mapping(self.obj)
        return {vg_id: name_locks.get(name, False) for name, vg_id in bone_to_id.items()}
