
import array
import time
from contextlib import contextmanager

import numpy as np

from ...core_subsystems.rust_weight_engine import RustWeightEngine
from ...core_subsystems.debug_logging import DebugLogService
from ...core_subsystems.profiler import ProfilerService
from ...core_subsystems.profiler.profiler_service import profile_section
from ..viewport import tag_redraw_areas
from ..layer_storage.temp_vg_bridge import DEFORM_GEN_KEY, WrittenRows


_live_state: dict = {}
_live_stroke: dict = {}
_live_vg_caches: dict = {}
_deferred: dict = {}
_kept_groups: dict = {}
_KEPT_GROUPS_MAX_BYTES = 256 * 1024 * 1024


def _layer_rows(layer_int: dict, id_to_bone: dict) -> tuple:
    from ...core_subsystems.rust_weight_engine.flat_array_bridge import int_layer_columns
    return _coo_rows(*int_layer_columns(layer_int), id_to_bone)


def _coo_rows(vert_ids, local_ids, weights, id_to_bone: dict) -> tuple:
    keep = np.isin(local_ids, np.fromiter(id_to_bone, dtype=np.int64, count=len(id_to_bone)))
    out = []
    for typecode, values in (('I', vert_ids), ('i', local_ids), ('d', weights)):
        column = array.array(typecode)
        column.frombytes(values[keep].astype(np.dtype(typecode)).tobytes())
        out.append(column)
    return (*out, id_to_bone)


