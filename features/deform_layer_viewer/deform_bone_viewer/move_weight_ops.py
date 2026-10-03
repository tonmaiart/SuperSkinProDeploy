
import bpy
from ....core.facade import CoreFacade
from ....interface.utils.op_exec import run_domain_via_unified


class SUPERSKIN_PG_move_weight_by_name(bpy.types.PropertyGroup):
    search: bpy.props.StringProperty(
        name="Search", default="_R",
        description="Text that identifies the source bones",
    )
    replace: bpy.props.StringProperty(
        name="Replace", default="_L",
        description="Replacement that turns a source bone name into its target bone name",
    )
    factor: bpy.props.FloatProperty(
        name="Amount", default=1.0, min=0.0, max=1.0, step=5, precision=3,
        subtype='FACTOR',
        description="Share of each pair's combined weight given to the target bone",
    )


class SUPERSKIN_OT_move_weight_by_name_swap(bpy.types.Operator):
    bl_idname = "superskin.move_weight_by_name_swap"
    bl_label = "Swap"
    bl_description = "Swap the Search and Replace text"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        prefs = context.window_manager.superskin_move_weight_prefs
        prefs.search, prefs.replace = prefs.replace, prefs.search
        return {'FINISHED'}


class SUPERSKIN_OT_move_weight_by_name(bpy.types.Operator):
    bl_idname = "superskin.move_weight_by_name"
    bl_label = "Move Weight by Name"
    bl_description = (
        "Move weight from every bone containing Search to the bone named with Replace, "
        "on the selected vertices (or the whole mesh if none are selected)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (CoreFacade.is_system_activated()
                and obj is not None and obj.type == 'MESH'
                and "ss_layers_meta" in obj.data)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300, confirm_text="Move")

    def draw(self, context):
        prefs = context.window_manager.superskin_move_weight_prefs
        col = self.layout.column()
        col.prop(prefs, "search")
        col.prop(prefs, "replace")
        col.operator("superskin.move_weight_by_name_swap", icon='ARROW_LEFTRIGHT')
        col.separator()
        col.prop(prefs, "factor", slider=True)

    def execute(self, context):
        return run_domain_via_unified(context, "deform_layer_viewer", "move_weight_by_name", op=self)


_classes = (
    SUPERSKIN_PG_move_weight_by_name,
    SUPERSKIN_OT_move_weight_by_name_swap,
    SUPERSKIN_OT_move_weight_by_name,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_move_weight_prefs = bpy.props.PointerProperty(
        type=SUPERSKIN_PG_move_weight_by_name, options={'SKIP_SAVE'},
    )


def unregister():
    try:
        del bpy.types.WindowManager.superskin_move_weight_prefs
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
