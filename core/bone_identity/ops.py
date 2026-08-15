"""Orphan bone row selection operator.

Selecting an orphan row sets active_orphan_name (not last_clicked_index,
since orphan bones have no real vertex group). Weight ops read
active_orphan_name via UIController._active_bone_name() and route through
get_unified_mapping() synthetic IDs so Rust can process them normally.

Relocated from core_subsystems/orphan_resolver/ops.py to eliminate a layering
violation (core_subsystems must not register bpy.types.Operator classes).

Ctrl/Shift-click multi-select: routes through the same BoneListAdapter /
resolve_row_click_selection() plumbing SUPERSKIN_OT_select_vertex_group_row
uses (features/deform_bone_viewer/ui.py), imported function-scoped to avoid
a core/ -> interface/ import at module load time. Orphan rows used to be
walled off from that pool entirely (Select All / Ctrl / Shift-click only
ever touched real vertex_groups), even though weight ops already target
orphan bones fine via synthetic IDs -- see docs/bug-history/0017.
"""

import bpy


class SUPERSKIN_OT_select_orphan_bone_row(bpy.types.Operator):
    """Select an orphaned-bone row in the Deform Bones list.

    Supports Ctrl-click (toggle), Shift-click (range-select), and
    Alt+Shift-click (select all visible) exactly like the real-bone and
    mask row operators, via the shared BoneListAdapter selection pool.
    """
    bl_idname = "superskin.select_orphan_bone_row"
    bl_label = "Select Orphaned Bone Row"
    bl_options = {'INTERNAL'}

    orphan_name: bpy.props.StringProperty(name="Orphaned Bone Name")

    def invoke(self, context, event):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}

        item_key = self.orphan_name

        was_suppressing = context.scene.superskin_internal_transaction
        context.scene.superskin_internal_transaction = True
        try:
            from ...interface.template_ui.select_ops import get_adapter, resolve_row_click_selection
            adapter = get_adapter('BONES')
            selected_keys, last_key, history = adapter.read_selection(context, obj)
            visual_order = adapter.get_keys_in_visual_order(context, obj)
            selected_keys, last_key, history = resolve_row_click_selection(
                selected_keys, last_key, history, item_key, visual_order, event
            )
            adapter.write_selection(context, obj, selected_keys, last_key, history)
        finally:
            context.scene.superskin_internal_transaction = was_suppressing

        # write_selection() just resolved the click's actual last-active
        # target (could be this orphan, a real bone, or the Mask row --
        # e.g. Ctrl-clicking this orphan OFF while other rows stay selected
        # falls back to whatever's next in history, which may not be an
        # orphan at all). Dispatch the weight-overlay preview/apply off
        # THAT, not blindly off self.orphan_name.
        storage = obj.superskin_storage
        if storage.active_orphan_name:
            _sync_bones_idx_to_orphan(obj, storage.active_orphan_name)
            try:
                from .bone_identity_service import BoneIdentityService
                BoneIdentityService(context, obj=obj).preview_orphan_weight(storage.active_orphan_name)
            except Exception as e:
                from ...core_subsystems.debug_logging import DebugLogService
                DebugLogService.log(
                    "bone_id",
                    f"SUPERSKIN_OT_select_orphan_bone_row: preview_orphan_weight() "
                    f"EXCEPTION for orphan_name={storage.active_orphan_name!r}: {e!r}",
                )
        else:
            try:
                from ..facade import CoreFacade
                ctrl = CoreFacade(context)
                ctrl.clear_orphan_weight_preview()
                vg_list = obj.vertex_groups
                if 0 <= storage.last_clicked_index < len(vg_list):
                    ctrl.set_active_bone_name(vg_list[storage.last_clicked_index].name)
                ctrl.apply_active_bone()
            except Exception:
                pass

        context.area.tag_redraw()
        return {'FINISHED'}


def _sync_bones_idx_to_orphan(obj, orphan_name: str):
    """Point superskin_bones_idx at the orphan row in the mirror collection."""
    for i, item in enumerate(obj.superskin_bones_collection):
        if item.is_orphan and item.name == orphan_name:
            obj.superskin_bones_idx = i
            return


_classes = (SUPERSKIN_OT_select_orphan_bone_row,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
