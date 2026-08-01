"""WeightTransferFeature — Unified Component Architecture for Maya-style weight transfer.

Collapses the old WeightTransferDomain (action dispatch) and inline PrefsExtensionSpec
into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefWeightTransfer PropertyGroup (registered on WindowManager)
  - Action dispatch: "transfer_weight_maya"
  - UI layout: delegates to .ui.draw_section()
  - JSON persistence: populate() / serialize_into()
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


class SSPrefWeightTransfer(bpy.types.PropertyGroup):
    """Weight Transfer settings (per-machine — shared across every .blend file)."""
    layer_output: bpy.props.EnumProperty(
        name="Layer Output",
        items=[
            ('SEPARATE', "Separate", "Keep each source vertex group as its own separate group on the target"),
            ('MERGE', "Merge", "Merge all source vertex groups into a single group before transferring"),
        ],
        default='SEPARATE',
        update=_on_changed,
    )
    insert_method: bpy.props.EnumProperty(
        name="Insert Method",
        items=[
            ('REPLACE', "Replace", "Clear the target's existing vertex groups before transferring"),
            ('APPEND', "Append", "Keep the target's existing vertex groups and add the transferred ones on top"),
        ],
        default='REPLACE',
        update=_on_changed,
    )
    transfer_method: bpy.props.EnumProperty(
        name="Transfer Method",
        items=[
            ('CLOSEST_DISTANCE', "Closest Distance", "Blend weights along the source's axial bone chain based on world position"),
            ('VERTEX_ID', "Vertex ID", "Copy weights using matching vertex indices (source and target must have identical vertex counts)"),
        ],
        default='CLOSEST_DISTANCE',
        update=_on_changed,
    )
    auto_assign_armature: bpy.props.BoolProperty(
        name="Auto Assign Modifier on Import",
        description=(
            "On JSON import, find (or create, if missing) an Armature object named after "
            "the one recorded at export time, assign it to the target's Armature modifier, "
            "and create the modifier if the target doesn't have one yet"
        ),
        default=True,
        update=_on_changed,
    )
    use_selected_source_verts: bpy.props.BoolProperty(
        name="Use Selected Source Vertices",
        description=(
            "Restrict the transfer's source data to only the currently selected "
            "vertices on the source (proxy) mesh, for a more precise, localized "
            "result — live Transfer only, Export/Import Weight JSON ignore this"
        ),
        default=False,
        update=_on_changed,
    )
    use_selected_target_verts: bpy.props.BoolProperty(
        name="Use Selected Target Vertices",
        description=(
            "Restrict the transfer to only write onto the currently selected "
            "vertices on the target mesh, for a more precise, localized result — "
            "live Transfer only, Export/Import Weight JSON ignore this"
        ),
        default=False,
        update=_on_changed,
    )
    pose_mode: bpy.props.EnumProperty(
        name="Pose Reference",
        items=[
            ('REST_POSE', "Rest Pose", "Read source/target vertex positions from their stored (bind) shape, ignoring any current Armature pose"),
            ('CURRENT_POSE', "Current Pose", "Read source/target vertex positions as currently deformed by their Armature (and other modifiers), matching whatever pose they are posed into right now"),
        ],
        default='REST_POSE',
        description=(
            "Which shape the closest-point-on-surface search reads source/target "
            "vertex positions from — live Transfer only (Export/Import Weight JSON "
            "always use Rest Pose)"
        ),
        update=_on_changed,
    )


# ==============================================================================
# WeightTransferFeature — UnifiedFeatureExtension
# ==============================================================================

class WeightTransferFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Transfer domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "weight_transfer"
    actions = ["transfer_weight_maya"]
    section_title = "Weight Transfer"
    draw_tab = "LAYER"
    defaults_path = _DEFAULTS_PATH
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r}")
        try:
            result = bpy.ops.object.mw_copy_skin_weight_maya()
            status = "FINISHED" if 'FINISHED' in result else "CANCELLED"
            core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r} status={status}")
            return {"status": status}
        except Exception as e:
            core_facade.debug_log("feature_domains", f"weight_transfer.execute() action={action!r} raised {e!r}")
            return {"status": "CANCELLED", "message": str(e)}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the Weight Transfer section — delegates to .ui.draw_section()."""
        ui.draw_section(layout, context)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        prefs = bpy.context.window_manager.superskin_weight_transfer_prefs
        prefs.layer_output = data.get("layer_output", "SEPARATE")
        prefs.insert_method = data.get("insert_method", "REPLACE")
        prefs.transfer_method = data.get("transfer_method", "CLOSEST_DISTANCE")
        prefs.auto_assign_armature = data.get("auto_assign_armature", True)
        prefs.use_selected_source_verts = data.get("use_selected_source_verts", False)
        prefs.use_selected_target_verts = data.get("use_selected_target_verts", False)
        prefs.pose_mode = data.get("pose_mode", "REST_POSE")

    def serialize_into(self, full_dict: dict) -> None:
        prefs = bpy.context.window_manager.superskin_weight_transfer_prefs
        full_dict["weight_transfer"] = {
            "layer_output": prefs.layer_output,
            "insert_method": prefs.insert_method,
            "transfer_method": prefs.transfer_method,
            "auto_assign_armature": prefs.auto_assign_armature,
            "use_selected_source_verts": prefs.use_selected_source_verts,
            "use_selected_target_verts": prefs.use_selected_target_verts,
            "pose_mode": prefs.pose_mode,
        }


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroup on WindowManager and the extension with UnifiedRegistry."""
    bpy.utils.register_class(SSPrefWeightTransfer)
    bpy.types.WindowManager.superskin_weight_transfer_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightTransfer, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(ui.SUPERSKIN_PT_weight_transfer_options)
    UnifiedRegistry.register(WeightTransferFeature())


def unregister():
    """Unregister the extension and PropertyGroup."""
    UnifiedRegistry.unregister("weight_transfer")
    bpy.utils.unregister_class(ui.SUPERSKIN_PT_weight_transfer_options)
    try:
        del bpy.types.WindowManager.superskin_weight_transfer_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightTransfer)
