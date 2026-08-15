"""WeightTransferFeature / WeightExportFeature / WeightImportFeature — Unified
Component Architecture for Maya-style weight transfer, split (2026-08-07)
into three separate tool_socket dropdown entries.

Collapses the old WeightTransferDomain (action dispatch) and inline PrefsExtensionSpec
into three UnifiedFeatureExtension subclasses sharing one package.

Owns:
  - SSPrefWeightTransfer PropertyGroup (registered on WindowManager)
  - Action dispatch: "transfer_weight_maya" (WeightTransferFeature only)
  - UI layout: delegates to .ui.draw_transfer_section() / .ui.draw_export_section() / .ui.draw_import_section()
  - JSON persistence: populate() / serialize_into() (WeightTransferFeature only)
"""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui
from . import state_ops

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


def _poll_mesh_object(self, obj):
    return obj.type == 'MESH'


def _on_is_source_changed(self, context):
    """Enforce "only one entry may be the Source at a time" — a radio
    toggle, not an independent checkbox. `self` is the SSWeightTransferEntryItem
    row that was just clicked; when it's switched ON, every sibling row in
    the same list (state.entries) has its own is_source cleared. Switching
    an entry OFF (making no entry the Source) is allowed and does nothing
    further here.

    Compares with `!=`, not `is not` — bpy_struct doesn't guarantee a stable
    Python wrapper identity across separate accesses to the same underlying
    CollectionProperty item (only `==`/`!=`, which compare the underlying
    RNA pointer, are reliable). Using `is not` here was a real bug: iterating
    `state.entries` can hand back a *different* Python wrapper object for
    the very entry `self` already refers to, so `entry is not self` could
    be True even when `entry` and `self` are the same underlying row —
    immediately clearing the `is_source` flag this callback was just asked
    to set, so the toggle appeared to silently do nothing."""
    if not self.is_source:
        return
    state = context.scene.superskin_weight_transfer_state
    for entry in state.entries:
        if entry != self and entry.is_source:
            entry.is_source = False


def _on_active_entry_changed(self, context):
    """Sync the 3D viewport selection to whichever row is now active in the
    unified Source/Target list — clicking a list row selects (and makes
    active) that row's object in the viewport, per user request. `self` is
    the SSWeightTransferState instance (the update callback for an
    IntProperty receives the owning PropertyGroup, not the entry).

    Skips entirely while state_ops.sync_active_entry_from_viewport() is the
    one currently assigning active_entry_index (the REVERSE sync direction:
    viewport selection -> highlighted list row) — without this guard, that
    reverse sync would immediately trigger this callback, which would
    re-mutate the viewport selection it was just reacting to, silently
    collapsing a multi-object viewport selection down to one object on every
    redraw."""
    if state_ops.is_syncing_viewport_selection():
        return
    state = self
    if not (0 <= state.active_entry_index < len(state.entries)):
        return
    obj = state.entries[state.active_entry_index].object
    if obj is None or obj.name not in context.view_layer.objects:
        return
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


class SSWeightTransferEntryItem(bpy.types.PropertyGroup):
    """One row in the unified Source/Target list — a mesh reference, its own
    "restrict to selected vertices only" toggle, and an `is_source` radio
    flag marking whether this row is THE Source (at most one entry across
    the whole list may have it set — see _on_is_source_changed()). Every
    other entry with a valid mesh is implicitly a Target."""
    object: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="Mesh",
        poll=_poll_mesh_object,
    )
    use_selected_verts: bpy.props.BoolProperty(
        name="Use Selected Vertices Only",
        description="Restrict this mesh's contribution to the transfer to only its currently selected vertices, instead of the whole mesh",
        default=False,
    )
    is_source: bpy.props.BoolProperty(
        name="Is Source",
        description="Mark this mesh as the Source for the transfer — only one entry in the list may be the Source at a time",
        default=False,
        update=_on_is_source_changed,
    )


class SSWeightTransferState(bpy.types.PropertyGroup):
    """Live/working Source + Target configuration for object.mw_copy_skin_weight_maya,
    registered on bpy.types.Scene (NOT WindowManager/SKIP_SAVE like SSPrefWeightTransfer)
    so it is real scene work-data — it round-trips through a normal .blend save
    automatically, the same way object transforms or SuperSkinPro Layers do,
    with no explicit "save" step required.

    `entries` is a single unified list (Source and Target used to be two
    separate CollectionPropertys) — which entry is the Source is a per-row
    `is_source` flag (SSWeightTransferEntryItem), not a separate field or
    collection here."""
    entries: bpy.props.CollectionProperty(type=SSWeightTransferEntryItem)
    active_entry_index: bpy.props.IntProperty(default=0, update=_on_active_entry_changed)


