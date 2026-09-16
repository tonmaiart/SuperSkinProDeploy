"""Weight Info — SKINNING-tab widget code.

Moved from ``features/weight_info/weight_info_feature.py`` (2026-09-14) —
the ``weight_info`` domain itself keeps only `execute()` (a permanent
no-op, viewer-only) and its registration in `UnifiedRegistry`. The
``SSPrefWeightInfoEntry``/``SSPrefWeightInfo`` PropertyGroups and the
``SUPERSKIN_UL_weight_info_list`` UIList are pure display scratch state
(rebuilt from scratch on every draw, never persisted -- see the original
domain doc's "Exactly-One-Vertex Rule"), not user settings, so they moved
here together with the drawing code rather than staying behind as
otherwise-unused registrations in the original package. See
``docs/domains/weight_info.md`` and ``docs/domains/skin_tools_ui.md``.

NOTE: ``weight_info`` is currently in ``features/__init__.py``'s
``_DISABLED`` tuple -- this code is migrated for consistency but cannot be
live-verified in Blender until the domain is re-enabled.
"""

import bpy

from ...core.facade import CoreFacade

_LIST_ROWS = 4
_WEIGHT_EPSILON = 0.001
_COLUMN_SPLIT = 0.7


# ==============================================================================
# Property Groups (transient UI-only state -- see module docstring)
# ==============================================================================

class SSPrefWeightInfoEntry(bpy.types.PropertyGroup):
    """One row of the scrollable list: a bone/vertex-group name and its
    weight on the currently inspected vertex, from the active layer."""
    bone_name: bpy.props.StringProperty()
    weight: bpy.props.FloatProperty()


class SSPrefWeightInfo(bpy.types.PropertyGroup):
    """Transient UI-only state: the list collection template_list() reads
    from. Rebuilt (cleared + re-added) every draw_section() call — nothing
    here is meant to survive a redraw, let alone a file save."""
    entries: bpy.props.CollectionProperty(type=SSPrefWeightInfoEntry)
    active_index: bpy.props.IntProperty(default=0)


# ==============================================================================
# UIList — gives the bone/weight view a real, height-capped scrollbar
# ==============================================================================

class SUPERSKIN_UL_weight_info_list(bpy.types.UIList):
    """Read-only scrollable list, one row per non-zero bone weight on the
    inspected vertex. Suppresses Blender's own built-in filter row (no
    search/selection semantics here, unlike
    ``interface.template_ui.SuperSkinListMixin``'s bone/layer rows)."""
    bl_idname = "SUPERSKIN_UL_weight_info_list"

    use_filter_show = False

    def draw_filter(self, context, layout):
        pass

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=_COLUMN_SPLIT)
        split.label(text=item.bone_name)
        split.label(text=f"{item.weight:.3f}")


# ==============================================================================
# Drawing entry point
# ==============================================================================

def draw_section(layout, context) -> None:
    obj = context.active_object
    if not obj or obj.type != 'MESH':
        layout.label(text="No mesh active", icon='ERROR')
        return

    if "ss_layers_meta" not in obj.data:
        layout.label(text="No layer system", icon='INFO')
        return

    try:
        facade = CoreFacade(context)
        selected = facade.get_selected_verts()
    except ValueError:
        layout.label(text="Not activated", icon='ERROR')
        return

    wi = context.window_manager.superskin_weight_info_prefs
    wi.entries.clear()

    if len(selected) == 1:
        v_idx = selected[0]
        layer_data = facade.read_active_layer()
        weights = layer_data.get(v_idx, {})
        nonzero = sorted(
            ((name, w) for name, w in weights.items() if w > _WEIGHT_EPSILON),
            key=lambda pair: -pair[1],
        )
        for name, w in nonzero:
            entry = wi.entries.add()
            entry.bone_name = name
            entry.weight = w

    if wi.active_index >= len(wi.entries):
        wi.active_index = 0

    # The list widget itself always stays on screen — only its items
    # are cleared/repopulated above. See docs/domains/weight_info.md's "Exactly-One-
    # Vertex Rule": clearing means emptying `wi.entries`, never
    # removing the template_list() call.
    layout.template_list(
        SUPERSKIN_UL_weight_info_list.bl_idname, "",
        wi, "entries", wi, "active_index",
        rows=_LIST_ROWS, maxrows=_LIST_ROWS,
    )


# ==============================================================================
# Registration (called from features/skin_tools_ui/__init__.py)
# ==============================================================================

_classes = (
    SSPrefWeightInfoEntry,
    SSPrefWeightInfo,
    SUPERSKIN_UL_weight_info_list,
)


def register():
    for cls in _classes:
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_weight_info_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightInfo, options={'SKIP_SAVE'},
    )


def unregister():
    try:
        del bpy.types.WindowManager.superskin_weight_info_prefs
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
