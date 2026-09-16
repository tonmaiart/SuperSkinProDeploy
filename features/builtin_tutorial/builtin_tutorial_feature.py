"""BuiltinTutorialFeature — Unified Component Architecture implementation for
the builtin_tutorial domain.

Presents a single "Quick Start Guide" button, drawn directly inside the
"How to use" toggle body
(interface.widget_preferences._draw_how_to_use_body(), moved there from
panel_main.py 2026-09-14 -- see that function's own docstring) rather than
through the generic PREFERENCE-tab loop -- same placement pattern
support_report uses for its own bare button in the System Actions box. See
docs/domains/builtin_tutorial.md for the full architecture and the
rationale for registering zero dispatch actions.

Owns one PropertyGroup (SSPrefBuiltinTutorial) holding transient navigation
state only (which step is currently shown in the popup) -- nothing here is
persisted to user.json.
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from . import logic

_step_items_cache = []


def _get_step_enum_items(self, context):
    """Dynamic EnumProperty items callback for SSPrefBuiltinTutorial.active_step.

    Cached on the module (not just returned fresh every call) because
    Blender's EnumProperty keeps only a borrowed reference to the strings
    returned by a callback -- returning a list that gets garbage collected
    before Blender is done reading it is a known crash source (same
    precaution debug_console's own dynamic EnumProperty callback documents).
    """
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
        # No actions registered — see docs/domains/builtin_tutorial.md "Why no dispatch actions".
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Just the bare button -- no label, no header. Not reached through
        the generic PREFERENCE-tab loop (widget_preferences.py's
        _draw_preferences() excludes this domain_id and its own
        _draw_how_to_use_body() calls this method directly instead), so
        section_title/is_collapsible() are irrelevant to how this actually
        renders; kept for UnifiedFeatureExtension's abstract interface only.
        """
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