class SSPrefWeightTransfer(bpy.types.PropertyGroup):
    """Weight Transfer settings (per-machine — shared across every .blend file).
    Shared verbatim by all three dropdown entries (Weight Transfer / Weight
    Export / Weight Import — see WeightTransferFeature / WeightExportFeature /
    WeightImportFeature below), since insert_method/transfer_method/
    pose_mode apply across more than one of them."""
    insert_method: bpy.props.EnumProperty(
        name="Target Layer Data",
        items=[
            ('REPLACE', "Overwrite", "Clear the target's existing vertex groups before transferring"),
            ('APPEND', "Keep Layer", "Keep the target's existing vertex groups and add the transferred ones on top"),
        ],
        default='REPLACE',
        update=_on_changed,
    )
    transfer_method: bpy.props.EnumProperty(
        name="Method",
        items=[
            ('CLOSEST_DISTANCE', "Closest Distance", "Blend weights along the source's axial bone chain based on world position"),
            ('VERTEX_ID', "Vertex ID", "Copy weights using matching vertex indices (source and target must have identical vertex counts)"),
        ],
        default='CLOSEST_DISTANCE',
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
# WeightTransferFeature / WeightExportFeature / WeightImportFeature
# — UnifiedFeatureExtension, three separate LAYER_SOCKET dropdown entries
# ==============================================================================
#
# Split 2026-08-07 from a single "Weight Transfer" dropdown entry containing
# an inline Transfer/Export/Import tab bar into three independently-selectable
# tool_socket dropdown entries (per explicit user request), so each shows up
# as its own item in the LAYER tab's "More Tools" dropdown instead of being
# tabs inside one item. All three still live in this one package/file — they
# share transfer_core/ops.py/io_ops.py/state_ops.py and the SSPrefWeightTransfer
# PropertyGroup above — only the UI entry points were split, per
# features/tool_socket/README.md's plug-in contract ("a tool plugged into the
# socket is a completely normal UnifiedFeatureExtension subclass"), which
# places no restriction on more than one such subclass living in the same
# feature package. Only WeightTransferFeature keeps defaults_path/populate/
# serialize_into — Export and Import have no settings of their own beyond
# what's already read from the shared prefs group, so a second/third
# default_config.json entry point would be redundant.

class WeightTransferFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Transfer domain (the live mesh-to-mesh
    transfer, via the unified Source/Target list — see README.md)."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "weight_transfer"
    actions = ["transfer_weight_maya"]
    section_title = "Weight Transfer"
    draw_tab = "LAYER_SOCKET"  # plugged into tool_socket's LAYER dropdown, see features/tool_socket/README.md
    link = "https://docs.superskinpro.com/transfer_weight/"
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
        """Draw the Weight Transfer section — delegates to .ui.draw_transfer_section()."""
        ui.draw_transfer_section(layout, context)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        prefs = bpy.context.window_manager.superskin_weight_transfer_prefs
        prefs.insert_method = data.get("insert_method", "REPLACE")
        prefs.transfer_method = data.get("transfer_method", "CLOSEST_DISTANCE")
        prefs.pose_mode = data.get("pose_mode", "REST_POSE")

    def serialize_into(self, full_dict: dict) -> None:
        prefs = bpy.context.window_manager.superskin_weight_transfer_prefs
        full_dict["weight_transfer"] = {
            "insert_method": prefs.insert_method,
            "transfer_method": prefs.transfer_method,
            "pose_mode": prefs.pose_mode,
        }


class WeightExportFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Export dropdown entry — no settings
    of its own (see README.md's "No configurable export settings")."""

    domain_id = "weight_export"
    actions = []  # self-contained operator (superskin.export_weight_json), no dispatched action
    section_title = "Weight Export"
    draw_tab = "LAYER_SOCKET"
    link = "https://docs.superskinpro.com/transfer_weight/"
    locked_expanded = True

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # No actions registered — Export runs via its own self-contained operator.
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        ui.draw_export_section(layout, context)


class WeightImportFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Import dropdown entry — shares
    insert_method/transfer_method with WeightTransferFeature's
    SSPrefWeightTransfer PropertyGroup, persisted from there."""

    domain_id = "weight_import"
    actions = []  # self-contained operator (superskin.import_weight_json), no dispatched action
    section_title = "Weight Import"
    draw_tab = "LAYER_SOCKET"
    link = "https://docs.superskinpro.com/transfer_weight/"
    locked_expanded = True

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # No actions registered — Import runs via its own self-contained operator.
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        ui.draw_import_section(layout, context)


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroups (WindowManager prefs + Scene work-state) and the
    extension with UnifiedRegistry."""
    bpy.utils.register_class(SSPrefWeightTransfer)
    bpy.types.WindowManager.superskin_weight_transfer_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightTransfer, options={'SKIP_SAVE'},
    )

    # Scene-level Source/Target work-state — deliberately NOT SKIP_SAVE, so it
    # persists with the .blend like any other scene data. Registration order
    # matters: each CollectionProperty(type=...) below requires its element
    # type to already be registered.
    bpy.utils.register_class(SSWeightTransferEntryItem)
    bpy.utils.register_class(SSWeightTransferState)
    bpy.types.Scene.superskin_weight_transfer_state = bpy.props.PointerProperty(
        type=SSWeightTransferState,
    )

    bpy.utils.register_class(ui.SUPERSKIN_UL_wt_entries)
    UnifiedRegistry.register(WeightTransferFeature())
    UnifiedRegistry.register(WeightExportFeature())
    UnifiedRegistry.register(WeightImportFeature())


def unregister():
    """Unregister the extensions and PropertyGroups."""
    UnifiedRegistry.unregister("weight_import")
    UnifiedRegistry.unregister("weight_export")
    UnifiedRegistry.unregister("weight_transfer")
    bpy.utils.unregister_class(ui.SUPERSKIN_UL_wt_entries)

    try:
        del bpy.types.Scene.superskin_weight_transfer_state
    except Exception:
        pass
    bpy.utils.unregister_class(SSWeightTransferState)
    bpy.utils.unregister_class(SSWeightTransferEntryItem)

    try:
        del bpy.types.WindowManager.superskin_weight_transfer_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightTransfer)
