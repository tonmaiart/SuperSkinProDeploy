
import bpy

from ..debug_logging.property_groups import SSPrefDebug


class SSPrefCustomizeUIState(bpy.types.PropertyGroup):
    """Ephemeral UI-only state — section collapse/expand. Never persisted to JSON."""
    single_ramp_expanded:   bpy.props.BoolProperty(default=True)
    multi_palette_expanded: bpy.props.BoolProperty(default=True)
    mask_ramp_expanded:     bpy.props.BoolProperty(default=True)
    apply_toolkit_expanded: bpy.props.BoolProperty(default=False)


class SSPrefRoot(bpy.types.PropertyGroup):
    """Root PropertyGroup bound to WindowManager.superskin_prefs."""
    ui_state:  bpy.props.PointerProperty(type=SSPrefCustomizeUIState)
    debug:     bpy.props.PointerProperty(type=SSPrefDebug)



_classes = [
    SSPrefCustomizeUIState,
    SSPrefDebug,
    SSPrefRoot,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_prefs = bpy.props.PointerProperty(
        type=SSPrefRoot, options={'SKIP_SAVE'},
    )


def unregister():
    del bpy.types.WindowManager.superskin_prefs
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
