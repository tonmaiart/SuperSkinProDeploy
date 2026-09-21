"""BuiltinTutorialFeature — Unified Component Architecture implementation for the
builtin_tutorial domain."""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from . import logic

_step_items_cache = []


def _get_step_enum_items(self, context):
    """Dynamic EnumProperty items callback for SSPrefBuiltinTutorial.active_step."""
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

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "builtin_tutorial"
    actions = []
    section_title = "Quick Start Guide"
    draw_tab = "PREFERENCE"

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade) -> dict:
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Just the bare button."""
        layout.operator("superskin.open_builtin_tutorial", text="Quick Start Guide", icon='HELP')


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

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
