from importlib import reload

from . import read, write, visualizer

for _mod in (read, write, visualizer):
    try:
        reload(_mod)
    except Exception:
        pass

from .read import ReadFacadeMixin
from .write import WriteFacadeMixin
from .visualizer import VisualizerFacadeMixin
from ..layer_storage import temp_vg_bridge as _temp_vg_bridge
from ..viewport import tag_redraw_areas as _tag_redraw_areas



class CoreFacade(ReadFacadeMixin, WriteFacadeMixin, VisualizerFacadeMixin):
    """Unified public API instantiated once per operator execution.

    CoreFacade is the sole ctrl type in the system. Operator and feature
    domain code interacts with core exclusively through this class.
    Sub-modules (layer_crud, pipeline, operations) accept CoreFacade as ctrl.
    """

    TEMP_VG_PREFIX = _temp_vg_bridge.PREFIX
    SESSION_MARKER = _temp_vg_bridge.SESSION_MARKER
    DEFORM_GEN_KEY = _temp_vg_bridge.DEFORM_GEN_KEY

    @staticmethod
    def iter_vertex_group_weights(mesh, vert_indices=None):
        from ..layer_storage.bmesh_io import iter_group_weights
        return iter_group_weights(mesh, vert_indices)

    @staticmethod
    def deform_bone_names(obj) -> frozenset:
        from ..layer_storage.geometry import deform_bone_names
        return deform_bone_names(obj)

    @staticmethod
    def managed_vg_names(obj) -> frozenset:
        from ..layer_storage.geometry import managed_vg_names
        return managed_vg_names(obj)

    @staticmethod
    def temp_layer_index(obj, default: int = 0) -> int:
        return _temp_vg_bridge.temp_layer_index(obj, default)

    @staticmethod
    def tag_redraw_areas(area_types=frozenset({'VIEW_3D'}), window_manager=None) -> None:
        _tag_redraw_areas(area_types, window_manager)

    def __init__(self, context):
        from ..shaders.shader_manager import ShaderManager
        from ..layer_storage.storage_service import LayerStorageService
        from ...core_subsystems.layer_compositor import LayerCompositor

        self.ctx = context

        self.shader_mgr = ShaderManager()

        self.obj = context.active_object
        if not self.obj or self.obj.type != 'MESH':
            raise ValueError("No active mesh object")

        self.mesh = self.obj.data
        self.storage = LayerStorageService(self.mesh)
        self._layer_mgr = LayerCompositor


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


    @staticmethod
    def active_vg_name_of(obj) -> str:
        idx = obj.superskin_storage.last_clicked_index
        vgs = obj.vertex_groups
        if 0 <= idx < len(vgs) and not vgs[idx].name.startswith(_temp_vg_bridge.PREFIX):
            return vgs[idx].name
        return ""

    def _active_vg_id(self) -> int | None:
        idx = self.obj.superskin_storage.last_clicked_index
        if 0 <= idx < len(self.obj.vertex_groups):
            return idx
        return None


    def _flatten_to_mesh(self):
        from ..ui_controller import pipeline
        return pipeline.flatten_to_mesh(self)

    def _restore_layer_state(self):
        from ..ui_controller import pipeline
        return pipeline.restore_layer_state(self)

    def heal_topology_if_needed(self):
        from ..ui_controller import pipeline
        return pipeline.heal_topology_if_needed(self)

    def has_pending_edit_mode_changes(self) -> bool:
        return False


    def create_layer(self, name: str) -> int:
        from ..ui_controller import layer_crud
        return layer_crud.create_layer(self, name)

    def create_group(self, name: str) -> int:
        from ..ui_controller import layer_crud
        return layer_crud.create_group(self, name)

    def move_layers_to_group(self, selected_indices: list, target_group_id) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.move_layers_to_group(self, selected_indices, target_group_id)

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

    def toggle_group_collapsed(self, group_index: int):
        from ..ui_controller import layer_crud
        return layer_crud.toggle_group_collapsed(self, group_index)

    def get_layer_icon(self, index: int = None) -> str:
        from ..ui_controller import layer_crud
        return layer_crud.get_layer_icon(self, index)

    def set_layer_icon(self, index: int, icon: str):
        from ..ui_controller import layer_crud
        return layer_crud.set_layer_icon(self, index, icon)

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

    def get_active_bone_name(self) -> str:
        from ..ui_controller import layer_crud
        return layer_crud.get_active_bone_name(self)

    def set_active_bone_name(self, name: str):
        from ..ui_controller import layer_crud
        return layer_crud.set_active_bone_name(self, name)

    def apply_active_bone(self):
        from ..ui_controller import layer_crud
        return layer_crud.apply_active_bone(self)

    def drop_unmanaged_weights(self) -> int:
        if self.is_paint_session():
            self.pull_paint_to_storage()
        return self.storage.drop_unmanaged_all_layers()

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

    def migrate_layer_storage(self) -> int:
        if not self.storage.has_layer_system():
            return 0
        return self.storage.migrate_layer_blobs()

    def remove_layer_system(self) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.remove_layer_system(self)

    def check_for_mask_gaps(self) -> bool:
        from ..ui_controller import layer_crud
        return layer_crud.check_for_mask_gaps(self)


    def refresh_visualizer(self):
        self.shader_mgr.invalidate_and_redraw()

    def refresh_visualizer_color_only(self):
        self.shader_mgr.invalidate_color_only()


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
        managed = self.storage.managed_vg_names(self.obj)
        return _CSS.normalize_weights(
            layer_dict=layer_dict,
            vertex_index=vertex_index,
            active_vg_name=active_vg_name,
            vg_names=[vg.name for vg in self.obj.vertex_groups if vg.name in managed],
            bone_locks=self.get_bone_locks(),
            is_mask=self.is_mask_context(),
        )

    def switch_to_layer(self, index: int) -> None:
        from ..ui_controller.layer_crud import switch_to_layer as _switch
        _switch(self, index)

    def add_vg_selected(self, obj, name: str) -> bool:
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC_sel
        return _LC_sel.add_vg_selected(obj, name)

    def remove_vg_selected(self, obj, name: str) -> bool:
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC_sel
        return _LC_sel.remove_vg_selected(obj, name)

    def clear_all_selected(self, obj) -> None:
        from ...core_subsystems.layer_compositor import LayerCompositor as _LC_sel
        _LC_sel.clear_all_selected(obj)

    @classmethod
    def save_prefs(cls) -> None:
        from ...core_subsystems.preferences.preferences_service import PreferencesService
        PreferencesService.save_to_user_file()

    @classmethod
    def is_editing_weights(cls) -> bool:
        import bpy
        wm = bpy.context.window_manager
        return getattr(wm, "superskin_active_interface", "LAYER") == 'SKINNING'

    @classmethod
    def get_layer_mask_state(cls, obj, layer_index: int) -> str:
        from ..layer_storage.storage_service import LayerStorageService
        from ..layer_storage.temp_vg_bridge import get_layer_mask_state as _get_layer_mask_state
        storage = LayerStorageService(obj.data)
        return _get_layer_mask_state(obj, storage, layer_index)

    @classmethod
    def get_rust_gateway(cls, tag: str):
        from ...core_subsystems.rust_weight_engine import RustWeightEngine
        return RustWeightEngine(tag)

    @classmethod
    def layer_to_coo(cls, layer_int: dict):
        from ...core_subsystems.rust_weight_engine.flat_array_bridge import int_layer_to_coo
        return int_layer_to_coo(layer_int)

    @classmethod
    def coo_to_layer(cls, vert_ids, bone_ids, weights) -> dict:
        from ...core_subsystems.rust_weight_engine.flat_array_bridge import coo_to_int_layer
        return coo_to_int_layer(vert_ids, bone_ids, weights)

    @classmethod
    def debug_log(cls, category: str, message: str) -> None:
        from ...core_subsystems.debug_logging import DebugLogService
        DebugLogService.log(category, message)

    @classmethod
    def get_debug_categories(cls) -> tuple:
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.CATEGORIES

    @classmethod
    def get_debug_logs(cls, category_filter: str | None = None, search: str = "") -> list:
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.get_logs(category_filter, search)

    @classmethod
    def clear_debug_logs(cls) -> None:
        from ...core_subsystems.debug_logging import DebugLogService
        DebugLogService.clear_logs()

    @classmethod
    def is_debug_print_enabled(cls) -> bool:
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.is_print_enabled()

    @classmethod
    def set_debug_print_enabled(cls, value: bool) -> None:
        from ...core_subsystems.debug_logging import DebugLogService
        DebugLogService.set_print_enabled(value)

    @classmethod
    def get_adhoc_debug_categories(cls) -> list:
        from ...core_subsystems.debug_logging import DebugLogService
        return DebugLogService.get_adhoc_categories()


    @classmethod
    def request_hud_slot(
        cls,
        owner_id: str,
        text: str,
        *,
        slot: int,
        timeout: float | None = None,
        color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        icon_texture=None,
    ) -> None:
        from ..shaders.shader_manager import ShaderManager
        ShaderManager().request_hud_slot(
            owner_id, text, slot=slot, timeout=timeout, color=color, icon_texture=icon_texture
        )

    @classmethod
    def release_hud_slot(cls, owner_id: str) -> None:
        from ..shaders.shader_manager import ShaderManager
        ShaderManager().release_hud_slot(owner_id)

    @classmethod
    def clear_all_hud_slots(cls) -> None:
        from ..shaders.shader_manager import ShaderManager
        ShaderManager().clear_all_hud_slots()


    @classmethod
    def profile_section(cls, key: str, size: int | None = None):
        from ...core_subsystems.profiler.profiler_service import profile_section as _profile_section
        return _profile_section(key, size)

    @classmethod
    def is_profiler_enabled(cls) -> bool:
        from ...core_subsystems.profiler import ProfilerService
        return ProfilerService.is_enabled()

    @classmethod
    def set_profiler_enabled(cls, value: bool) -> None:
        from ...core_subsystems.profiler import ProfilerService
        ProfilerService.set_enabled(value)

    @classmethod
    def get_profiler_stats(cls) -> dict:
        from ...core_subsystems.profiler import ProfilerService
        return ProfilerService.get_stats()

    @classmethod
    def clear_profiler_metrics(cls) -> None:
        from ...core_subsystems.profiler import ProfilerService
        ProfilerService.clear()

    @classmethod
    def export_profiler_metrics(cls) -> str:
        from ...core_subsystems.profiler import ProfilerService
        return ProfilerService.export_to_file()

    @classmethod
    def get_dev_output_dir(cls, name: str) -> str:
        from ...core_subsystems.dev_records import DevRecordsService
        return DevRecordsService.get_dir(name)

    @classmethod
    def export_support_report(cls, rig_context: dict | None = None) -> str:
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
