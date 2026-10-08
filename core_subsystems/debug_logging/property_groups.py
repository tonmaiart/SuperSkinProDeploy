
import bpy

from .debug_log_service import CATEGORIES


def _on_debug_changed(self, context):
    from ..preferences.preferences_service import PreferencesService
    PreferencesService.save_to_user_file()


class SSPrefDebug(bpy.types.PropertyGroup):
    """One BoolProperty per debug-log category -- console visibility, default True."""
    temp_vg: bpy.props.BoolProperty(
        name="Temp VG & Mode Transitions",
        description="Show Edit/Object Mode transitions, __ssp_* temp Vertex Group bake/restore entries in the debug console",
        default=True,
        update=_on_debug_changed,
    )
    core_pipeline: bpy.props.BoolProperty(
        name="Core Storage & Compositor",
        description="Show layer/mask read-write, composite flattening, ss_layer_N / ss_mask_N I/O entries in the debug console",
        default=True,
        update=_on_debug_changed,
    )
    rust_ffi: bpy.props.BoolProperty(
        name="Rust FFI Gateway",
        description="Show entries for data crossing the Python-Rust FFI boundary in the debug console",
        default=True,
        update=_on_debug_changed,
    )
    viewport_viz: bpy.props.BoolProperty(
        name="GPU Visualizer & Shaders",
        description="Show heatmap/HUD drawing, shader cache invalidation, deform generation bump entries in the debug console",
        default=True,
        update=_on_debug_changed,
    )
    bone_id: bpy.props.BoolProperty(
        name="Bone Identity & Orphans",
        description="Show bone lock/mapping resolution, orphan bone scanning/remapping entries in the debug console",
        default=True,
        update=_on_debug_changed,
    )
    feature_domains: bpy.props.BoolProperty(
        name="Feature Domains",
        description="Show Extra Domain execute() dispatch entries (weight_apply, mirror, clipboard, etc.) in the debug console",
        default=True,
        update=_on_debug_changed,
    )


assert set(SSPrefDebug.__annotations__.keys()) == set(CATEGORIES), (
    "SSPrefDebug fields must match DebugLogService.CATEGORIES exactly"
)
