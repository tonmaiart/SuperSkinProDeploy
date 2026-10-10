
import bpy

from ....core.facade import CoreFacade


def _is_mask_kind(obj) -> bool:
    storage = getattr(obj, "superskin_storage", None) if obj else None
    return storage is not None and storage.active_is_mask


class SUPERSKIN_PT_copy_weight_options(bpy.types.Panel):
    """Copy, cut, and paste the active bone weight or layer mask on the whole mesh"""
    bl_idname = "SUPERSKIN_PT_copy_weight_options"
    bl_label = "Copy Weight"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 12

    def draw(self, context):
        layout = self.layout
        kind = "layer" if _is_mask_kind(context.active_object) else "bone"

        row = layout.row(align=True)
        row.operator(f"superskin.deform_copy_{kind}_weight", text="Copy", icon='COPYDOWN')
        row.operator(f"superskin.deform_cut_{kind}_weight", text="Cut", icon='TRASH')
        layout.separator()
        row = layout.row(align=True)
        row.operator(f"superskin.deform_paste_{kind}_weight_replace", text="Replace", icon='PASTEDOWN')
        row.operator(f"superskin.deform_paste_{kind}_weight_add", text="Add", icon='ADD')
        row.operator(f"superskin.deform_paste_{kind}_weight_subtract", text="Subtract", icon='REMOVE')


class SUPERSKIN_OT_open_copy_weight_options(bpy.types.Operator):
    """Open the Copy Weight options"""
    bl_idname = "superskin.open_copy_weight_options"
    bl_label = "Copy Weight"
    bl_options = {'INTERNAL'}

    @classmethod
    def description(cls, context, properties):
        obj = context.active_object
        if _is_mask_kind(obj):
            try:
                return f"Copy mask weights from \"{CoreFacade(context).active_layer_name()}\""
            except Exception:
                return "Copy mask weights from the active layer"
        bone = CoreFacade.active_vg_name_of(obj) if obj else ""
        if bone:
            return f"Copy vertex group weights from \"{bone}\""
        return "Copy vertex group weights from the active vertex group"

    def execute(self, context):
        return bpy.ops.wm.call_panel(name=SUPERSKIN_PT_copy_weight_options.bl_idname)


_classes = (
    SUPERSKIN_PT_copy_weight_options,
    SUPERSKIN_OT_open_copy_weight_options,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
