"""MirrorFeature — Unified Component Architecture implementation for the mirror domain.

Collapses the old MirrorDomain (action dispatch) and prefs.py (PropertyGroup,
draw, persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefMirror / SSPrefMirrorSRItem PropertyGroups (registered on WindowManager)
  - MirrorPreferencesService (stateless accessor)
  - Action dispatch: "mirror"
  - UI layout: draw_section()
  - JSON persistence: populate() / serialize_into()
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from .logic import execute_mirror_pipeline

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


class SSPrefMirrorSRItem(bpy.types.PropertyGroup):
    """A single bone-name search/replace rule used to find mirror pairs."""
    search_text:  bpy.props.StringProperty(name="Search",  update=_on_changed)
    replace_text: bpy.props.StringProperty(name="Replace", update=_on_changed)


class SSPrefMirror(bpy.types.PropertyGroup):
    """Mirror settings (per-machine — shared across every .blend file)."""
    mirror_axis: bpy.props.EnumProperty(
        name="MirrorAxis",
        items=[
            ('X', "X", "Mirror along axis X"),
            ('Y', "Y", "Mirror along axis Y"),
            ('Z', "Z", "Mirror along axis Z"),
        ],
        default='X',
        update=_on_changed,
    )
    direction: bpy.props.EnumProperty(
        name="Direction",
        items=[
            ('POS_NEG', "Positive to Negative", "Mirror from positive side to negative side"),
            ('NEG_POS', "Negative to Positive", "Mirror from negative side to positive side"),
        ],
        default='POS_NEG',
        update=_on_changed,
    )
    mirror_data: bpy.props.EnumProperty(
        name="Mirror Data",
        items=[
            ('BONE', "Deform Bone", "Mirror only the deform-bone weight (layer) channel"),
            ('MASK', "Layer Mask", "Mirror only the active layer's mask channel"),
            ('BOTH', "Both", "Mirror both the deform-bone weight and layer mask channels"),
        ],
        default='BOTH',
        update=_on_changed,
    )
    search_replace_pairs:  bpy.props.CollectionProperty(type=SSPrefMirrorSRItem)
    search_replace_index:  bpy.props.IntProperty(name="Index", default=0)


# ==============================================================================
# Preferences accessor (replaces MirrorPreferencesService)
# ==============================================================================

class MirrorPreferencesService:
    """Stateless accessor for mirror prefs — consumed by logic.py."""

    @staticmethod
    def _prefs() -> "SSPrefMirror":
        return bpy.context.window_manager.superskin_mirror_prefs

    @classmethod
    def get_mirror_axis(cls) -> str:
        return cls._prefs().mirror_axis

    @classmethod
    def get_mirror_direction(cls) -> str:
        return cls._prefs().direction

    @classmethod
    def get_mirror_data(cls) -> str:
        return cls._prefs().mirror_data

    @classmethod
    def get_mirror_search_replace_pairs(cls) -> list:
        return [(p.search_text, p.replace_text) for p in cls._prefs().search_replace_pairs]


def _draw_sr_body(box, context, mirror) -> None:
    sr_coll = mirror.search_replace_pairs
    idx = mirror.search_replace_index

    row = box.row()
    row.template_list(
        "SUPERSKIN_UL_mirror_sr", "",
        mirror, "search_replace_pairs",
        mirror, "search_replace_index",
        rows=4,
    )

    col_btns = row.column(align=True)
    # "Add" must stay enabled even when the list is empty -- it's the
    # only way to recover from an empty list through the UI. Only
    # "Remove" needs a valid selection, so its enabled state is scoped
    # to its own sub-layout rather than the shared column.
    col_btns.operator("superskin.add_mirror_sr", text="", icon='ADD')
    remove_col = col_btns.column(align=True)
    remove_col.enabled = 0 <= idx < len(sr_coll)
    rm = remove_col.operator("superskin.remove_mirror_sr", text="", icon='REMOVE')
    rm.index = idx


# ==============================================================================
# Options popover — direction, axis, mirror data, S/R mapping list
# ==============================================================================

class SUPERSKIN_PT_mirror_options(bpy.types.Panel):
    """Popover content for the Mirror domain's settings, opened from the
    gear icon next to "Mirror Weights". Used to be drawn inline in the
    section body; moved here so the button row stays a single compact row
    with the action first."""
    bl_idname = "SUPERSKIN_PT_mirror_options"
    bl_label = "Mirror Options"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- UI is the N-panel sidebar region itself, so a Panel
    # registered against it (with no bl_category restricting which tab it
    # docks under) gets auto-listed as its own separate docked panel (under
    # a default "Misc" tab) in ADDITION to being invocable via
    # layout.popover(). HEADER-region panels are only ever drawn when
    # explicitly invoked (popover()/menu), never auto-docked -- the same
    # convention Blender's own built-in popovers use (e.g.
    # VIEW3D_PT_shading_lighting).
    bl_region_type = 'HEADER'
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        mirror = context.window_manager.superskin_mirror_prefs

        col_opts = layout.column(align=True)
        row_axis = col_opts.split(factor=0.45, align=True)
        row_axis.label(text="Mirror Axis:")
        row_axis.prop(mirror, "mirror_axis", text="")
        col_opts.separator(factor=0.5)
        row_dir = col_opts.split(factor=0.45, align=True)
        row_dir.label(text="Mirror Direction:")
        row_dir.prop(mirror, "direction", text="")
        col_opts.separator(factor=0.5)
        row_data = col_opts.split(factor=0.45, align=True)
        row_data.label(text="Mirror Target Data:")
        row_data.prop(mirror, "mirror_data", text="")
        layout.label(text="Mapping Keywords:")
        _draw_sr_body(layout.box(), context, mirror)


# ==============================================================================
# MirrorFeature — UnifiedFeatureExtension
# ==============================================================================

class MirrorFeature(UnifiedFeatureExtension):
    """Unified extension for the Mirror domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "mirror"
    actions = ["mirror"]
    section_title = "Mirror"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH
    priority = 3
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        try:
            execute_mirror_pipeline(core_facade)
        except ValueError as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the Mirror section: the Mirror Weights action first, with
        the direction/axis/data/mapping options tucked behind a popover
        (gear icon) in the same row instead of always-expanded inline
        controls."""
        row_btn = layout.row(align=True)
        row_btn.scale_y = 1.2
        row_btn.operator("object.mirror_weights", text="Mirror Weights")
        row_btn.popover(SUPERSKIN_PT_mirror_options.bl_idname, text="", icon='PREFERENCES')

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        mirror = bpy.context.window_manager.superskin_mirror_prefs
        mirror.mirror_axis = data.get("mirror_axis", "X")
        mirror.direction   = data.get("direction",   "POS_NEG")
        mirror.mirror_data = data.get("mirror_data", "BOTH")

        sr_coll = mirror.search_replace_pairs
        sr_coll.clear()
        for pair in data.get("search_replace_pairs", []):
            item = sr_coll.add()
            item.search_text  = pair[0]
            item.replace_text = pair[1]

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        mirror = bpy.context.window_manager.superskin_mirror_prefs
        full_dict["mirror"] = {
            "mirror_axis": mirror.mirror_axis,
            "direction":   mirror.direction,
            "mirror_data": mirror.mirror_data,
            "search_replace_pairs": [
                [p.search_text, p.replace_text]
                for p in mirror.search_replace_pairs
            ],
        }


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroups on WindowManager and the extension with UnifiedRegistry."""
    bpy.utils.register_class(SSPrefMirrorSRItem)
    bpy.utils.register_class(SSPrefMirror)
    bpy.types.WindowManager.superskin_mirror_prefs = bpy.props.PointerProperty(
        type=SSPrefMirror, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(SUPERSKIN_PT_mirror_options)
    UnifiedRegistry.register(MirrorFeature())


def unregister():
    """Unregister PropertyGroups and the extension."""
    UnifiedRegistry.unregister("mirror")
    bpy.utils.unregister_class(SUPERSKIN_PT_mirror_options)
    try:
        del bpy.types.WindowManager.superskin_mirror_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefMirror)
    bpy.utils.unregister_class(SSPrefMirrorSRItem)



