"""Sketch Weight Guide — SKINNING-tab widget code.

Moved from ``features/sketch_weight/sketch_weight_feature.py::draw_section()``
(2026-09-14) — the ``sketch_weight`` domain keeps its ``execute()``/
``SSPrefSketchWeight`` PropertyGroup/persistence in its own package; only
this drawing code moved here. Reads ``superskin_sketch_weight_prefs``
directly (a plain WindowManager attribute, no import needed) and dispatches
via the generic ``superskin.execute_action`` operator. See
``docs/domains/sketch_weight.md`` and ``docs/domains/skin_tools_ui.md``.

NOTE: ``sketch_weight`` is currently in ``features/__init__.py``'s
``_DISABLED`` tuple -- this code is migrated for consistency but cannot be
live-verified in Blender until the domain is re-enabled.
"""


def draw_section(layout, context) -> None:
    prefs = context.window_manager.superskin_sketch_weight_prefs
    col = layout.column(align=True)
    col.label(text="Select the tool below, then drag a stroke")
    col.label(text="across the mesh in the viewport.")
    col.separator()
    op = col.operator("superskin.execute_action", text="Select Sketch Guide Tool", icon='GREASEPENCIL')
    op.domain_id = "sketch_weight"
    op.action_id = "draw_guide_stroke"
    col.separator()
    col.prop(prefs, "guide_radius")
