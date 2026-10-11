
import bpy

from ....interface.template_ui import SuperSkinListMixin
from ....interface.utils.utils import _has_layer_system
from ....interface.utils.icons import (
    get_mesh_init_icon_id, get_mesh_missing_icon_id, get_mesh_uninit_icon_id,
)


def _mesh_state_icon_kwargs(obj) -> dict:
    if not _has_layer_system(obj):
        icon_id, fallback = get_mesh_uninit_icon_id(), 'NONE'
    elif _armature_for_mesh(obj) is None:
        icon_id, fallback = get_mesh_missing_icon_id(), 'ERROR'
    else:
        icon_id, fallback = get_mesh_init_icon_id(), 'FILEBROWSER'
    if icon_id:
        return {"icon_value": icon_id}
    return {"icon": fallback}


def _is_listed_mesh(obj) -> bool:
    return _armature_for_mesh(obj) is not None or _has_layer_system(obj)



def _armature_for_mesh(mesh_obj):
    if not mesh_obj or mesh_obj.type != 'MESH':
        return None
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    return None


def _select_single(context, obj):
    for other in context.view_layer.objects:
        if other is not obj and other.select_get():
            other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


_last_mesh_name = ""


def get_effective_mesh(context):
    global _last_mesh_name
    obj = context.active_object
    if obj is None:
        return None
    if obj.type == 'MESH':
        if _armature_for_mesh(obj) is not None:
            _last_mesh_name = obj.name
        return obj
    if obj.type == 'ARMATURE':
        remembered = context.view_layer.objects.get(_last_mesh_name)
        if remembered is not None and _armature_for_mesh(remembered) == obj:
            return remembered
        bound = sorted(
            (o for o in context.view_layer.objects if _armature_for_mesh(o) == obj),
            key=lambda o: o.name,
        )
        return bound[0] if bound else None
    return None


def get_effective_armature(context):
    obj = context.active_object
    if obj and obj.type == 'ARMATURE':
        return obj
    return _armature_for_mesh(obj)


def run_with_object_active(context, target_obj, callback, *args):
    prev_active = context.view_layer.objects.active
    if prev_active is target_obj:
        return callback(*args)

    prev_selected = [o for o in context.view_layer.objects if o.select_get()]
    for o in prev_selected:
        o.select_set(False)
    target_obj.select_set(True)
    context.view_layer.objects.active = target_obj
    try:
        return callback(*args)
    finally:
        target_obj.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active



class SUPERSKIN_UL_bind_mesh_list(SuperSkinListMixin, bpy.types.UIList):

    def domain(self) -> str:
        return 'BIND_MESH'

    def get_item_key(self, item) -> str:
        return item.name

    def get_display_order(self, context, data):
        objects = data.objects
        return sorted(range(len(objects)), key=lambda i: objects[i].name)

    def extra_keep_predicate(self, context, data, item, original_idx) -> bool:
        return item.name in context.view_layer.objects and _is_listed_mesh(item)

    def is_selected(self, context, data, key: str) -> bool:
        return False

    def draw_main_icon(self, context, data, item) -> str:
        return _mesh_state_icon_kwargs(item).get("icon", 'NONE')

    def draw_main_icon_value(self, context, data, item) -> int:
        return _mesh_state_icon_kwargs(item).get("icon_value", 0)

    def get_row_operator_id(self, item=None) -> str:
        return "superskin.layer_viewer_select_mesh"

    def set_row_operator_props(self, op, item):
        op.mesh_name = item.name

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        op = layout.operator(
            self.get_row_operator_id(item), text=item.name, emboss=False,
            **_mesh_state_icon_kwargs(item),
        )
        self.set_row_operator_props(op, item)


def _get_bind_mesh_index(self):
    context = bpy.context
    mesh = get_effective_mesh(context)
    if mesh is None:
        return -1
    return context.scene.objects.find(mesh.name)


def _set_bind_mesh_index(self, value):
    objects = bpy.context.scene.objects
    if 0 <= value < len(objects) and objects[value].type == 'MESH':
        _select_single(bpy.context, objects[value])


class SUPERSKIN_PT_layer_viewer_mesh_options(bpy.types.Panel):
    """Choose the mesh to work on and manage its layer data"""
    bl_idname = "SUPERSKIN_PT_layer_viewer_mesh_options"
    bl_label = "Mesh Options"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 8

    def draw(self, context):
        layout = self.layout
        layout.template_list(
            "SUPERSKIN_UL_bind_mesh_list", "",
            context.scene, "objects",
            context.window_manager, "superskin_bind_mesh_index",
            rows=5,
        )

        layout.separator()
        row_bake = layout.row(align=True)
        row_bake.operator("superskin.layer_remove_data", text="Bake Active")
        row_bake.operator("superskin.layer_remove_all_data", text="Bake All")


class SUPERSKIN_OT_layer_viewer_select_mesh(bpy.types.Operator):
    """Work on this mesh"""
    bl_idname = "superskin.layer_viewer_select_mesh"
    bl_label = "Select Mesh"
    bl_options = {'INTERNAL', 'UNDO'}

    mesh_name: bpy.props.StringProperty()

    def execute(self, context):
        obj = context.view_layer.objects.get(self.mesh_name)
        if obj is None or obj.type != 'MESH':
            return {'CANCELLED'}
        _select_single(context, obj)
        return {'FINISHED'}



def draw_object_selectors(layout, context):
    mesh = get_effective_mesh(context)
    if not _is_listed_mesh(mesh):
        mesh = None

    row = layout.row(align=True)
    mesh_label = mesh.name if mesh else ""
    icon_kwargs = _mesh_state_icon_kwargs(mesh) if mesh else {"icon": 'NONE'}
    row.popover(SUPERSKIN_PT_layer_viewer_mesh_options.bl_idname, text=mesh_label, **icon_kwargs)
    return row



def register():
    bpy.types.WindowManager.superskin_bind_mesh_index = bpy.props.IntProperty(
        get=_get_bind_mesh_index, set=_set_bind_mesh_index, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(SUPERSKIN_UL_bind_mesh_list)
    bpy.utils.register_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    bpy.utils.register_class(SUPERSKIN_OT_layer_viewer_select_mesh)


def unregister():
    global _last_mesh_name
    _last_mesh_name = ""
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_viewer_select_mesh)
    bpy.utils.unregister_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    bpy.utils.unregister_class(SUPERSKIN_UL_bind_mesh_list)
    del bpy.types.WindowManager.superskin_bind_mesh_index
