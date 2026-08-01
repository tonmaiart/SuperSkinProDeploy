"""CoreFacade — unified public API surface for all Extra Domains.

Extra Domains MUST NOT import from core/ sub-modules directly.
They import CoreFacade from here and call only these methods.

ST_STRICT note: this package IS part of core/ but its purpose is to
expose a stable interface outward. It may import from core/ freely.
It must never import from any domain package (clipboard/, mirror/, etc.).

Split-backend layout:
    read.py        — ReadFacadeMixin  (read-only context harvesting,
                                       _read_active_layer_int, _locks_by_id)
    write.py       — WriteFacadeMixin (storage mutation and flatten,
                                       _write_active_layer_string,
                                       orphan budget helpers)
    visualizer.py  — VisualizerFacadeMixin (GPU shader / HUD toast)
"""
from importlib import reload

from . import read, write, visualizer

# Reload BEFORE binding the mixin classes below -- the exact same
# "Reload-Ordering Footgun" already documented and fixed in
# core_subsystems/profiler/__init__.py (and, this session, in six other
# core_subsystems packages that had the un-fixed anti-pattern:
# layer_compositor, debug_logging, context_selection_service,
# topology_cache_manager, license_gateway, rust_weight_engine). Binding
# ReadFacadeMixin/WriteFacadeMixin/VisualizerFacadeMixin BEFORE reload(_mod)
# runs would permanently capture the PRE-reload mixin classes: `class
# CoreFacade(ReadFacadeMixin, WriteFacadeMixin, VisualizerFacadeMixin)`
# further below would then be built from stale mixins on every single F3
# Reload Scripts after the first, silently keeping every method defined in
# read.py/write.py/visualizer.py one generation behind whatever's on disk --
# with no error, since the stale class is perfectly valid Python, just wrong.
for _mod in (read, write, visualizer):
    try:
        reload(_mod)
    except Exception:
        pass

from .read import ReadFacadeMixin
from .write import WriteFacadeMixin
from .visualizer import VisualizerFacadeMixin


# Short-TTL cache backing CoreFacade.is_system_activated() (Layer 1 of the
# license system, advisory-only per core_subsystems/license_gateway/README.md
# -- see that method's docstring for why caching it is safe). Module-level
# rather than a class attribute so a reload() of this module (F3 Reload
# Scripts) naturally resets it instead of leaving a stale cached value alive
# across reloads.
_activation_cache = {"value": None, "timestamp": 0.0}
_ACTIVATION_CACHE_TTL_SECONDS = 1.0


# ── CoreFacade ────────────────────────────────────────────────────────────────

