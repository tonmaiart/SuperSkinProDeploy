
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from . import logic

_step_items_cache = []


def _get_step_enum_items(self, context):
    global _step_items_cache
    steps = logic.load_steps()
    if not steps:
        _step_items_cache = [("__none__", "No tutorial content", "")]
        return _step_items_cache
    _step_items_cache = [
        (logic.get_step_id(step, idx), str(step.get("title", f"Step {idx + 1}")), "")
        for idx, step in enumerate(steps)
    ]
    return _step_items_cache


class SSPrefBuiltinTutorial(bpy.types.PropertyGroup):
    active_step: bpy.props.EnumProperty(
        name="Tutorial Step",
        description="Step currently shown in the Quick Start Guide popup",
        items=_get_step_enum_items,
    )


class BuiltinTutorialFeature(UnifiedFeatureExtension):
    """Unified extension for the Built-in Tutorial domain."""


    domain_id = "builtin_tutorial"
    actions = []
    section_title = "Quick Start Guide"
    draw_tab = "PREFERENCE"


    def execute(self, action: str, context, core_facade) -> dict:
        return {"status": "CANCELLED"}


    def draw_section(self, layout, context) -> None:
        layout.operator("superskin.open_builtin_tutorial", text="Quick Start Guide", icon='HELP')



def register():
    bpy.utils.register_class(SSPrefBuiltinTutorial)
    bpy.types.WindowManager.superskin_builtin_tutorial_prefs = bpy.props.PointerProperty(
        type=SSPrefBuiltinTutorial, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(BuiltinTutorialFeature())


def unregister():
    UnifiedRegistry.unregister("builtin_tutorial")
    del bpy.types.WindowManager.superskin_builtin_tutorial_prefs
    bpy.utils.unregister_class(SSPrefBuiltinTutorial)
