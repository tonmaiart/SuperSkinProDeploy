"""WriteFacadeMixin — all state-mutation, storage-commit, and flatten operations.

Methods here may modify layer storage or trigger mesh vertex group commits.

Every write lands in ss_layer_N / ss_mask_N (the permanent per-layer storage)
and finish() then recomposites the visible layers into the mesh's real deform
vertex groups, so the Armature modifier deforms the viewport live in Weight
Paint Mode.

finish() passes nothing BMesh-related: CoreFacade's public proxy properties
(obj, mesh, storage, shader_mgr) satisfy the attribute contract pipeline
expects.

write_active_layer() and write_layer_dict() / write_mask_dict() delegate to
_write_active_layer_string() for orphan-merge / zero-prune logic.

_normalize_orphan_budget() and _purge_zeroed_orphans_from_all_layers() are
module-level helpers used exclusively by _write_active_layer_string().

mutate_active_layer() is a thin contextmanager composition of
read_active_layer() + write_active_layer() — it does not duplicate their
internals, so it can never drift out of sync with orphan/mask handling
changes made to either. See docs/core-interfaces/edit_mode_weight_write_pattern.md.
"""

import time
from contextlib import contextmanager

from ...core_subsystems.rust_weight_engine import RustWeightEngine
from ...core_subsystems.debug_logging import DebugLogService
from ...core_subsystems.profiler import ProfilerService
from ...core_subsystems.profiler.profiler_service import profile_section


# Per-object state for an addon brush stroke (see begin_live_stroke()).
_live_state: dict = {}
_live_stroke: dict = {}
_live_vg_caches: dict = {}


# ── Orphan budget helpers ─────────────────────────────────────────────────────

def _normalize_orphan_budget(layer_str: dict, known_bone_names: set) -> None:
    """Scale orphaned bone weights down so total weight per vertex stays <= 1.0."""
    for weights in layer_str.values():
        known_total = sum(w for name, w in weights.items() if name in known_bone_names)
        orphan_total = sum(w for name, w in weights.items() if name not in known_bone_names)
        if orphan_total <= 0.0:
            continue
        orphan_budget = max(0.0, 1.0 - known_total)
        if orphan_total <= orphan_budget:
            continue
        scale = orphan_budget / orphan_total
        for name in list(weights):
            if name not in known_bone_names:
                weights[name] = weights[name] * scale


def _purge_zeroed_orphans_from_all_layers(storage, orphan_entries: dict,
                                           written_layer_str: dict) -> None:
    """Remove orphaned bones fully zeroed from the active layer across all other layers."""
    orphan_names: set = set()
    for v_weights in orphan_entries.values():
        orphan_names.update(v_weights.keys())

    still_present: set = set()
    for v_weights in written_layer_str.values():
        still_present.update(v_weights.keys())

    fully_zeroed = orphan_names - still_present
    if not fully_zeroed:
        return

    active_idx = storage.get_active_layer_index()
    meta_list = storage.read_meta_list()

    for layer in meta_list:
        idx = layer["index"]
        if idx == active_idx:
            continue
        layer_dict = storage.read_layer_dict(idx)
        changed = False
        for v_weights in layer_dict.values():
            for bone_name in fully_zeroed:
                if bone_name in v_weights:
                    del v_weights[bone_name]
                    changed = True
        if changed:
            RustWeightEngine.prune_zero_bones(layer_dict)
            storage.write_layer_dict(idx, layer_dict)

    meta_changed = False
    for layer in meta_list:
        locks = layer.get("bone_locks", {})
        new_locks = {k: v for k, v in locks.items() if k not in fully_zeroed}
        if new_locks != locks:
            layer["bone_locks"] = new_locks
            meta_changed = True

        sel = layer.get("bone_selection", ",")
        new_sel = sel
        for bone_name in fully_zeroed:
            new_sel = new_sel.replace(f"{bone_name},", "")
        if new_sel != sel:
            layer["bone_selection"] = new_sel
            meta_changed = True

        if layer.get("active_bone", "") in fully_zeroed:
            layer["active_bone"] = ""
            meta_changed = True

    if meta_changed:
        storage.write_meta_list(meta_list)