class CoreFacade(ReadFacadeMixin, WriteFacadeMixin, VisualizerFacadeMixin):
    """Unified public API instantiated once per operator execution.

    CoreFacade is the sole ctrl type in the system. Operator and feature
    domain code interacts with core exclusively through this class.
    Sub-modules (layer_crud, pipeline, operations) accept CoreFacade as ctrl.
    """

    def __init__(self, context):
        from ..shaders.shader_manager import ShaderManager
        from ..layer_storage.storage_service import LayerStorageService
        from ...core_subsystems.layer_compositor import LayerCompositor

        if not self.is_system_activated():
            raise ValueError(
                "SuperSkinPro is not activated — enter your Pro license key in "
                "the 3D Viewport sidebar's Activation panel."
            )

        self.ctx = context
        self._ctx = context      # alias — ContextSelectionService / is_mask_context
        self.context = context   # alias — features/clipboard/logic.py uses ctrl.context.window_manager

        # shader_mgr first — lightweight, must always be available
        self.shader_mgr = ShaderManager()

        self.obj = context.active_object
        if not self.obj or self.obj.type != 'MESH':
            raise ValueError("No active mesh object")

        self.mesh = self.obj.data
        self.storage = LayerStorageService(self.mesh)
        self._layer_mgr = LayerCompositor  # classmethod-only class; assign the class itself

    # ── Private proxy properties (mixin compatibility aliases) ────────

    @property
    def _obj(self):
        return self.obj

    @property
    def _mesh(self):
        return self.mesh

    @property
    def _storage(self):
        return self.storage

    @property
    def _shader_mgr(self):
        return self.shader_mgr

    # ── active_layer_index property ───────────────────────────────────

    @property
    def active_layer_index(self) -> int:
        return self.storage.get_active_layer_index()

    @active_layer_index.setter
    def active_layer_index(self, value: int):
        self.storage.set_active_layer_index(value)

    def active_layer_name(self) -> str:
        return self._layer_mgr.active_layer_name(
            self.storage.read_meta_list(), self.active_layer_index
        )

    # ── Internal helpers ──────────────────────────────────────────────

    def _idx_to_name(self) -> dict:
        return {vg.index: vg.name for vg in self.obj.vertex_groups}

    def _active_bone_name(self) -> str:
        storage = self.obj.superskin_storage
        if storage.active_orphan_name:
            return storage.active_orphan_name
        idx = storage.last_clicked_index
        if 0 <= idx < len(self.obj.vertex_groups):
            return self.obj.vertex_groups[idx].name
        return ""

    def _active_vg_id(self) -> int | None:
        storage = self.obj.superskin_storage
        if storage.active_orphan_name:
            bone_to_id, _ = self.storage.get_unified_mapping(self.obj)
            return bone_to_id.get(storage.active_orphan_name)
        idx = storage.last_clicked_index
        if 0 <= idx < len(self.obj.vertex_groups):
            return idx
        return None

    # ── Pipeline wrappers ─────────────────────────────────────────────

    def _finish(self, *, color_only=False, dirty_verts=None):
        from ..ui_controller import pipeline
        return pipeline.finish(self, color_only=color_only, dirty_verts=dirty_verts)

    def _flatten_to_mesh(self):
        from ..ui_controller import pipeline
        return pipeline.flatten_to_mesh(self)

    def _flatten_to_mesh_edit(self, *, dirty_verts=None):
        from ..ui_controller import pipeline
        return pipeline.flatten_to_mesh_edit(self, dirty_verts=dirty_verts)

    def _save_current_layer_state(self):
        from ..ui_controller import pipeline
        return pipeline.save_current_layer_state(self)

    def _restore_layer_state(self):
        from ..ui_controller import pipeline
        return pipeline.restore_layer_state(self)

    def _is_mask_context(self):
        from ..ui_controller import pipeline
        return pipeline.is_mask_context(self)

    def heal_topology_if_needed(self):
        from ..ui_controller import pipeline
        return pipeline.heal_topology_if_needed(self)

    def has_pending_edit_mode_changes(self) -> bool:
        """True if the active object is in Edit Mode with unsaved __ssp_*
        temp VGs present -- i.e. weight edits exist that only "Save Weights"
        (or exiting Edit Mode through the normal gate) would bake to the
        permanent ss_layer_N storage. Callers that need to leave Edit Mode
        for an unrelated reason (e.g. switching to Pose Mode) should check
        this first rather than silently discarding or force-baking in-progress
        edits."""
        from ..layer_storage.temp_vg_bridge import has_temp_vgs
        return self.obj.mode == 'EDIT' and has_temp_vgs(self.obj)

    # ── Layer CRUD ────────────────────────────────────────────────────

    def create_layer(self, name: str) -> int:
        from ..ui_controller import layer_crud
        return layer_crud.create_layer(self, name)

    def remove_layer(self, index: int):
        from ..ui_controller import layer_crud
        return layer_crud.remove_layer(self, index)

    def move_layer(self, index: int, direction: int) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.move_layer(self, index, direction)

    def duplicate_layer(self, index: int) -> int:
        from ..ui_controller import layer_crud
        return layer_crud.duplicate_layer(self, index)

    def merge_selected_layers(self, selected_indices: list, target_index: int) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.merge_selected_layers(self, selected_indices, target_index)

    def toggle_visible(self, index: int):
        from ..ui_controller import layer_crud
        return layer_crud.toggle_visible(self, index)

    def rename_layer(self, index: int, new_name: str):
        from ..ui_controller import layer_crud
        return layer_crud.rename_layer(self, index, new_name)

    def layer_meta_list(self) -> list:
        from ..ui_controller import layer_crud
        return layer_crud.layer_meta_list(self)

    def get_bone_locks(self, layer_index: int = None) -> dict:
        from ..ui_controller import layer_crud
        return layer_crud.get_bone_locks(self, layer_index)

    def set_bone_locks(self, bone_locks: dict, layer_index: int = None):
        from ..ui_controller import layer_crud
        return layer_crud.set_bone_locks(self, bone_locks, layer_index)

    def apply_bone_locks(self):
        from ..ui_controller import layer_crud
        return layer_crud.apply_bone_locks(self)

    def get_selected_bones(self, layer_index: int = None) -> str:
        from ..ui_controller import layer_crud
        return layer_crud.get_selected_bones(self, layer_index)

    def set_selected_bones(self, selected_names: str, layer_index: int = None):
        from ..ui_controller import layer_crud
        return layer_crud.set_selected_bones(self, selected_names, layer_index)

    def apply_selected_bones(self):
        from ..ui_controller import layer_crud
        return layer_crud.apply_selected_bones(self)

    def get_active_bone_name(self, layer_index: int = None) -> str:
        from ..ui_controller import layer_crud
        return layer_crud.get_active_bone_name(self, layer_index)

    def set_active_bone_name(self, name: str, layer_index: int = None):
        from ..ui_controller import layer_crud
        return layer_crud.set_active_bone_name(self, name, layer_index)

    def apply_active_bone(self):
        from ..ui_controller import layer_crud
        return layer_crud.apply_active_bone(self)

    def clear_orphan_weight_preview(self):
        """Remove the temp VG that previews an orphan bone's composited
        weight via the native Weight Overlay, if one is present. Call when
        the Deform Bones list selection moves off an orphan row onto a real
        bone or the Mask row. See BoneIdentityService.preview_orphan_weight()."""
        from ..bone_identity import BoneIdentityService
        BoneIdentityService(self.ctx, obj=self.obj).clear_orphan_weight_preview()

    def enter_mask_editing_context(self, active_vg_idx: int = -1):
        from ..ui_controller import layer_crud
        return layer_crud.enter_mask_editing_context(self, active_vg_idx)

    def exit_mask_editing_context(self, active_vg_idx: int = -1):
        from ..ui_controller import layer_crud
        return layer_crud.exit_mask_editing_context(self, active_vg_idx)

    def get_active_layer_weights_for_display(self) -> dict:
        from ..ui_controller import layer_crud
        return layer_crud.get_active_layer_weights_for_display(self)

    def init_layer_system(self) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.init_layer_system(self)

    def remove_layer_system(self) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.remove_layer_system(self)

    def check_for_mask_gaps(self) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.check_for_mask_gaps(self)

    # ── Visualizer delegation ─────────────────────────────────────────

    @property
    def visualizer_mode(self) -> str:
        return None

    def set_visualizer_mode(self, mode_str):
        pass  # Served by Blender native overlay.

    def restore_visualizer_from_mask(self):
        pass  # Native overlay restores automatically via active_index.

    def refresh_visualizer(self):
        self.shader_mgr.invalidate_and_redraw()

    def refresh_visualizer_color_only(self):
        self.shader_mgr.invalidate_color_only()

    # ── get_ctrl escape hatch ─────────────────────────────────────────

    def get_ctrl(self):
        """CoreFacade IS the ctrl. Returns self."""
        return self

    # ── Existing CoreFacade public API ────────────────────────────────

    def mirror(self) -> None:
        from ..ui_controller.operations import mirror as _mirror
        _mirror(self)

    def normalize_weights(
        self,
        layer_dict: dict,
        vertex_index: int,
        active_vg_name: str,
    ) -> dict:
        from ...core_subsystems.context_selection_service import ContextSelectionService as _CSS
        return _CSS.normalize_weights(
            layer_dict=layer_dict,
            vertex_index=vertex_index,
            active_vg_name=active_vg_name,
            vg_names=[vg.name for vg in self.obj.vertex_groups],
            bone_locks=self.get_bone_locks(),
            is_mask=self._is_mask_context(),
        )

    def switch_to_layer(self, index: int, push_undo: bool = False) -> None:
        from ..ui_controller.layer_crud import switch_to_layer as _switch
        _switch(self, index, push_undo=push_undo)

    def add_vg_selected(self, obj, name: str) -> bool:
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC_sel
        return _LC_sel.add_vg_selected(obj, name)

    def remove_vg_selected(self, obj, name: str) -> bool:
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC_sel
        return _LC_sel.remove_vg_selected(obj, name)

    @classmethod
    def save_prefs(cls) -> None:
        from ...core_subsystems.preferences.preferences_service import PreferencesService
        PreferencesService.save_to_user_file()

    @classmethod
    def is_system_activated(cls) -> bool:
        """True if a valid Pro license is currently activated.

        Delegates to LicenseGateway.is_pro(), which always re-derives validity
        via the compiled Rust HMAC check rather than trusting a cached flag.
        Safe to call without an active mesh/context — unlike CoreFacade(context),
        this does not construct an instance.

        Short-TTL-cached (see ``_activation_cache`` below) -- this is Layer 1
        of the license system (core_subsystems/license_gateway/README.md),
        documented there as purely advisory UX (panel poll()/fast error
        message), never the real security boundary; actual enforcement
        (Layers 2/3) re-derives independently on every protected call
        regardless of what this method returns, so caching this result does
        not weaken anything. This method is called on every single
        CoreFacade(context) construction (see __init__ above) and on every
        redraw of the main panel (interface/panel_main.py), so the
        underlying Rust HMAC re-check -- measured at ~12-15ms per call --
        was making every operator call and every UI hover/click noticeably
        laggy once it started running unconditionally that often.
        """
        import time
        now = time.monotonic()
        if (_activation_cache["value"] is not None
                and (now - _activation_cache["timestamp"]) < _ACTIVATION_CACHE_TTL_SECONDS):
            return _activation_cache["value"]

        from ...core_subsystems.license_gateway import LicenseGateway
        result = LicenseGateway.is_pro()
        _activation_cache["value"] = result
        _activation_cache["timestamp"] = now
        return result

    @classmethod
    def invalidate_activation_cache(cls) -> None:
        """Force the next is_system_activated() call to re-derive instead of
        returning a cached result. Call this immediately after any operation
        that changes activation state (activate, reset) so the UI reflects
        the new state right away instead of waiting out the TTL."""
        _activation_cache["value"] = None

    @classmethod
    def is_editing_weights(cls) -> bool:
        """True while SuperSkinPro's own "Edit Layer Weight" mode is active.

        Cheap, context-only check -- reads
        ``window_manager.superskin_active_interface == 'SKINNING'``, the
        same flag `superskin.mw_enter_edit_mode` / `superskin.mw_exit_edit_mode`
        (features/controller/ops_scene_modes.py) set on entry/exit. Does not
        construct a CoreFacade instance (no Pro-activation or active-object
        requirement), so it is safe to call from an operator's poll() even
        before activation or with nothing selected -- intended for gating
        keymap shortcuts (bone picker, Multi Color Mode, etc.) so they only
        fire while genuinely inside the addon's own edit-weight context,
        not just because the active object happens to be a mesh in Blender's
        native Edit Mode.
        """
        import bpy
        wm = bpy.context.window_manager
        return getattr(wm, "superskin_active_interface", "LAYER") == 'SKINNING'

    @classmethod
    def get_rust_gateway(cls, tag: str):
        from ...core_subsystems.rust_weight_engine import RustWeightEngine
        return RustWeightEngine(tag)

    @classmethod
    def layer_to_coo(cls, layer_int: dict):
        """Flatten ``{v_idx: {bone_id: weight}}`` (already int-bone-ID keyed,
        no string names) into parallel COO arrays for a hot-path Rust
        calculator call (add/scale/smooth/sharpen) -- ``features/`` code must
        not import ``core_subsystems/`` directly (Import Invariant #3), so
        this is the sanctioned entry point to
        ``flat_array_bridge.int_layer_to_coo()``.

        Returns:
            ``(vert_ids, bone_ids, weights)`` -- see
            ``core_subsystems/rust_weight_engine/flat_array_bridge.py``.
        """
        from ...core_subsystems.rust_weight_engine.flat_array_bridge import int_layer_to_coo
        return int_layer_to_coo(layer_int)

    @classmethod
    def coo_to_layer(cls, vert_ids, bone_ids, weights) -> dict:
        """Inverse of :meth:`layer_to_coo` -- rebuilds ``{v_idx: {bone_id:
        weight}}`` from a hot-path Rust calculator's COO array result."""
        from ...core_subsystems.rust_weight_engine.flat_array_bridge import coo_to_int_layer
        return coo_to_int_layer(vert_ids, bone_ids, weights)

    @classmethod
    def debug_log(cls, category: str, message: str) -> None:
        """Print *message* if *category* is enabled in Preferences > Developer / Debug Tools.

        The only sanctioned way for features/ to reach DebugLogService --
        ST_PURE_BACKEND forbids importing core_subsystems/ directly from a
        feature domain. core/ files may import DebugLogService directly
        instead of going through this facade method.
        """
        from ...core_subsystems.debug_logging import DebugLogService
        DebugLogService.log(category, message)

    @classmethod
    def get_debug_categories(cls) -> tuple:
        """Return the fixed tuple of debug-log category strings.

        Safe to call without an active mesh/context -- does not construct an
        instance. Used by the ``debug_console`` feature domain to build its
        category filter and per-category enable checkboxes.
        """
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.CATEGORIES

    @classmethod
    def get_debug_logs(cls, category_filter: str | None = None, search: str = "") -> list:
        """Return buffered debug-log entries, optionally filtered.

        Safe to call without an active mesh/context. See
        ``DebugLogService.get_logs()`` for the filter semantics.
        """
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.get_logs(category_filter, search)

    @classmethod
    def clear_debug_logs(cls) -> None:
        """Discard all buffered debug-log entries.

        Safe to call without an active mesh/context.
        """
        from ...core_subsystems.debug_logging import DebugLogService
        DebugLogService.clear_logs()

    @classmethod
    def get_adhoc_debug_categories(cls) -> list:
        """Return the distinct "adhoc:"-prefixed categories currently buffered.

        Safe to call without an active mesh/context. Used by the
        ``debug_console`` feature domain to populate the Debug Deck dropdown
        with whatever ad hoc tags are currently present in the log buffer --
        no registration step needed, see CLAUDE.md's "Ad Hoc Debug Deck
        Exception".
        """
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.get_adhoc_categories()

    # ── Profiler (classmethods — no instance required) ────────────────

    @classmethod
    def profile_section(cls, key: str, size: int | None = None):
        """Return a context manager that times its body and records it under
        *key*, or a no-op timer (skips time.perf_counter() entirely) when the
        profiler is disabled.

        Args:
            key: Metric identifier, e.g. "weight_apply.add.rust_ffi".
            size: Optional input-size hint for this call (e.g.
                len(dirty_verts)) -- lets the recorded sample distinguish
                "cost scales with input, as expected" from "cost is flat
                regardless of input, a fixed per-call overhead worth
                chasing." `None` (default) when no single size number is
                meaningful for this call site.

        The only sanctioned way for features/ to reach ProfilerService --
        ST_PURE_BACKEND (Import Invariant #1) forbids importing
        core_subsystems/ directly from a feature domain. core/ files may
        import profile_section from core_subsystems.profiler.profiler_service
        directly instead of going through this facade method.
        """
        from ...core_subsystems.profiler.profiler_service import profile_section as _profile_section
        return _profile_section(key, size)

    @classmethod
    def is_profiler_enabled(cls) -> bool:
        """True if profiler capture is currently active.

        Safe to call without an active mesh/context -- does not construct an
        instance.
        """
        from ...core_subsystems.profiler import ProfilerService
        return ProfilerService.is_enabled()

    @classmethod
    def set_profiler_enabled(cls, value: bool) -> None:
        """Turn profiler capture on/off.

        Called from the ``profiler`` feature domain's Enabled checkbox
        ``update=`` callback. Safe to call without an active mesh/context.
        """
        from ...core_subsystems.profiler import ProfilerService
        ProfilerService.set_enabled(value)

    @classmethod
    def get_profiler_stats(cls) -> dict:
        """Return per-key summary stats for every recorded metric.

        ``{key: {"last_ms", "avg_ms", "min_ms", "max_ms", "call_count",
        "avg_size"}}`` -- see ``ProfilerService.get_stats()`` for the full
        field semantics (``call_count`` is all-time, not windowed;
        ``avg_size`` is `None` when no sample in the window carried a size
        hint).

        Safe to call without an active mesh/context.
        """
        from ...core_subsystems.profiler import ProfilerService
        return ProfilerService.get_stats()

    @classmethod
    def clear_profiler_metrics(cls) -> None:
        """Discard all recorded profiler samples.

        Safe to call without an active mesh/context.
        """
        from ...core_subsystems.profiler import ProfilerService
        ProfilerService.clear()

    @classmethod
    def export_profiler_metrics(cls) -> str:
        """Write all recorded profiler metrics to a timestamped JSON file
        under ``<addon_root>/profiler_records/``.

        Safe to call without an active mesh/context. Returns the absolute
        path of the written file.
        """
        from ...core_subsystems.profiler import ProfilerService
        return ProfilerService.export_to_file()

    @classmethod
    def get_dev_output_dir(cls, name: str) -> str:
        """Return ``<addon_root>/<name>/``, creating it if needed.

        The sanctioned way for a dev-only export feature (profiler, debug
        console, ...) to get a working-tree output folder without knowing
        or computing the addon root itself. *name* must be a bare folder
        name (e.g. ``"debug_console_records"``), never a path -- and must
        have a matching ``.gitignore`` entry, since these are
        working-tree-only artifacts never shipped or committed.

        Safe to call without an active mesh/context.
        """
        from ...core_subsystems.dev_records import DevRecordsService
        return DevRecordsService.get_dir(name)

    @classmethod
    def export_support_report(cls, rig_context: dict | None = None) -> str:
        """Write a sanitized, user-safe diagnostic report to a timestamped
        JSON file under ``<addon_root>/support_reports/``.

        Bundles a static environment snapshot (addon/Blender version, OS,
        best-effort GPU info), every buffered debug log entry except ad hoc
        (``adhoc:*``) dev-debug decks, and *rig_context* if supplied --
        never invented when the caller has no active mesh. Any
        home-directory username embedded in a string is redacted before
        writing.

        Safe to call without an active mesh/context or Pro activation --
        the whole point of this report is to still work when something is
        already broken.

        Args:
            rig_context: Optional dict of active-object-derived facts (e.g.
                vertex/bone counts) the caller collected from its own
                bpy.context access -- this method never reads bpy.context
                itself.

        Returns:
            Absolute path to the written file.
        """
        from ...core_subsystems.support_report import SupportReportService
        return SupportReportService.export_to_file(rig_context)

    @classmethod
    def get_flat_array_bridge(cls):
        from ...core_subsystems.rust_weight_engine import RustWeightEngine
        return RustWeightEngine

    @classmethod
    def get_clipboard_data_ops(cls):
        from ...core_subsystems.layer_compositor import data_operations
        return data_operations
