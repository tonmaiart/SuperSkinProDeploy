"""MirrorFeature — Unified Component Architecture implementation for the mirror domain.

Collapses the old MirrorDomain (action dispatch) and prefs.py (PropertyGroup,
draw, persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefMirror / SSPrefMirrorSRItem PropertyGroups (registered on WindowManager)
  - MirrorPreferencesService (stateless accessor)
  - Action dispatch: "mirror"
  - JSON persistence: populate() / serialize_into()

UI layout (draw_section(), the SUPERSKIN_PT_mirror_options popover, and
_draw_sr_body()) moved to features/skin_tools_ui/ui_mirror.py (2026-09-14,
see docs/domains/skin_tools_ui.md) — draw_section() here is a stub. The
"Mirror Weight" button itself moved again, same day, into
features/skin_tools_ui/ui_action_grid.py's combined 2-column grid; only
SUPERSKIN_PT_mirror_options/_draw_sr_body()/SUPERSKIN_UL_mirror_sr remain
in ui_mirror.py.
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from .logic import execute_mirror_pipeline, execute_mirror_pipeline_all_layers

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
    mirror_all_layers: bpy.props.BoolProperty(
        name="Mirror to All Layers",
        description="Mirror every layer in the stack instead of just the active one",
        default=False,
        update=_on_changed,
    )


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

    @classmethod
    def get_mirror_all_layers(cls) -> bool:
        return cls._prefs().mirror_all_layers


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
    link = "https://docs.superskinpro.com/mirror_tab/"
    defaults_path = _DEFAULTS_PATH
    priority = 3
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        try:
            if MirrorPreferencesService.get_mirror_all_layers():
                execute_mirror_pipeline_all_layers(core_facade)
            else:
                execute_mirror_pipeline(core_facade)
        except ValueError as e:
            return {"status": "CANCELLED", "message": str(e)}
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """UI moved to features/skin_tools_ui/ui_mirror.py — see
        docs/domains/skin_tools_ui.md."""
        pass

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        mirror = bpy.context.window_manager.superskin_mirror_prefs
        mirror.mirror_axis = data.get("mirror_axis", "X")
        mirror.direction   = data.get("direction",   "POS_NEG")
        mirror.mirror_data = data.get("mirror_data", "BOTH")
        mirror.mirror_all_layers = data.get("mirror_all_layers", False)

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
            "mirror_all_layers": mirror.mirror_all_layers,
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
    UnifiedRegistry.register(MirrorFeature())


def unregister():
    """Unregister PropertyGroups and the extension."""
    UnifiedRegistry.unregister("mirror")
    try:
        del bpy.types.WindowManager.superskin_mirror_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefMirror)
    bpy.utils.unregister_class(SSPrefMirrorSRItem)



