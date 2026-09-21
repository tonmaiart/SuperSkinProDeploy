"""ReadFacadeMixin — all read-only context harvesting operations.

Methods here must not mutate storage, shader state, or mesh data.

Four methods call core_subsystems directly for pure read operations:
    get_selected_verts()         -> ContextSelectionService
    is_mask_context()            -> ContextSelectionService
    get_local_mapping()          -> TopologyCacheManager
    get_cached_mesh_neighbors()  -> TopologyCacheManager
"""

from ...core_subsystems.context_selection_service import ContextSelectionService
from ...core_subsystems.topology_cache_manager import TopologyCacheManager


class ReadFacadeMixin:
    """Mixin providing read-only access to layer, mesh, and bone state."""

    def _sync_paint_to_storage(self) -> None:
        """Weight Paint session: the temp VGs hold the live paint state, so
        storage is stale after any native brush stroke. Reads must pull it in
        first, or a following write pushes the stale copy back over the paint.
        Skipped mid addon-stroke, where storage is intentionally deferred."""
        from ..layer_storage.temp_vg_bridge import is_wp_session, pull_temp_to_storage
        from .write import _live_stroke
        if self.obj.name in _live_stroke or not is_wp_session(self.obj):
            return
        # The pull re-reads and diffs every vertex, and this facade's own
        # writes keep storage and temp VGs consistent, so once per facade
        # (one operator invocation) is enough.
        if getattr(self, "_paint_synced", False):
            return
        pull_temp_to_storage(self.obj, self.storage)
        self._paint_synced = True

    def get_active_layer_dict(self) -> dict:
        """Return active layer weight dict {v_idx: {bone_name: weight}}.

        Reads ss_layer_N, first pulling any pending Weight Paint strokes into
        it (see _sync_paint_to_storage())."""
        self._sync_paint_to_storage()
        return self.storage.read_active_layer_dict()

    def pull_paint_to_storage(self) -> bool:
        """Copy the live Weight Paint temp-VG state into ss_layer_N / ss_mask_N.

        Unlike _sync_paint_to_storage() this does not require the object to be
        in Weight Paint Mode, so an exit path can flush the last strokes after
        the mode has already changed. Returns True if anything was written."""
        from ..layer_storage.temp_vg_bridge import has_temp_vgs, pull_temp_to_storage
        if not has_temp_vgs(self.obj) or not self.storage.has_layer_system():
            return False
        return pull_temp_to_storage(self.obj, self.storage)

    def get_active_mask_dict(self) -> dict:
        """Return active mask dict {v_idx: float} from ss_mask_N, after
        pulling any pending Weight Paint strokes into it."""
        self._sync_paint_to_storage()
        return self.storage.read_active_mask_dict()

    def get_active_layer_index(self) -> int:
        return self.active_layer_index

    def get_meta_list(self) -> list:
        """Return layer metadata from ss_layers_meta storage."""
        return self.storage.read_meta_list()

    def get_flattened_mask_dict(self, layer_index: int) -> dict:
        """Return ``{v_idx: float}`` -- *layer_index*'s own mask, "cut" down
        to only the fraction that survives the real compositor's top-down
        alpha-blend against every visible layer stacked above it (i.e.
        what that layer's mask looks like after flattening), rather than
        its raw, uncut mask value. Delegates to
        ``LayerCompositor.get_layer_cut_mask()`` -- see that method's
        docstring for the exact formula, the ordering it expects, and its
        one known approximation versus the real Rust compositor engine
        (mask-only; does not replicate the engine's own weight-data
        presence gating).

        Unlike ``get_active_mask_dict()``, this works for ANY layer index,
        not just the active one. Always reads ``ss_mask_N`` storage via
        ``storage.read_mask_dict()`` and does not pull pending Weight Paint
        strokes first, unlike ``get_active_mask_dict()``.
        """
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC
        meta_list = self.storage.read_meta_list()
        num_verts = self.get_num_verts()
        mask_dicts_map = {
            l["index"]: self.storage.read_mask_dict(l["index"]) for l in meta_list
        }
        return _LC.get_layer_cut_mask(meta_list, mask_dicts_map, layer_index, num_verts)

    def get_selected_verts(self) -> list[int]:
        """Return selected vertex indices for the current editor mode.

        Delegates to ContextSelectionService.
        Result is cached on the facade instance for the lifetime of one
        operator execution (facade is per-operator, so caching is safe).
        """
        if hasattr(self, '_cached_sel_verts'):
            return self._cached_sel_verts
        result = ContextSelectionService.get_selected_verts(self.obj, self.mesh)
        self._cached_sel_verts = result
        return result

    def get_active_vg_id(self):
        return self._active_vg_id()

    def get_active_vg_name(self) -> str:
        idx = self.get_active_vg_id()
        if idx is None:
            return ""
        vg_list = self.obj.vertex_groups
        return vg_list[idx].name if 0 <= idx < len(vg_list) else ""

    def get_vertex_groups(self):
        return self.obj.vertex_groups

    def get_mesh(self):
        return self.mesh

    def get_obj(self):
        return self.obj

    def is_mask_context(self) -> bool:
        """Return True when the UI is in a mask-painting context."""
        return ContextSelectionService.is_mask_context(self._ctx.scene)

    def get_local_mapping(self) -> tuple[dict[str, int], dict[int, str]]:
        """Return (bone_to_id, id_to_bone) for real vertex groups.

        Delegates to TopologyCacheManager. Shares the class-level cache
        with any other caller using the manager.
        """
        return TopologyCacheManager.get_local_mapping(self.obj, self.storage)

    def get_bone_locks(self, layer_index: int = None) -> dict:
        from ..ui_controller import layer_crud
        return layer_crud.get_bone_locks(self, layer_index)

    def get_vertex_coordinates(self) -> list:
        return self.storage.get_vertex_coordinates()

    def get_num_verts(self) -> int:
        return len(self.mesh.vertices)

    def get_selected_bones_pool(self) -> set:
        """Return the multi-select pool (bone names), parsed from
        storage.selected_names."""
        storage = self.obj.superskin_storage
        return {n for n in storage.selected_names.split(",") if n}

    def get_selected_bones_pool_string(self) -> str:
        """get_selected_bones_pool() formatted as the comma-bounded string
        layer_crud.set_selected_bones() expects."""
        names = self.get_selected_bones_pool()
        return f",{','.join(sorted(names))}," if names else ","

    def read_active_layer(self) -> dict:
        """Read the active layer from ss_layer_N, first pulling any pending
        Weight Paint strokes into it.

        Returns {v_idx (int): {bone_name (str): weight (float)}}.
        Also caches the unified bone mapping for a paired write_active_layer()
        call on the same instance.
        """
        bone_to_id, id_to_bone = self.storage.get_unified_mapping(self.obj)
        self._bone_to_id = bone_to_id
        self._id_to_bone = id_to_bone
        layer_int = self._read_active_layer_int(bone_to_id)
        return {
            v_idx: {id_to_bone[b]: w for b, w in weights.items() if b in id_to_bone}
            for v_idx, weights in layer_int.items()
        }

    def get_unified_mapping(self) -> tuple:
        """Return (bone_to_id, id_to_bone) including synthetic orphan IDs.

        Reuses the mapping cached by read_active_layer() if already called on
        this instance. Use after read_active_layer() when int-keyed data is
        required for Rust FFI calls.
        """
        if hasattr(self, '_bone_to_id') and hasattr(self, '_id_to_bone'):
            return self._bone_to_id, self._id_to_bone
        bone_to_id, id_to_bone = self.storage.get_unified_mapping(self.obj)
        self._bone_to_id = bone_to_id
        self._id_to_bone = id_to_bone
        return bone_to_id, id_to_bone

    def get_locks_by_id(self) -> dict:
        """Return bone locks keyed by integer VG index (unified mapping).

        Use instead of get_bone_locks() when passing lock data to Rust
        functions that require int keys.
        """
        return self._locks_by_id()

    def get_deform_bone_ids(self) -> set:
        """Return the subset of get_unified_mapping()'s bone IDs whose
        matching armature bone has `use_deform` enabled.

        This is the same filter interface/utils/utils.py's
        `_get_display_order_impl()` uses to decide which vertex groups the
        Deform Bones list shows as rows -- a vertex group present in the
        unified mapping but absent from this set is typically a leftover
        from an earlier rig version, or belongs to a control/helper bone
        that was never flagged as a deformer, and is therefore invisible in
        that list even though get_unified_mapping()/get_locks_by_id() still
        include it.

        Purely additive: get_unified_mapping()/get_locks_by_id() are
        unchanged and keep including every non-temp vertex group as before,
        so existing callers are unaffected. Only code that explicitly wants
        the narrower, Deform-Bones-list-consistent set should call this.

        Returns every unified-mapping bone ID unchanged if the mesh has no
        Armature modifier (nothing to filter against).
        """
        bone_to_id, _ = self.get_unified_mapping()
        arm_obj = next(
            (m.object for m in self.obj.modifiers if m.type == 'ARMATURE' and m.object), None,
        )
        if arm_obj is None:
            return set(bone_to_id.values())
        deform_bone_names = {b.name for b in arm_obj.data.bones if b.use_deform}
        return {bone_id for name, bone_id in bone_to_id.items() if name in deform_bone_names}

    def get_cached_mesh_neighbors(self) -> dict[int, list[int]]:
        """Return vertex-neighbor topology map for Rust smooth/sharpen ops.

        Delegates to TopologyCacheManager. Shares the class-level cache
        with any other caller using the manager.
        """
        return TopologyCacheManager.get_cached_mesh_neighbors(self.mesh, self.storage)

    def _read_active_layer_int(self, bone_to_id: dict) -> dict:
        """Read the active layer as an int-keyed dict and cache orphan entries.

        Reads from ss_layer_N storage. Populates self._orphan_entries
        with any weight entries whose bone name is absent from the current
        vertex group list, so _write_active_layer_string() can re-merge them.

        Orphan status is checked against REAL vertex_groups names, not
        *bone_to_id* -- since read_active_layer() always passes the UNIFIED
        mapping here (get_unified_mapping(), which assigns every orphan a
        synthetic ID precisely so weight ops can target them), checking
        "b not in bone_to_id" would never be true for an orphan that's
        already tracked in superskin_bones_collection -- self._orphan_entries
        would end up permanently empty, silently skipping
        _purge_zeroed_orphans_from_all_layers() in
        write.py::_write_active_layer_string() (that call is gated on
        `orphan_entries` being truthy) even after an orphan's weight is
        correctly scaled to zero elsewhere in the same write. The Deform
        Bones list row would then never clear itself once weight is fully
        stolen from an orphan bone via a normal (unlocked) weight op.
        """
        from ...core_subsystems.rust_weight_engine import RustWeightEngine as _RWE_local

        self._sync_paint_to_storage()
        raw = self.storage.read_active_layer_dict()

        real_vg_names = {vg.name for vg in self.obj.vertex_groups
                         if not vg.name.startswith("__ssp_")}
        self._orphan_entries = {
            v_idx: {b: w for b, w in weights.items() if b not in real_vg_names}
            for v_idx, weights in raw.items()
            if any(b not in real_vg_names for b in weights)
        }
        return _RWE_local.map_layer_to_int(raw, bone_to_id)

    def _locks_by_id(self) -> dict:
        """Return bone locks keyed by unified VG index (int), for Rust FFI callers.

        Covers every bone in the unified mapping, defaulting to False (unlocked)
        for any bone absent from the per-layer bone_locks metadata. A layer with
        no bones ever explicitly locked has an empty bone_locks dict by design
        (see LayerCompositor.get_bone_locks) -- Rust smooth/sharpen build their
        "which bones to process" list by filtering this dict's own keys, so
        returning only the explicitly-present entries silently excluded every
        bone on any layer with an empty bone_locks dict, making smooth/sharpen
        a no-op on such layers. See docs/bug-history/0021.
        """
        name_locks = self._layer_mgr.get_bone_locks(
            self.storage.read_meta_list(), self.active_layer_index
        )
        bone_to_id, _ = self.storage.get_unified_mapping(self.obj)
        return {vg_id: name_locks.get(name, False) for name, vg_id in bone_to_id.items()}
