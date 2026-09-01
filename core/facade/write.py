"""WriteFacadeMixin — all state-mutation, storage-commit, and flatten operations.

Methods here may modify layer storage or trigger mesh vertex group commits.

finish() is implemented inline, passing self (CoreFacade) as the ctrl-compatible
argument to pipeline.flatten_to_mesh_edit(). CoreFacade's public proxy properties
(obj, mesh, storage, shader_mgr) satisfy the attribute contract pipeline expects.

write_active_layer_from_calc() accepts int-keyed layer data (direct Rust output)
and converts it via RustWeightEngine's data-bridge static methods without a
string->int->string round-trip.

write_active_layer() and write_layer_dict() / write_mask_dict() delegate to
_write_active_layer_string() for orphan-merge / temp-VG routing logic.

_normalize_orphan_budget() and _purge_zeroed_orphans_from_all_layers() are
module-level helpers used exclusively by _write_active_layer_string().

purge_zeroed_orphans_after_bake() is a separate module-level entry point (plus
a WriteFacadeMixin method wrapper of the same name) for callers that bypass
_write_active_layer_string() entirely and write straight to storage --
namely the three temp-VG bake-back sites (Exit Edit Mode, layer switch,
file-load recovery), which persist to ss_layer_N without ever populating the
facade's _orphan_entries read-cache.

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
from ..ui_controller import pipeline


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

    from ...core_subsystems.rust_weight_engine import RustWeightEngine as _RWE

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
            _RWE.prune_zero_bones(layer_dict)
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
    """Call immediately after a temp-VG bake-back writes new_layer_dict to
    ss_layer_N for the active layer, passing the layer's PRE-bake dict as
    old_layer_dict (read via storage.read_layer_dict(active_idx) before the
    write). Detects orphan bone names in old_layer_dict (any bone name not
    backed by a real, non-__ssp_* vertex group -- same rule as
    facade/read.py's _read_active_layer_int()) that are absent from
    new_layer_dict, and purges those from ss_layers_meta and every other
    layer's stored weight dict, mirroring what _write_active_layer_string()
    already does for its own write path.

    Bake-back sites (layer switch, Exit Edit Mode, file-load recovery) write
    straight to storage and never go through _write_active_layer_string(), so
    they need this explicit call to get equivalent orphan cleanup.

    new_layer_dict is expected to already be zero-pruned by the caller
    (read_temp_vgs_from_bm() / read_temp_vgs_to_layer() both prune before
    returning) -- this only diffs bone-name presence, it does not re-prune.
    """
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
        """See module-level purge_zeroed_orphans_after_bake() in this file."""
        purge_zeroed_orphans_after_bake(self.storage, self.obj, old_layer_dict, new_layer_dict)

    def set_selected_bones_pool(self, names: set) -> None:
        """Write the multi-select pool to the correct target for the current
        mode. Companion to ReadFacadeMixin.get_selected_bones_pool().

        Outside Edit Mode: writes storage.selected_names directly (memfile
        undo already handles this correctly there). In Edit Mode: splits
        real bones (write_pool_names_bm(), undo-safe) from orphan bones
        (storage.selected_orphan_names, same pre-existing undo gap as
        before this redesign, isolated to the rare orphan case).
        """
        from ..layer_storage.temp_vg_bridge import has_temp_vgs, write_pool_names_bm
        storage = self.obj.superskin_storage
        if self.obj.mode == 'EDIT' and has_temp_vgs(self.obj):
            real_name_to_idx = {vg.name: vg.index for vg in self.obj.vertex_groups
                                 if not vg.name.startswith("__ssp_")}
            real_names = {n for n in names if n in real_name_to_idx}
            orphan_names = names - real_names
            write_pool_names_bm(self.obj, self.mesh, real_names)
            storage.selected_orphan_names = (
                f",{','.join(sorted(orphan_names))}," if orphan_names else ""
            )
            return
        storage.selected_names = f",{','.join(sorted(names))}," if names else ","

    def write_layer_dict(self, layer_dict: dict):
        """Commit a nested weight dict straight to ss_layer_N.

        Not mode-aware. In Edit Mode with __ssp_* temp VGs present, this
        write is invisible to the live BMesh state and gets clobbered by the
        Exit-Edit-Mode bake-back (see docs/bug-history/0019). Use
        write_active_layer() instead for any code path reachable while the
        mesh may be in Edit Mode.
        """
        from ..layer_storage.temp_vg_bridge import has_temp_vgs
        if self.obj.mode == 'EDIT' and has_temp_vgs(self.obj):
            DebugLogService.log(
                "core_pipeline",
                "write_layer_dict(): called in EDIT mode with temp VGs present -- "
                "this writes ss_layer_N directly, bypassing the temp VG bridge. "
                "The write will be overwritten on Exit Edit Mode / layer switch. "
                "Use write_active_layer() instead.",
            )
        self.storage.write_layer_dict(self.active_layer_index, layer_dict)

    def write_mask_dict(self, mask_dict: dict):
        """Write the active layer's mask to the correct target for the current mode.

        Outside Edit Mode, writes straight to ss_mask_N via storage. In Edit
        Mode with __ssp_* temp VGs present, that direct write is invisible to
        the live BMesh state and gets clobbered by the Exit-Edit-Mode bake-back
        (see docs/bug-history/0020), so this instead re-reads the current
        (unchanged) bone-weight layer and routes both through
        _write_active_layer_string()/write_layer_to_temp_vgs_bm() together,
        matching the pattern already used by weight_apply_feature.py's mask ops.
        """
        from ..layer_storage.temp_vg_bridge import has_temp_vgs

        if self.obj.mode == 'EDIT' and has_temp_vgs(self.obj):
            layer_str = self.read_active_layer()
            result_int = {
                v_idx: {self._bone_to_id[b]: w for b, w in weights.items() if b in self._bone_to_id}
                for v_idx, weights in layer_str.items()
            }
            self._write_active_layer_string(result_int, self._id_to_bone, mask_dict, is_mask_mode=True)
            return

        self.storage.write_mask_dict(self.active_layer_index, mask_dict)

    def finish(self, *, color_only: bool = False, dirty_verts: set = None,
              active_layer_override: dict = None, mask_override: dict = None):
        """Reflatten layers to mesh vertex groups and request a viewport redraw.

        Routes through pipeline.flatten_to_mesh_edit() when in EDIT mode,
        passing self (the facade) as the ctrl-compatible object. The facade's
        public proxy properties (obj, mesh, storage, shader_mgr) satisfy the
        attribute contract that pipeline functions expect.

        Args:
            color_only: When True, only the colour VBO is invalidated. Use for
                weight-paint strokes where mesh topology is unchanged.
            dirty_verts: forwarded to flatten_to_mesh_edit() (EDIT mode only)
                to restrict its post-composite BMesh write loop to a known
                vertex subset. `None` (default) preserves full-mesh behavior.
                See flatten_to_mesh_edit()'s docstring for the correctness
                assumption this relies on.
            active_layer_override, mask_override: forwarded to
                flatten_to_mesh_edit() to skip its BMesh read entirely. See
                that function's docstring for the correctness contract.
        """
        if self._obj.mode == 'EDIT':
            pipeline.flatten_to_mesh_edit(self, dirty_verts=dirty_verts,
                                          active_layer_override=active_layer_override,
                                          mask_override=mask_override)
        else:
            self._storage.flatten_visible_layers_to_mesh(self._obj)
        self._mesh.update()
        self._obj.update_tag()
        self._shader_mgr.bump_deform_generation()
        # Bump an object-level counter so the overlay_color multi-color-
        # preview draw callback can detect weight changes without importing
        # from core -- mirrors core/ui_controller/pipeline.py's finish(),
        # which this method otherwise duplicates inline. Missing here meant
        # any write routed through CoreFacade.finish() (write_active_layer(),
        # write_active_layer_from_calc(), mutate_active_layer()) never
        # invalidated multi_color_draw.py's _make_base_key() cache, so Multi
        # Color Preview kept showing stale colors after e.g. Auto Assign
        # Weight until something else happened to bump it. See
        # docs/bug-history/0031.
        self._obj["__ssp_deform_gen"] = self._obj.get("__ssp_deform_gen", 0) + 1
        from ...core_subsystems.debug_logging import DebugLogService as _AdhocDLS
        _AdhocDLS.log(
            "adhoc:mirror_multicolor",
            f"finish(): obj={self._obj.name!r} mode={self._obj.mode} "
            f"color_only={color_only} new_deform_gen={self._obj['__ssp_deform_gen']}",
        )
        if color_only:
            self._shader_mgr.invalidate_color_only()
        else:
            self._shader_mgr.invalidate_and_redraw()

    def finish_color_only(self):
        self.finish(color_only=True)

    def write_active_layer(self, layer_str: dict, *, color_only: bool = True,
                           dirty_verts: set = None) -> None:
        """Write a string-keyed layer dict to the correct target for the current
        mode, then call finish().

        In Edit Mode, writes to __ssp_* BMesh temp VGs. Outside Edit Mode,
        writes to ss_layer_N. Handles orphan re-merge and zero-weight pruning
        via the underlying _write_active_layer_string path.

        Args:
            layer_str: {v_idx (int): {bone_name (str): weight (float)}}
            color_only: Passed to finish(). True when topology is unchanged
                (typical for weight-paint brush strokes).
            dirty_verts: passed to finish(). See flatten_to_mesh_edit()'s
                docstring for what this restricts and the correctness
                assumption it relies on.

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
            # Gated on ProfilerService (the real, default-off toggle), not
            # DebugLogService.is_enabled() -- that now always returns True
            # unconditionally (see its own docstring), so this print()+format
            # call used to fire on every write_active_layer() call in
            # production -- every Mirror, Auto Block, Clipboard paste,
            # weight_transfer, and weight_apply Object-Mode write. See
            # superskinpro-core-optimize skill session notes for the same
            # fix already applied to pipeline.py/write.py's EDIT breakdown logs.
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
        mutation. On a clean exit, commits it via write_active_layer() (which
        performs the mode-aware temp-VG/ss_layer_N write and calls finish()).
        On an exception, nothing is written and the exception propagates.

        This is a pure composition of read_active_layer() + write_active_layer()
        — it exists to make the read-before-write ordering (required so the
        bone mapping and orphan-entry caches are populated before the commit)
        structurally hard to get wrong, not to introduce new write logic.

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
                                    dirty_verts: set = None):
        """Convert int-keyed layer data, merge orphans, prune zeros, and persist.

        Routes to __ssp_* BMesh temp VGs in EDIT mode; otherwise writes to
        ss_layer_N via storage.save_active(). Orphan re-merge and budget
        normalization are applied before writing.

        Args:
            dirty_verts: passed to write_layer_to_temp_vgs_bm() to restrict
                its BMesh sync loops to a known vertex subset. `None`
                (default) preserves full-mesh behavior -- see that
                function's docstring for the correctness assumption.
        """
        from ..layer_storage.temp_vg_bridge import has_temp_vgs, write_layer_to_temp_vgs_bm

        # Previously uninstrumented -- write_active_layer_from_calc() (the
        # int-keyed hot path) has had a full phase breakdown all along, but
        # this string-keyed sibling (used by write_active_layer(), so every
        # Mirror/Auto Block/Clipboard/weight_transfer write, plus weight_apply's
        # mask and Object-Mode branches) had none, so its cost was invisible
        # in every profiler capture -- it only ever showed up as an unexplained
        # gap between a caller's own outer timer and whatever inner markers
        # happened to be nested underneath (e.g. auto_block.write vs.
        # flatten_to_mesh_edit.total). Same _profile_metrics-gated pattern as
        # everywhere else in this file.
        _profile_metrics = ProfilerService.is_enabled()
        _t0 = time.perf_counter() if _profile_metrics else None

        # Scope the int->string conversion to dirty_verts when this write will
        # land on the EDIT+temp-VG path below, which only ever reads layer_str
        # entries for vertices in dirty_verts (write_layer_to_temp_vgs_bm's own
        # dirty_verts restriction) -- mirrors the identical layer_int_for_convert
        # lever already used in write_active_layer_from_calc() above. The
        # Object-Mode / no-temp-VG fallthrough below still needs the complete
        # layer (it persists to ss_layer_N, which must represent every vertex),
        # so that path keeps layer_int unscoped exactly as before. Safe for the
        # orphan re-merge loop below: it already tolerates layer_str not
        # covering every vertex (that's the whole point of the re-merge), it
        # just adds entries for whichever orphan-carrying vertices need
        # restoring, regardless of layer_str's starting size.
        _use_temp_vg_path = self.obj.mode == 'EDIT' and has_temp_vgs(self.obj)
        if dirty_verts is not None and _use_temp_vg_path:
            layer_int_for_convert = {v: layer_int[v] for v in dirty_verts if v in layer_int}
        else:
            layer_int_for_convert = layer_int
        layer_str = RustWeightEngine.map_layer_to_string(layer_int_for_convert, id_to_bone)
        orphan_entries = getattr(self, "_orphan_entries", {})
        if _profile_metrics:
            _t_convert = time.perf_counter()

        # real_vg_count is the single authoritative real/orphan boundary --
        # see LayerStorageService.real_vg_count()'s docstring (geometry.py)
        # for why this must never be re-derived from len(self.obj.vertex_groups).
        real_vg_count = self.storage.real_vg_count(self.obj)
        known_bone_names = self.storage.known_bone_names(id_to_bone, real_vg_count)

        # Re-merge is a safety net for callers whose layer_int doesn't cover
        # every vertex (so a vertex missing from it doesn't silently lose
        # its orphan weight here) -- it must NOT restore orphan data for a
        # vertex this write actually touched, or a freshly computed zero
        # (a real bone painted up over the orphan, or the orphan itself
        # being scaled/added to directly as the active bone) gets stomped
        # back to its old value every single write, making the orphan
        # channel unkillable regardless of what the weight tool computed.
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

        if _use_temp_vg_path:
            # TEMP ROLLBACK (investigating a mask-smooth correctness
            # report -- a mask value of 0 snapping straight to 1 from a
            # tiny intensity): reverted to sync_mesh's default (True) here
            # as a precaution while the root cause is still unconfirmed.
            # Both callers reaching this branch (write_active_layer(),
            # and apply_action()'s is_mask branch calling
            # _write_active_layer_string() directly) still call
            # self.finish() immediately afterward regardless, so this is
            # a pure safety reversion, not a confirmed fix -- see
            # write_layer_to_temp_vgs_bm()'s sync_mesh docstring for the
            # (currently unverified against this bug) original reasoning.
            write_layer_to_temp_vgs_bm(
                self.obj, self.mesh, layer_str, id_to_bone, mask_dict,
                dirty_verts=dirty_verts,
            )
            if not is_mask_mode and orphan_entries:
                _purge_zeroed_orphans_from_all_layers(self.storage, orphan_entries, layer_str)
            if _profile_metrics:
                _t_persist = time.perf_counter()
                _base = "core.facade._write_active_layer_string"
                _size = len(dirty_verts) if dirty_verts is not None else len(layer_str)
                ProfilerService.record(f"{_base}.convert", 1000 * (_t_convert - _t0), _size)
                ProfilerService.record(f"{_base}.orphan_remerge", 1000 * (_t_orphan_remerge - _t_convert), _size)
                ProfilerService.record(f"{_base}.normalize_prune", 1000 * (_t_normalize_prune - _t_orphan_remerge), _size)
                ProfilerService.record(f"{_base}.persist_temp_vg", 1000 * (_t_persist - _t_normalize_prune), _size)
                ProfilerService.record(f"{_base}.total", 1000 * (_t_persist - _t0), _size)
            return

        self.storage.save_active(layer_str, mask_dict, is_mask_mode=is_mask_mode)
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

    def write_active_layer_from_calc(self, layer_int: dict, id_to_bone: dict, *,
                                     dirty_verts: set = None,
                                     mask_override: dict = None) -> None:
        """Write an integer-keyed layer dict (direct Rust output) to the correct
        target for the current mode.

        Converts int-keyed data to string format via data_bridge, prunes zero
        bones, then writes through the appropriate path.

        In EDIT mode this performs a dual-update:
          1. Temp VGs (``__ssp_*``) are updated so Blender's native Weight
             Overlay shows the active layer's weights in real-time.
          2. All visible layers are composited and written directly into the
             real deformation vertex groups on the edit-bmesh, so the Armature
             modifier recalculates viewport deform immediately.

        The caller does NOT need to call finish() afterwards — the viewport
        refresh is handled inline.

        Persistence of temp VG data back to ``ss_layer_N`` storage only occurs
        during a deliberate Save Weight operation (``_exit_edit_mode``).

        This method handles only weight writes (not mask writes). Use
        write_active_layer() for the full string-keyed path that includes orphan
        re-merging.

        Args:
            layer_int: {v_idx (int): {vg_index (int): weight (float)}} --
                the caller must always pass the COMPLETE, current
                active-layer dict here (see weight_apply_feature.py::
                apply_action()'s comment on why it must never be trimmed by
                the caller). Internally, when `dirty_verts` is given, this
                method converts only that vertex subset to a string-keyed
                dict for write_layer_to_temp_vgs_bm() and
                flatten_to_mesh_edit()'s `active_layer_override` -- both of
                those only ever read entries within `dirty_verts` on this
                path, so converting the whole layer for them would be pure
                waste. The persistence fallthrough (ss_layer_N, when not in
                EDIT mode with temp VGs) still converts the full `layer_int`,
                since it needs complete data.
            id_to_bone: {vg_index (int): bone_name (str)}
            dirty_verts: passed to flatten_to_mesh_edit() to restrict its
                post-composite BMesh write loop to a known vertex subset.
                `None` (default) preserves full-mesh behavior. See
                flatten_to_mesh_edit()'s docstring for the correctness
                assumption this relies on.
            mask_override: passed to flatten_to_mesh_edit() as its
                `mask_override` -- this method doesn't touch mask data
                itself, so the caller must supply the active layer's
                current mask (unchanged since this call doesn't write it)
                for the compositor to use in place of a BMesh mask read.
                `None` (default) means "no mask data for this layer", same
                as a full read finding none.
        """
        # Combined gate -- see pipeline.py's flatten_to_mesh_edit() for why
        # both consumers share one set of timestamps instead of each
        # computing (or risking a NameError on) their own.
        _profile_debug = DebugLogService.is_enabled("core_pipeline")
        _profile_metrics = ProfilerService.is_enabled()
        _profile = _profile_debug or _profile_metrics
        _t_start = time.perf_counter() if _profile else None

        if self._obj.mode == 'EDIT':
            from ..layer_storage.temp_vg_bridge import (
                has_temp_vgs, prepare_temp_vg_write, write_layer_to_temp_vgs_bm,
            )
            if has_temp_vgs(self._obj):
                # Only convert/prune the dirty-vertex subset here, not the
                # whole painted layer. write_layer_to_temp_vgs_bm()'s BMesh
                # sync loops (Phase 5) and flatten_to_mesh_edit()'s
                # compositor override both only ever read entries for
                # vertices in `dirty_verts` when it's given -- every entry
                # outside that set is provably never looked up by either
                # consumer on this path, so running map_layer_to_string()/
                # prune_zero_bones() over the full active layer (which can
                # be the whole mesh) was pure waste on the gesture hot path.
                #
                # `layer_str` here is intentionally NOT the complete active
                # layer when dirty_verts is given -- do not repurpose it for
                # anything that needs full data (the ss_layer_N persistence
                # fallthrough below builds its own full conversion instead).
                if dirty_verts is not None:
                    layer_int_for_convert = {
                        v: layer_int[v] for v in dirty_verts if v in layer_int
                    }
                else:
                    layer_int_for_convert = layer_int

                # Rust's add/scale/smooth/sharpen only ever touch the known
                # (real-vertex-group) bone channels -- an orphan bone's
                # weight is carried through completely unchanged unless
                # something squeezes it down here, same as
                # _write_active_layer_string() already does for its own
                # write path via _normalize_orphan_budget(). Without this,
                # painting a real bone up to full weight on top of an
                # orphan never reduces the orphan's stored value at all,
                # so it can never reach zero and be purged.
                #
                # real_vg_count is the single authoritative real/orphan
                # boundary -- see LayerStorageService.real_vg_count()'s
                # docstring (geometry.py) for why this must never be
                # re-derived from len(self._obj.vertex_groups) directly.
                real_vg_count = self._storage.real_vg_count(self._obj)

                # int->string conversion + orphan-budget normalize + global
                # zero-bone prune used to be three separate full-mesh
                # pure-Python passes (map_layer_to_string, then
                # _normalize_orphan_budget, then _prune_zero_bones's own two
                # internal passes) -- profiled as this call's single biggest
                # cost on a full-mesh (Select All) gesture, on par with the
                # Rust compositor call itself.
                #
                # rust_normalize_prune_layer_int (preferred) does the
                # normalize+prune math in one rayon-parallel Rust pass but
                # deliberately stays int-keyed -- a follow-up profiling run
                # showed the earlier string-keyed rust_convert_normalize_prune_layer
                # still paid a real, avoidable tax (a String clone per
                # surviving entry in Rust, then a fresh Python str
                # allocation per entry via PyO3) that this plain
                # dict-lookup-based int->string step below does not: Python
                # reuses id_to_bone's existing str objects instead of
                # allocating new ones. See layer_convert.rs's
                # normalize_prune_layer_int doc comment for the full
                # correctness argument (real/orphan boundary by ID, f64
                # throughout, same strict w > 0.0 zero-prune epsilon) --
                # unchanged from the string-keyed version, only the output
                # shape differs.
                #
                # Three-tier hasattr fallback, same convention as
                # rust_composite_layers_mixed above: an un-rebuilt binary
                # keeps using whichever older path (string-keyed Rust, then
                # pure Python) it already has, byte-identical to before.
                rust = RustWeightEngine("write_active_layer_from_calc")
                if hasattr(rust.module, "rust_normalize_prune_layer_int"):
                    known_ids = set(id_to_bone.keys())
                    result_int = rust.call(
                        "rust_normalize_prune_layer_int",
                        layer_int_for_convert, known_ids, real_vg_count,
                    )
                    layer_str = {
                        v_idx: {id_to_bone[b_id]: w for b_id, w in weights.items()}
                        for v_idx, weights in result_int.items()
                    }
                elif hasattr(rust.module, "rust_convert_normalize_prune_layer"):
                    layer_str = rust.call(
                        "rust_convert_normalize_prune_layer",
                        layer_int_for_convert, id_to_bone, real_vg_count,
                    )
                else:
                    layer_str = RustWeightEngine.map_layer_to_string(layer_int_for_convert, id_to_bone)
                    known_bone_names = self._storage.known_bone_names(id_to_bone, real_vg_count)
                    _normalize_orphan_budget(layer_str, known_bone_names)
                    RustWeightEngine.prune_zero_bones(layer_str)

                if _profile:
                    _t_convert = time.perf_counter()

                _t0 = time.perf_counter() if _profile else None

                if dirty_verts is not None:
                    # Merged single-pass path (the weight-apply gesture hot
                    # path always has a real dirty_verts here). Compute the
                    # temp-VG write data WITHOUT touching the BMesh at all
                    # (prepare_temp_vg_write() only does VG-list bookkeeping
                    # + dict building, no bm.verts scan), then hand it to
                    # flatten_to_mesh_edit() so it folds the actual per-vertex
                    # __ssp_* write into its own single combined BMesh pass
                    # (temp-VG write + real-VG old-state read + real-VG
                    # new-state write, one bm.verts[v_idx] visit per dirty
                    # vertex) instead of running as a separate
                    # write_layer_to_temp_vgs_bm() scan beforehand -- see that
                    # function's temp_vg_new_weights/temp_vg_all_ssp_indices
                    # docstring for the full correctness argument. This
                    # replaces what used to be three separate bm.verts scans
                    # (this step's own write, flatten_to_mesh_edit()'s
                    # old_state_scan, and its final write loop) with one.
                    temp_vg_new_weights, temp_vg_all_ssp_indices = prepare_temp_vg_write(
                        self._obj, layer_str, id_to_bone, dirty_verts,
                    )

                    if _profile:
                        _t_tempvg = time.perf_counter()

                    # 2. Composite all visible layers and write the final evaluated
                    #    result directly into the real deformation VGs on the edit-
                    #    bmesh, along with this step's temp-VG write, in one combined
                    #    pass. This triggers an immediate Armature modifier update
                    #    so the viewport deform reflects the weight change.
                    #    active_layer_override=layer_str reuses the dict this
                    #    method already built above instead of having
                    #    flatten_to_mesh_edit() re-read the identical data back
                    #    out of the BMesh via a full per-vertex scan.
                    pipeline.flatten_to_mesh_edit(self, dirty_verts=dirty_verts,
                                                  active_layer_override=layer_str,
                                                  mask_override=mask_override,
                                                  temp_vg_new_weights=temp_vg_new_weights,
                                                  temp_vg_all_ssp_indices=temp_vg_all_ssp_indices)
                else:
                    # No dirty_verts (full-mesh caller) -- the merge above is
                    # scoped to looping over a known dirty_verts set, so this
                    # case keeps the original, unmerged two-call shape exactly.
                    # 1. Update temp VGs so the native Weight Overlay sees the
                    #    active layer's weights in real-time.
                    # sync_mesh=False: step 2 below (flatten_to_mesh_edit()) always
                    # runs immediately after in this same call, and already ends
                    # with its own bmesh.update_edit_mesh() covering both this
                    # step's temp-VG writes and its own real-VG writes -- see
                    # write_layer_to_temp_vgs_bm()'s sync_mesh docstring.
                    write_layer_to_temp_vgs_bm(self._obj, self._mesh, layer_str, id_to_bone,
                                               dirty_verts=dirty_verts, sync_mesh=False)

                    if _profile:
                        _t_tempvg = time.perf_counter()

                    pipeline.flatten_to_mesh_edit(self, dirty_verts=dirty_verts,
                                                  active_layer_override=layer_str,
                                                  mask_override=mask_override)

                if _profile:
                    _t_flatten = time.perf_counter()

                # 3. Force the evaluated mesh and all 3D viewports to refresh.
                import bpy as _bpy
                self._obj.update_from_editmode()

                if _profile:
                    _t_update_editmode = time.perf_counter()

                self._shader_mgr.bump_deform_generation()
                self._obj["__ssp_deform_gen"] = self._obj.get("__ssp_deform_gen", 0) + 1
                for window in _bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()

                if _profile:
                    _t_redraw = time.perf_counter()
                    if _profile_metrics:
                        # Gated on _profile_metrics (the real ProfilerService
                        # toggle), not _profile_debug -- DebugLogService.
                        # is_enabled() now always returns True unconditionally
                        # (see its own docstring: capture is always-on so the
                        # Debug Console buffer stays complete regardless of
                        # which category checkboxes are ticked), so
                        # _profile_debug can no longer distinguish "user wants
                        # this" from "always". Gating this specific block on
                        # it made the f-string format + print() syscall below
                        # fire on EVERY write_active_layer_from_calc() call in
                        # production, permanently, landing entirely in the
                        # untracked gap between this function's own
                        # "total_incl_convert" (_t_redraw - _t_start, ending
                        # right above) and the caller's outer timer around
                        # this whole call -- see superskinpro-core-optimize
                        # skill session notes.
                        #
                        # Isolates update_from_editmode()'s cost specifically --
                        # it runs right after flatten_to_mesh_edit()'s own
                        # bmesh.update_edit_mesh(), and the two may be doing
                        # overlapping BMesh->Mesh sync work. Compare
                        # update_from_editmode's ms against the others to see
                        # if it's worth investigating removal (carefully --
                        # the GPU visualizer may depend on it for immediate
                        # deformed-coordinate reads).
                        #
                        # When dirty_verts is not None (the merged path above),
                        # "write_layer_to_temp_vgs_bm" here measures only
                        # prepare_temp_vg_write() (cheap, no BMesh scan) -- the
                        # actual per-vertex temp-VG write cost moved into
                        # "flatten_to_mesh_edit"'s own timing (see that
                        # function's core_pipeline breakdown log, "bmesh_write"
                        # phase, for where it now shows up).
                        DebugLogService.log(
                            "core_pipeline",
                            f"write_active_layer_from_calc() EDIT breakdown: "
                            f"map_to_string+prune={1000 * (_t_convert - _t_start):.2f}ms "
                            f"write_layer_to_temp_vgs_bm={1000 * (_t_tempvg - _t0):.2f}ms "
                            f"flatten_to_mesh_edit={1000 * (_t_flatten - _t_tempvg):.2f}ms "
                            f"update_from_editmode={1000 * (_t_update_editmode - _t_flatten):.2f}ms "
                            f"redraw_tagging={1000 * (_t_redraw - _t_update_editmode):.2f}ms "
                            f"total_incl_convert={1000 * (_t_redraw - _t_start):.2f}ms",
                        )
                        _base = "core.facade.write_active_layer_from_calc"
                        # Size hint = vertex count this call actually scoped
                        # its work to (dirty_verts when given, else the
                        # complete active-layer dict this method always
                        # receives).
                        _size = len(dirty_verts) if dirty_verts is not None else len(layer_int)
                        ProfilerService.record(f"{_base}.map_to_string_prune", 1000 * (_t_convert - _t_start), _size)
                        ProfilerService.record(f"{_base}.write_layer_to_temp_vgs_bm", 1000 * (_t_tempvg - _t0), _size)
                        ProfilerService.record(f"{_base}.flatten_to_mesh_edit", 1000 * (_t_flatten - _t_tempvg), _size)
                        ProfilerService.record(f"{_base}.update_from_editmode", 1000 * (_t_update_editmode - _t_flatten), _size)
                        ProfilerService.record(f"{_base}.redraw_tagging", 1000 * (_t_redraw - _t_update_editmode), _size)
                        ProfilerService.record(f"{_base}.total_incl_convert", 1000 * (_t_redraw - _t_start), _size)
                return

        # Fallthrough: Object Mode, or EDIT mode without temp VGs. Neither
        # of the dirty_verts-trimmed conversions above ran on this path, so
        # build the FULL conversion here -- this writes straight to
        # ss_layer_N and needs the complete active layer.
        layer_str = RustWeightEngine.map_layer_to_string(layer_int, id_to_bone)
        RustWeightEngine.prune_zero_bones(layer_str)
        self._storage.save_active(layer_str, is_mask_mode=self.is_mask_context())
