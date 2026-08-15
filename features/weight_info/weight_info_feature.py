"""WeightInfoFeature — Unified Component Architecture implementation for the
weight_info domain.

Read-only viewer: while exactly one vertex is selected, lists that vertex's
non-zero active-layer bone weights — one row per vertex group (row = bone
name, value = weight). Any other selection count (zero, or more than one)
clears the list entirely, since a multi-vertex summary was never the intent
of this domain. Mask data and other layers are never read — only the
active layer's weight dict (``CoreFacade.read_active_layer()``), which is
already mode-aware (Edit Mode temp VGs vs. Object Mode ``ss_layer_N``).

Owns:
  - SSPrefWeightInfoEntry PropertyGroup (one row's worth of bone/weight data)
  - SSPrefWeightInfo PropertyGroup (registered on WindowManager, SKIP_SAVE) —
    the CollectionProperty backing the scrollable list, rebuilt from scratch
    on every draw_section() call (same pattern as debug_console's log_items;
    only safe because it lives on WindowManager, not on the Mesh/Object ID)
  - SUPERSKIN_UL_weight_info_list (UIList — gives the list a real, height-
    capped, scrollable widget via template_list())
  - UI layout: draw_section()
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_LIST_ROWS = 4
_WEIGHT_EPSILON = 0.001
_COLUMN_SPLIT = 0.7


# ==============================================================================
# Property Groups
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
# WeightInfoFeature — UnifiedFeatureExtension
# ==============================================================================

class WeightInfoFeature(UnifiedFeatureExtension):
    """Viewer extension listing one vertex's active-layer weights, per
    vertex group, in the SKINNING tab."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "weight_info"
    actions = []
    section_title = "Weight Info"
    draw_tab = "SKINNING"
    collapsible = True
    priority = 4
    expanded_by_default = True
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # Viewer-only domain — no dispatchable actions.
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
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
        # are cleared/repopulated above. See README.md's "Exactly-One-
        # Vertex Rule": clearing means emptying `wi.entries`, never
        # removing the template_list() call.
        layout.template_list(
            SUPERSKIN_UL_weight_info_list.bl_idname, "",
            wi, "entries", wi, "active_index",
            rows=_LIST_ROWS, maxrows=_LIST_ROWS,
        )

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

_classes = (
    SSPrefWeightInfoEntry,
    SSPrefWeightInfo,
    SUPERSKIN_UL_weight_info_list,
)


def register():
    """Register PropertyGroups/UIList and the extension with UnifiedRegistry."""
    for cls in _classes:
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_weight_info_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightInfo, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(WeightInfoFeature())


def unregister():
    """Unregister the extension and everything this domain registered."""
    UnifiedRegistry.unregister("weight_info")
    try:
        del bpy.types.WindowManager.superskin_weight_info_prefs
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