def purge_zeroed_orphans_after_bake(storage, obj, old_layer_dict: dict,
                                     new_layer_dict: dict) -> None:
    """Purge orphan bones that were present in old_layer_dict but are absent
    from new_layer_dict, from the meta list and every other layer. For
    callers that write straight to storage instead of going through
    _write_active_layer_string()."""
    real_vg_names = {vg.name for vg in obj.vertex_groups if not vg.name.startswith("__ssp_")}
    orphan_entries = {
        v_idx: {b: w for b, w in weights.items() if b not in real_vg_names}
        for v_idx, weights in old_layer_dict.items()
        if any(b not in real_vg_names for b in weights)
    }
    if orphan_entries:
        _purge_zeroed_orphans_from_all_layers(storage, orphan_entries, new_layer_dict)


class WriteFacadeMixin:
    """Mixin providing write access to layer storage and flatten pipeline."""

    def purge_zeroed_orphans_after_bake(self, old_layer_dict: dict, new_layer_dict: dict) -> None:
        purge_zeroed_orphans_after_bake(self.storage, self.obj, old_layer_dict, new_layer_dict)

    def set_selected_bones_pool(self, names: set) -> None:
        """Write the multi-select pool to storage. Companion to
        ReadFacadeMixin.get_selected_bones_pool()."""
        storage = self.obj.superskin_storage
        storage.selected_names = f",{','.join(sorted(names))}," if names else ","

    def write_layer_dict(self, layer_dict: dict):
        """Commit a nested weight dict straight to ss_layer_N."""
        from ..layer_storage.temp_vg_bridge import is_wp_session, push_layer_to_temp_vgs
        self.storage.write_layer_dict(self.active_layer_index, layer_dict)
        if is_wp_session(self.obj):
            push_layer_to_temp_vgs(self.obj, layer_dict)

    def write_mask_dict(self, mask_dict: dict):
        """Write the active layer's mask straight to ss_mask_N."""
        from ..layer_storage.temp_vg_bridge import is_wp_session, push_layer_to_temp_vgs
        if is_wp_session(self.obj):
            self.storage.write_mask_dict(self.active_layer_index, mask_dict)
            push_layer_to_temp_vgs(self.obj, None, mask_dict)
            return
        self.storage.write_mask_dict(self.active_layer_index, mask_dict)

    def _get_live_state(self, active_idx: int) -> dict:
        """Per-stroke cache: meta list, every other layer decoded once, and
        the active layer's stored mask."""
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
            others_layers[i] = self.storage.read_layer_dict(i)
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
        """Per-stroke ``{v_idx: {group: weight}}`` mirrors of what this stroke
        last wrote to the real deform VGs and to the temp VGs, so a dab never
        re-reads vertex groups it already knows. Discarded with the stroke; a
        write outside a stroke gets a throwaway cache."""
        name = self.obj.name
        if name not in _live_stroke:
            return {"deform": {}, "temp": {}}
        return _live_vg_caches.setdefault(name, {"deform": {}, "temp": {}})

    def _composite_write_dirty(self, state: dict, active_idx: int,
                               layer_subset: dict, mask_subset: dict, dirty) -> None:
        """Recomposite only *dirty* vertices across all visible layers and
        write just those vertices' real deform VG entries."""
        from ...core_subsystems.layer_compositor import LayerCompositor
        from ..layer_storage.temp_vg_bridge import apply_vg_deltas

        obj = self.obj
        layer_map = {i: {v: d[v] for v in dirty if v in d} for i, d in state["layers"].items()}
        layer_map[active_idx] = layer_subset
        mask_map = {i: {v: m[v] for v in dirty if v in m} for i, m in state["masks"].items()}
        if mask_subset:
            mask_map[active_idx] = mask_subset

        vgs = obj.vertex_groups
        idx_to_name = {vg.index: vg.name for vg in vgs if not vg.name.startswith("__ssp_")}
        name_to_idx = {n: i for i, n in idx_to_name.items()}
        mesh = self.mesh
        with profile_section("core.facade.live.composite"):
            result = LayerCompositor.composite_layers(
                state["meta"], layer_map, mask_map, idx_to_name, len(mesh.vertices),
                dirty_verts=dirty,
            )
            new_state = LayerCompositor.bone_weights_to_deform_state(result, name_to_idx)

        cache = self._live_vg_cache()["deform"]
        removals: dict = {}
        adds: dict = {}
        with profile_section("core.facade.live.deform_write"):
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
            self._shader_mgr.bump_deform_generation()
            obj["__ssp_deform_gen"] = obj.get("__ssp_deform_gen", 0) + 1
            for window in self.ctx.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()

    @staticmethod
    def is_addon_stroke_active(obj_name: str) -> bool:
        return obj_name in _live_stroke

    def warm_live_state(self) -> None:
        """Decode every non-active layer now so the first dab of the next
        stroke finds the per-stroke cache ready. The caller owns invalidation
        and must pass keep_state=True to begin_live_stroke() to reuse it."""
        _live_state.pop(self.obj.name, None)
        self._get_live_state(self.active_layer_index)

    @staticmethod
    def drop_live_state(obj_name: str) -> None:
        _live_state.pop(obj_name, None)
        _live_vg_caches.pop(obj_name, None)

    def begin_live_stroke(self, keep_state: bool = False) -> None:
        """Start an addon brush stroke: per-dab writes go through
        write_active_layer_live() and storage is committed at end_live_stroke().
        keep_state=True reuses a per-stroke cache the caller has verified is
        still current."""
        if not keep_state:
            _live_state.pop(self.obj.name, None)
        _live_vg_caches.pop(self.obj.name, None)
        _live_stroke[self.obj.name] = set()

    def end_live_stroke(self, keep_state: bool = False) -> None:
        """Commit the stroke: merge the vertices touched by the stroke from the
        temp VGs into storage. The real deform VGs were already kept current
        per dab, so no whole-mesh recomposite is needed. keep_state=True leaves
        the per-stroke cache in place for the caller to reuse."""
        from ..layer_storage.temp_vg_bridge import is_wp_session, pull_temp_to_storage
        name = self.obj.name
        dirty = _live_stroke.pop(name, None)
        _live_vg_caches.pop(name, None)
        if not keep_state:
            _live_state.pop(name, None)
        if not dirty:
            return
        if is_wp_session(self.obj):
            pull_temp_to_storage(self.obj, self.storage, dirty)
        self._shader_mgr.invalidate_color_only()

    def write_active_layer_live(self, layer_int: dict, id_to_bone: dict, dirty_verts,
                                mask_dict: dict = None, is_mask: bool = False) -> None:
        """Per-dab write during an addon brush stroke: updates only
        *dirty_verts* in the temp VGs and the real deform VGs. Storage is
        left for end_live_stroke()."""
        from ..layer_storage.temp_vg_bridge import push_layer_to_temp_vgs

        obj = self.obj
        dirty = set(dirty_verts)
        touched = _live_stroke.get(obj.name)
        if touched is not None:
            touched |= dirty
        active_idx = self.active_layer_index
        state = self._get_live_state(active_idx)

        layer_subset_int = {v: layer_int[v] for v in dirty if v in layer_int}
        layer_subset = RustWeightEngine.map_layer_to_string(layer_subset_int, id_to_bone)
        if is_mask:
            state["active_mask"] = mask_dict
            mask_subset = {v: mask_dict[v] for v in dirty if v in mask_dict}
            with profile_section("core.facade.live.push_temp_vgs"):
                push_layer_to_temp_vgs(obj, None, mask_dict, dirty)
        else:
            active_mask = state["active_mask"]
            mask_subset = {v: active_mask[v] for v in dirty if v in active_mask}
            with profile_section("core.facade.live.push_temp_vgs"):
                push_layer_to_temp_vgs(obj, layer_subset, None, dirty,
                                       live_cache=self._live_vg_cache()["temp"])
        self._composite_write_dirty(state, active_idx, layer_subset, mask_subset, dirty)

    def finish(self, *, color_only: bool = False, dirty_verts: set = None,
              active_layer_override: dict = None, mask_override: dict = None):
        """Reflatten layers to mesh vertex groups and request a viewport redraw.

        Args:
            color_only: When True, only the colour VBO is invalidated. Use for
                weight-paint strokes where mesh topology is unchanged.
            dirty_verts, active_layer_override, mask_override: accepted for
                caller compatibility; the flatten recomposites the whole mesh.
        """
        with profile_section("core.facade.finish.flatten"):
            self._storage.flatten_visible_layers_to_mesh(self._obj)
        with profile_section("core.facade.finish.mesh_update"):
            self._mesh.update()
            self._obj.update_tag()
        with profile_section("core.facade.finish.shader_invalidate"):
            self._shader_mgr.bump_deform_generation()
            # Bumps an object-level counter so overlay_color's multi-color-preview
            # draw callback can detect weight changes without importing from core.
            self._obj["__ssp_deform_gen"] = self._obj.get("__ssp_deform_gen", 0) + 1
            if color_only:
                self._shader_mgr.invalidate_color_only()
            else:
                self._shader_mgr.invalidate_and_redraw()

    def finish_color_only(self):
        self.finish(color_only=True)

    def write_active_layer(self, layer_str: dict, *, color_only: bool = True,
                           dirty_verts: set = None) -> None:
        """Write a string-keyed layer dict to ss_layer_N, then call finish().

        Handles orphan re-merge and zero-weight pruning via the underlying
        _write_active_layer_string path.

        Args:
            layer_str: {v_idx (int): {bone_name (str): weight (float)}}
            color_only: Passed to finish(). True when topology is unchanged
                (typical for weight-paint brush strokes).
            dirty_verts: passed to finish() and _write_active_layer_string().

        Call read_active_layer() first on this instance so the bone mapping
        cache is populated; otherwise the mapping is computed fresh.
        """
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
        self._write_active_layer_string(result_int, self._id_to_bone, None, is_mask_mode=False,
                                        dirty_verts=dirty_verts)
        self.finish(color_only=color_only, dirty_verts=dirty_verts)

    @contextmanager
    def mutate_active_layer(self, *, color_only: bool = True):
        """Read-modify-write transaction for the active layer's weight data.

        Yields the string-keyed dict from read_active_layer() for in-place
        mutation. On a clean exit, commits it via write_active_layer(). On an
        exception, nothing is written and the exception propagates.

        Args:
            color_only: Passed through to write_active_layer()/finish().
        """
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

    def _write_active_layer_string(self, layer_int: dict, id_to_bone: dict,
                                    mask_dict: dict = None, *,
                                    is_mask_mode: bool = False,
                                    dirty_verts: set = None,
                                    mask_default: float = 1.0):
        """Convert int-keyed layer data, merge orphans, prune zeros, and persist
        to ss_layer_N via storage.save_active(). Orphan re-merge and budget
        normalization are applied before writing.

        Args:
            dirty_verts: vertices this write actually touched; orphan weight
                is only restored for vertices outside it.
            mask_default: accepted for caller compatibility.
        """
        _profile_metrics = ProfilerService.is_enabled()
        _t0 = time.perf_counter() if _profile_metrics else None

        layer_str = RustWeightEngine.map_layer_to_string(layer_int, id_to_bone)
        orphan_entries = getattr(self, "_orphan_entries", {})
        if _profile_metrics:
            _t_convert = time.perf_counter()

        # real_vg_count is the single authoritative real/orphan boundary --
        # see LayerStorageService.real_vg_count()'s docstring (geometry.py).
        real_vg_count = self.storage.real_vg_count(self.obj)
        known_bone_names = self.storage.known_bone_names(id_to_bone, real_vg_count)

        # Re-merge is a safety net for callers whose layer_int doesn't cover
        # every vertex. It must NOT restore orphan data for a vertex this
        # write actually touched, or a freshly computed zero gets stomped
        # back to its old value every write.
        for v_idx, orphan_weights in orphan_entries.items():
            if dirty_verts is not None:
                if v_idx in dirty_verts:
                    continue
            elif v_idx in layer_int:
                continue
            layer_str.setdefault(v_idx, {}).update(orphan_weights)
        if _profile_metrics:
            _t_orphan_remerge = time.perf_counter()
        if not is_mask_mode:
            _normalize_orphan_budget(layer_str, known_bone_names)
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
        if not is_mask_mode and orphan_entries:
            _purge_zeroed_orphans_from_all_layers(self.storage, orphan_entries, layer_str)
        if _profile_metrics:
            _t_persist = time.perf_counter()
            _base = "core.facade._write_active_layer_string"
            _size = len(dirty_verts) if dirty_verts is not None else len(layer_str)
            ProfilerService.record(f"{_base}.convert", 1000 * (_t_convert - _t0), _size)
            ProfilerService.record(f"{_base}.orphan_remerge", 1000 * (_t_orphan_remerge - _t_convert), _size)
            ProfilerService.record(f"{_base}.normalize_prune", 1000 * (_t_normalize_prune - _t_orphan_remerge), _size)
            ProfilerService.record(f"{_base}.persist_save_active", 1000 * (_t_persist - _t_normalize_prune), _size)
            ProfilerService.record(f"{_base}.total", 1000 * (_t_persist - _t0), _size)