class WriteFacadeMixin:
    """Mixin providing write access to layer storage and flatten pipeline."""

    def set_selected_bones_pool(self, names: set) -> None:
        storage = self.obj.superskin_storage
        storage.selected_names = f",{','.join(sorted(names))}," if names else ","

    def write_layer_dict(self, layer_dict: dict):
        self.storage.write_layer_dict(self.active_layer_index, layer_dict)

    def write_mask_dict(self, mask_dict: dict):
        self.storage.write_mask_dict(self.active_layer_index, mask_dict)

    def write_layer_int(self, layer_index: int, layer_int: dict, id_to_bone: dict) -> None:
        if layer_index == self.active_layer_index:
            self.write_active_layer_int(layer_int, id_to_bone)
            return
        layer_str = RustWeightEngine.map_layer_to_string(layer_int, id_to_bone)
        RustWeightEngine.prune_zero_bones(layer_str)
        self.storage.write_layer_dict(layer_index, layer_str)
        _live_state.pop(self.obj.name, None)

    def write_layer_mask_dict(self, layer_index: int, mask_dict: dict) -> None:
        if layer_index == self.active_layer_index:
            self.write_mask_dict(mask_dict)
            return
        self.storage.write_mask_dict(layer_index, mask_dict)
        _live_state.pop(self.obj.name, None)

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
            return {"rows": WrittenRows()}
        return _live_vg_caches.setdefault(name, {"rows": WrittenRows()})

    def _session_surface(self, live_cache: dict) -> frozenset:
        surface = live_cache.get("surface")
        if surface is None:
            from ..layer_storage.temp_vg_bridge import session_surface
            surface = live_cache["surface"] = session_surface(self.obj)
        return surface

    @staticmethod
    def _record_rows(live_cache: dict, verts, rows) -> None:
        if rows is None:
            return
        if isinstance(rows, tuple):
            live_cache["rows"].add_coo(verts, *rows)
        else:
            live_cache["rows"].update(rows)

    def _groups_signature(self) -> tuple:
        return (self.obj.get(DEFORM_GEN_KEY, 0), len(self.mesh.vertices),
                tuple(vg.name for vg in self.obj.vertex_groups))

    def _group_state(self, live_cache: dict):
        from ..layer_storage.temp_vg_bridge import GroupWriteState
        state = live_cache.get("groups")
        if state is None or len(state.known) != len(self.mesh.vertices):
            state = live_cache["groups"] = GroupWriteState(len(self.mesh.vertices))
        return state

    def _composite_write_dirty(self, state: dict, active_idx: int,
                               active_layer, mask_subset: dict, dirty,
                               active_rows=None) -> None:
        from ...core_subsystems.layer_compositor import LayerCompositor
        from ..layer_storage.temp_vg_bridge import targets_coo, write_group_entries

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
                deform_rows = LayerCompositor.composite_layer_groups(
                    state["meta"], layer_map, mask_map, name_to_idx, len(mesh.vertices),
                    dirty_verts=dirty,
                )
            else:
                result = LayerCompositor.composite_layers(
                    state["meta"], layer_map, mask_map, idx_to_name, len(mesh.vertices),
                    dirty_verts=dirty,
                )
                deform_rows = targets_coo(
                    LayerCompositor.bone_weights_to_deform_state(result, name_to_idx))

        verts = np.fromiter(sorted(dirty), dtype=np.int64, count=len(dirty))
        self._record_rows(live_cache, verts, active_rows)
        deferred = _deferred.get(obj.name) if obj.name in _live_stroke else None
        if deferred is not None:
            with profile_section("core.facade.live.defer"):
                deferred["deform_owned"] = idx_to_name
                deferred["deform"].add_coo(verts, *deform_rows)
                rows = active_rows
                if rows is not None and not isinstance(rows, tuple):
                    rows = targets_coo(rows)
                deferred["last"] = (verts, deform_rows, rows)
            return

        with profile_section("core.facade.live.deform_write"):
            write_group_entries(vgs, mesh, verts, [(idx_to_name, *deform_rows)],
                                self._group_state(live_cache))

        with profile_section("core.facade.live.tag_redraw"):
            obj.update_tag()
            self._bump_deform_generation()
            tag_redraw_areas(window_manager=self.ctx.window_manager)

    @staticmethod
    def is_addon_stroke_active(obj_name: str) -> bool:
        return obj_name in _live_stroke

    def warm_live_state(self) -> None:
        _live_state.pop(self.obj.name, None)
        _kept_groups.pop(self.obj.name, None)
        self._get_live_state(self.active_layer_index)

    @staticmethod
    def drop_live_state(obj_name: str) -> None:
        _live_state.pop(obj_name, None)
        _live_vg_caches.pop(obj_name, None)
        _kept_groups.pop(obj_name, None)
        _deferred.pop(obj_name, None)

    def take_live_rows(self):
        deferred = _deferred.get(self.obj.name)
        if deferred is None:
            return None
        rows, deferred["last"] = deferred["last"], None
        return rows

    def _flush_deferred(self) -> None:
        deferred = _deferred.pop(self.obj.name, None)
        if deferred is None or deferred["deform_owned"] is None:
            return
        from ..layer_storage.temp_vg_bridge import write_group_entries
        live_cache = self._live_vg_cache()
        state = self._group_state(live_cache)
        vgs, mesh = self.obj.vertex_groups, self.mesh
        with profile_section("core.facade.live.flush_deferred"):
            written = deferred["deform"]
            verts = written.marked()
            write_group_entries(vgs, mesh, verts, [(deferred["deform_owned"],
                                                     *written.rows_coo(verts))], state)
            self.obj.update_tag()
            self._bump_deform_generation()
            tag_redraw_areas(window_manager=self.ctx.window_manager)

    def begin_live_stroke(self, keep_state: bool = False) -> None:
        name = self.obj.name
        _deferred[name] = {"deform": WrittenRows(), "deform_owned": None, "last": None}
        kept = _kept_groups.pop(name, None)
        if not keep_state:
            _live_state.pop(name, None)
        _live_vg_caches.pop(name, None)
        if keep_state and kept is not None and kept[0] == self._groups_signature():
            _live_vg_caches[name] = {"rows": WrittenRows(), "groups": kept[1]}
        _live_stroke[name] = set()

    def end_live_stroke(self, keep_state: bool = False) -> None:
        from ..layer_storage.temp_vg_bridge import commit_stroke_rows, is_paint_session
        name = self.obj.name
        if name in _live_stroke:
            self._flush_deferred()
        _deferred.pop(name, None)
        dirty = _live_stroke.pop(name, None)
        caches = _live_vg_caches.pop(name, None) or {}
        _kept_groups.pop(name, None)
        if not keep_state:
            _live_state.pop(name, None)
        group_state = caches.get("groups")
        if (keep_state and group_state is not None
                and group_state.nbytes() <= _KEPT_GROUPS_MAX_BYTES):
            _kept_groups[name] = (self._groups_signature(), group_state)
        if not dirty:
            return
        with profile_section("core.facade.live.end_stroke", len(dirty)):
            if is_paint_session(self.obj):
                try:
                    commit_stroke_rows(self.obj, self.storage, dirty, caches.get("rows"),
                                       caches.get("id_to_bone") or {}, caches.get("mask"))
                except Exception:
                    _kept_groups.pop(name, None)
                    raise
            self.shader_mgr.invalidate_color_only()

    def write_active_layer_live(self, layer_int: dict, id_to_bone: dict, dirty_verts,
                                mask_dict: dict = None, is_mask: bool = False,
                                active_coo: tuple = None) -> None:
        from ...core_subsystems.layer_compositor import LayerCompositor
        from ..layer_storage.temp_vg_bridge import surface_rows_coo, surface_rows_int

        obj = self.obj
        dirty = set(dirty_verts)
        active_rows = None
        touched = _live_stroke.get(obj.name)
        if touched is not None:
            touched |= dirty
        active_idx = self.active_layer_index
        state = self._get_live_state(active_idx)
        live_cache = self._live_vg_cache()
        live_cache["id_to_bone"] = id_to_bone

        coo_of = getattr(layer_int, "coo_of", None)
        if coo_of is not None and not is_mask and LayerCompositor.has_group_compositor():
            coo = coo_of(dirty)
            active_mask = state["active_mask"]
            mask_subset = ({v: active_mask[v] for v in dirty if v in active_mask}
                           if active_mask else {})
            active_rows = surface_rows_coo(coo, id_to_bone, self._session_surface(live_cache))
            active_layer = _coo_rows(*coo, id_to_bone) if layer_int.has_entries() else {}
            self._composite_write_dirty(state, active_idx, active_layer, mask_subset, dirty,
                                        active_rows)
            return

        rows_of = getattr(layer_int, "rows_of", None)
        layer_subset_int = (rows_of(dirty) if rows_of is not None
                            else {v: layer_int[v] for v in dirty if v in layer_int})
        if is_mask:
            state["active_mask"] = mask_dict
            mask_subset = {v: mask_dict[v] for v in dirty if v in mask_dict}
            live_cache["mask"] = mask_dict
        else:
            active_mask = state["active_mask"]
            mask_subset = {v: active_mask[v] for v in dirty if v in active_mask}
            active_rows = surface_rows_int(layer_subset_int, id_to_bone, dirty,
                                           self._session_surface(live_cache))
        if LayerCompositor.has_group_compositor():
            has_entries = getattr(layer_int, "has_entries", None)
            if not (has_entries() if has_entries is not None else any(layer_int.values())):
                active_layer = {}
            elif (active_coo is not None and len(active_coo[0])
                  and id_to_bone.keys() >= set(active_coo[1])):
                active_layer = (array.array('I', active_coo[0]), array.array('i', active_coo[1]),
                                array.array('d', active_coo[2]), id_to_bone)
            else:
                active_layer = _layer_rows(layer_subset_int, id_to_bone)
        else:
            active_layer = RustWeightEngine.map_layer_to_string(layer_subset_int, id_to_bone)
        self._composite_write_dirty(state, active_idx, active_layer, mask_subset, dirty,
                                    active_rows)

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
        from ..layer_storage.temp_vg_bridge import is_paint_session
        if not is_paint_session(self.obj) or self.obj.name in _live_stroke:
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
        if _profile_metrics:
            _t_persist = time.perf_counter()
            _base = "core.facade._write_active_layer_string"
            _size = len(dirty_verts) if dirty_verts is not None else len(layer_str)
            ProfilerService.record(f"{_base}.convert", 1000 * (_t_convert - _t0), _size)
            ProfilerService.record(f"{_base}.normalize_prune", 1000 * (_t_normalize_prune - _t_convert), _size)
            ProfilerService.record(f"{_base}.persist_save_active", 1000 * (_t_persist - _t_normalize_prune), _size)
            ProfilerService.record(f"{_base}.total", 1000 * (_t_persist - _t0), _size)
