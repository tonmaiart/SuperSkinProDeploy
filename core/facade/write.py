
import array
import time
from contextlib import contextmanager

from ...core_subsystems.rust_weight_engine import RustWeightEngine
from ...core_subsystems.debug_logging import DebugLogService
from ...core_subsystems.profiler import ProfilerService
from ...core_subsystems.profiler.profiler_service import profile_section
from ..viewport import tag_redraw_areas
from ..layer_storage.temp_vg_bridge import DEFORM_GEN_KEY


_live_state: dict = {}
_live_stroke: dict = {}
_live_vg_caches: dict = {}
_paint_sync_pending: set = set()

_BULK_WRITE_MIN_VERTS = 256


def _layer_rows(layer_int: dict, id_to_bone: dict) -> tuple:
    vert_ids, local_ids, weights = array.array('I'), array.array('i'), array.array('d')
    for v_idx, row in layer_int.items():
        for b_id, w in row.items():
            if b_id in id_to_bone:
                vert_ids.append(v_idx)
                local_ids.append(b_id)
                weights.append(w)
    return vert_ids, local_ids, weights, id_to_bone


class WriteFacadeMixin:
    """Mixin providing write access to layer storage and flatten pipeline."""

    def set_selected_bones_pool(self, names: set) -> None:
        storage = self.obj.superskin_storage
        storage.selected_names = f",{','.join(sorted(names))}," if names else ","

    def write_layer_dict(self, layer_dict: dict):
        from ..layer_storage.temp_vg_bridge import is_wp_session, push_layer_to_temp_vgs
        self.storage.write_layer_dict(self.active_layer_index, layer_dict)
        if is_wp_session(self.obj):
            push_layer_to_temp_vgs(self.obj, layer_dict)

    def write_mask_dict(self, mask_dict: dict):
        from ..layer_storage.temp_vg_bridge import is_wp_session, push_layer_to_temp_vgs
        if is_wp_session(self.obj):
            self.storage.write_mask_dict(self.active_layer_index, mask_dict)
            push_layer_to_temp_vgs(self.obj, None, mask_dict)
            return
        self.storage.write_mask_dict(self.active_layer_index, mask_dict)

    def _get_live_state(self, active_idx: int) -> dict:
        name = self.obj.name
        state = _live_state.get(name)
        if state is not None and state.get("active_idx") == active_idx:
            return state
        meta = self.storage.read_meta_list()
        others_layers, others_masks = {}, {}
        for layer in meta:
            i = layer["index"]
            if i == active_idx:
                continue
            others_layers[i] = self.storage.read_layer_raw(i)
            m = self.storage.read_mask_dict(i)
            if m:
                others_masks[i] = m
        active_mask = self.storage.read_mask_dict(active_idx)
        state = {
            "active_idx": active_idx,
            "meta": meta,
            "layers": others_layers,
            "masks": others_masks,
            "active_mask": active_mask,
        }
        _live_state[name] = state
        return state

    def _live_vg_cache(self) -> dict:
        name = self.obj.name
        if name not in _live_stroke:
            return {"deform": {}, "temp": {}, "temp_maps": {}}
        return _live_vg_caches.setdefault(name, {"deform": {}, "temp": {}, "temp_maps": {}})

    def _composite_write_dirty(self, state: dict, active_idx: int,
                               active_layer, mask_subset: dict, dirty,
                               temp_write: tuple = None) -> None:
        from ...core_subsystems.layer_compositor import LayerCompositor
        from ..layer_storage.temp_vg_bridge import apply_vg_deltas, write_group_targets

        obj = self.obj
        layer_map = dict(state["layers"])
        layer_map[active_idx] = active_layer
        mask_map = {i: {v: m[v] for v in dirty if v in m} for i, m in state["masks"].items()}
        if mask_subset:
            mask_map[active_idx] = mask_subset

        vgs = obj.vertex_groups
        live_cache = self._live_vg_cache()
        local_mapping = live_cache.get("local_mapping")
        if local_mapping is None:
            local_mapping = live_cache["local_mapping"] = self.storage.get_local_mapping(obj)
        name_to_idx, idx_to_name = local_mapping
        mesh = self.mesh
        with profile_section("core.facade.live.composite"):
            if isinstance(active_layer, tuple):
                vert_ids, group_ids, weights = LayerCompositor.composite_layer_groups(
                    state["meta"], layer_map, mask_map, name_to_idx, len(mesh.vertices),
                    dirty_verts=dirty,
                )
                new_state = {}
                for v, g, w in zip(vert_ids, group_ids, weights):
                    row = new_state.get(v)
                    if row is None:
                        row = new_state[v] = {}
                    row[g] = w
            else:
                result = LayerCompositor.composite_layers(
                    state["meta"], layer_map, mask_map, idx_to_name, len(mesh.vertices),
                    dirty_verts=dirty,
                )
                new_state = LayerCompositor.bone_weights_to_deform_state(result, name_to_idx)

        cache = live_cache["deform"]
        removals: dict = {}
        adds: dict = {}
        with profile_section("core.facade.live.deform_write"):
            if temp_write is not None:
                for v in dirty:
                    cache[v] = new_state.get(v, {})
                write_group_targets(mesh, sorted(dirty),
                                    (temp_write, (idx_to_name, new_state)))
                dirty = ()
            for v in dirty:
                old = cache.get(v)
                if old is None:
                    old = {g.group: g.weight for g in mesh.vertices[v].groups if g.group in idx_to_name}
                new = new_state.get(v, {})
                for gi in old.keys() - new.keys():
                    removals.setdefault(gi, []).append(v)
                for gi, w in new.items():
                    if abs(old.get(gi, -1.0) - w) > 1e-6:
                        adds.setdefault((gi, w), []).append(v)
                cache[v] = new
            apply_vg_deltas(vgs, removals, adds)

        with profile_section("core.facade.live.tag_redraw"):
            obj.update_tag()
            self._bump_deform_generation()
            tag_redraw_areas(window_manager=self.ctx.window_manager)

    @staticmethod
    def is_addon_stroke_active(obj_name: str) -> bool:
        return obj_name in _live_stroke

    def warm_live_state(self) -> None:
        _live_state.pop(self.obj.name, None)
        self._get_live_state(self.active_layer_index)

    @staticmethod
    def drop_live_state(obj_name: str) -> None:
        _live_state.pop(obj_name, None)
        _live_vg_caches.pop(obj_name, None)

    def begin_live_stroke(self, keep_state: bool = False) -> None:
        if not keep_state:
            _live_state.pop(self.obj.name, None)
        _live_vg_caches.pop(self.obj.name, None)
        _live_stroke[self.obj.name] = set()

    def end_live_stroke(self, keep_state: bool = False) -> None:
        from ..layer_storage.temp_vg_bridge import is_wp_session, pull_temp_to_storage
        name = self.obj.name
        dirty = _live_stroke.pop(name, None)
        caches = _live_vg_caches.pop(name, None) or {}
        vg_maps = caches.get("temp_maps")
        written = None if caches.get("mask_written") else caches.get("temp")
        if not keep_state:
            _live_state.pop(name, None)
        if not dirty:
            return
        with profile_section("core.facade.live.end_stroke", len(dirty)):
            if is_wp_session(self.obj):
                try:
                    pull_temp_to_storage(self.obj, self.storage, dirty, vg_maps, written)
                except Exception:
                    _paint_sync_pending.add(name)
                    raise
            self.shader_mgr.invalidate_color_only()

    def write_active_layer_live(self, layer_int: dict, id_to_bone: dict, dirty_verts,
                                mask_dict: dict = None, is_mask: bool = False,
                                active_coo: tuple = None) -> None:
        from ...core_subsystems.layer_compositor import LayerCompositor
        from ..layer_storage.temp_vg_bridge import push_layer_to_temp_vgs, temp_vg_targets_int

        obj = self.obj
        dirty = set(dirty_verts)
        bulk = len(dirty) >= _BULK_WRITE_MIN_VERTS
        temp_write = None
        touched = _live_stroke.get(obj.name)
        if touched is not None:
            touched |= dirty
        active_idx = self.active_layer_index
        state = self._get_live_state(active_idx)

        layer_subset_int = {v: layer_int[v] for v in dirty if v in layer_int}
        layer_subset = None
        live_cache = self._live_vg_cache()
        if is_mask:
            state["active_mask"] = mask_dict
            mask_subset = {v: mask_dict[v] for v in dirty if v in mask_dict}
            live_cache["mask_written"] = True
            with profile_section("core.facade.live.push_temp_vgs"):
                push_layer_to_temp_vgs(obj, None, mask_dict, dirty,
                                       vg_maps=live_cache["temp_maps"])
        else:
            active_mask = state["active_mask"]
            mask_subset = {v: active_mask[v] for v in dirty if v in active_mask}
            with profile_section("core.facade.live.push_temp_vgs"):
                if bulk:
                    temp_write = temp_vg_targets_int(obj, layer_subset_int, id_to_bone, dirty,
                                                     live_cache["temp_maps"])
                    live_cache["temp"].update(temp_write[1])
                else:
                    layer_subset = RustWeightEngine.map_layer_to_string(layer_subset_int, id_to_bone)
                    push_layer_to_temp_vgs(obj, layer_subset, None, dirty,
                                           live_cache=live_cache["temp"],
                                           vg_maps=live_cache["temp_maps"])
        if LayerCompositor.has_group_compositor():
            if not any(layer_int.values()):
                active_layer = {}
            elif (active_coo is not None and len(active_coo[0])
                  and id_to_bone.keys() >= set(active_coo[1])):
                active_layer = (array.array('I', active_coo[0]), array.array('i', active_coo[1]),
                                array.array('d', active_coo[2]), id_to_bone)
            else:
                active_layer = _layer_rows(layer_subset_int, id_to_bone)
        else:
            active_layer = (layer_subset if layer_subset is not None
                            else RustWeightEngine.map_layer_to_string(layer_subset_int, id_to_bone))
        self._composite_write_dirty(state, active_idx, active_layer, mask_subset, dirty,
                                    temp_write)

    def _bump_deform_generation(self) -> None:
        self.shader_mgr.bump_deform_generation()
        self.obj[DEFORM_GEN_KEY] = self.obj.get(DEFORM_GEN_KEY, 0) + 1

    def finish(self, *, color_only: bool = False):
        with profile_section("core.facade.finish.flatten"):
            self.storage.flatten_visible_layers_to_mesh(self.obj)
        with profile_section("core.facade.finish.mesh_update"):
            self.mesh.update()
            self.obj.update_tag()
        with profile_section("core.facade.finish.shader_invalidate"):
            self._bump_deform_generation()
            if color_only:
                self.shader_mgr.invalidate_color_only()
            else:
                self.shader_mgr.invalidate_and_redraw()
        tag_redraw_areas(window_manager=self.ctx.window_manager)

    def finish_color_only(self):
        self.finish(color_only=True)

    def write_active_layer(self, layer_str: dict, *, color_only: bool = True,
                           dirty_verts: set = None) -> None:
        if not hasattr(self, '_bone_to_id') or not hasattr(self, '_id_to_bone'):
            self.get_unified_mapping()
        result_int = {
            v_idx: {self._bone_to_id[b]: w for b, w in weights.items() if b in self._bone_to_id}
            for v_idx, weights in layer_str.items()
        }
        if ProfilerService.is_enabled():
            DebugLogService.log(
                "core_pipeline",
                f"write_active_layer(): {len(result_int)} verts, obj.mode={self.obj.mode}",
            )
        self.write_active_layer_int(result_int, self._id_to_bone, None, is_mask_mode=False,
                                    dirty_verts=dirty_verts)
        self.finish(color_only=color_only)

    def write_active_layer_touched(self, layer_str: dict, dirty_verts: set) -> None:
        from ..layer_storage.temp_vg_bridge import is_wp_session
        if not is_wp_session(self.obj) or self.obj.name in _live_stroke:
            self.write_active_layer(layer_str, color_only=True, dirty_verts=dirty_verts)
            return
        if not hasattr(self, '_bone_to_id') or not hasattr(self, '_id_to_bone'):
            self.get_unified_mapping()
        bone_to_id = self._bone_to_id
        layer_int = {
            v_idx: {bone_to_id[b]: w for b, w in weights.items() if b in bone_to_id}
            for v_idx, weights in layer_str.items()
        }
        self.begin_live_stroke()
        try:
            self.write_active_layer_live(layer_int, self._id_to_bone, dirty_verts)
        finally:
            self.end_live_stroke()

    @contextmanager
    def mutate_active_layer(self, *, color_only: bool = True):
        layer_data = self.read_active_layer()
        try:
            yield layer_data
        except Exception:
            DebugLogService.log(
                "core_pipeline",
                "mutate_active_layer(): exception in block, write skipped",
            )
            raise
        else:
            self.write_active_layer(layer_data, color_only=color_only)

    def write_active_layer_int(self, layer_int: dict, id_to_bone: dict,
                               mask_dict: dict = None, *,
                               is_mask_mode: bool = False,
                               dirty_verts: set = None):
        _profile_metrics = ProfilerService.is_enabled()
        _t0 = time.perf_counter() if _profile_metrics else None

        layer_str = RustWeightEngine.map_layer_to_string(layer_int, id_to_bone)
        if _profile_metrics:
            _t_convert = time.perf_counter()
        RustWeightEngine.prune_zero_bones(layer_str)
        if _profile_metrics:
            _t_normalize_prune = time.perf_counter()

        _size = sum(map(len, layer_str.values())) if ProfilerService.is_enabled() else None
        with profile_section("core.facade._write_active_layer_string.save_active_only", _size):
            self.storage.save_active(layer_str, mask_dict, is_mask_mode=is_mask_mode)
        from ..layer_storage.temp_vg_bridge import is_wp_session, push_layer_to_temp_vgs
        if is_wp_session(self.obj):
            with profile_section("core.facade._write_active_layer_string.push_temp_vgs", _size):
                push_layer_to_temp_vgs(self.obj, None if is_mask_mode else layer_str,
                                       mask_dict if is_mask_mode else None,
                                       dirty_verts=dirty_verts)
        if _profile_metrics:
            _t_persist = time.perf_counter()
            _base = "core.facade._write_active_layer_string"
            _size = len(dirty_verts) if dirty_verts is not None else len(layer_str)
            ProfilerService.record(f"{_base}.convert", 1000 * (_t_convert - _t0), _size)
            ProfilerService.record(f"{_base}.normalize_prune", 1000 * (_t_normalize_prune - _t_convert), _size)
            ProfilerService.record(f"{_base}.persist_save_active", 1000 * (_t_persist - _t_normalize_prune), _size)
            ProfilerService.record(f"{_base}.total", 1000 * (_t_persist - _t0), _size)
