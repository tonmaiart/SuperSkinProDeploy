
import bpy

from ....interface.template_ui import (
    SuperSkinListMixin, draw_list_with_sidebar, resolve_row_click_selection,
)
from ....interface.utils.utils import _has_layer_system
from ....interface.utils.icons import (
    get_mesh_init_icon_id, get_mesh_missing_icon_id, get_mesh_toggle_icon_id,
    get_mesh_uninit_icon_id,
)
from ....interface.utils.popover_width import fit_width_units, open_popover_at_width


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


def _is_shown_in_mesh_list(context, obj) -> bool:
    if getattr(context.window_manager, "superskin_bind_mesh_only_layer_data", True):
        return _has_layer_system(obj)
    return _is_listed_mesh(obj)



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



class _BindMeshListHooks(SuperSkinListMixin):
    """Row hooks shared by the bind mesh UIList and the click operator, which replays the
    list's filtered order outside the draw call."""

    left_zone_factor = 0.01

    def domain(self) -> str:
        return 'BIND_MESH'

    def get_item_key(self, item) -> str:
        return item.name

    def get_display_order(self, context, data):
        objects = data.objects
        return sorted(range(len(objects)), key=lambda i: objects[i].name)

    def extra_keep_predicate(self, context, data, item, original_idx) -> bool:
        return item.name in context.view_layer.objects and _is_shown_in_mesh_list(context, item)

    def _ensure_filter_dependencies(self, context, data):
        _ = context.window_manager.superskin_bind_mesh_only_layer_data

    def is_selected(self, context, data, key: str) -> bool:
        obj = context.view_layer.objects.get(key)
        return obj is not None and obj.select_get()

    def draw_main_icon(self, context, data, item) -> str:
        return _mesh_state_icon_kwargs(item).get("icon", 'NONE')

    def draw_main_icon_value(self, context, data, item) -> int:
        return _mesh_state_icon_kwargs(item).get("icon_value", 0)

    def get_row_operator_id(self, item=None) -> str:
        return "superskin.layer_viewer_select_mesh"

    def set_row_operator_props(self, op, item):
        op.mesh_name = item.name

    @classmethod
    def select_all_rows(cls, context) -> bool:
        global _last_clicked_mesh, _mesh_click_history
        view_objects = context.view_layer.objects
        visible = _bind_mesh_hooks.compute_visible_keys(context, context.scene, "objects")
        listed = [view_objects[k] for k in visible if k in view_objects]
        if not listed:
            return False
        for obj in listed:
            obj.select_set(True)
        _mesh_click_history = [obj.name for obj in listed]
        if _last_clicked_mesh not in _mesh_click_history:
            _last_clicked_mesh = _mesh_click_history[-1]
        return True


_bind_mesh_hooks = _BindMeshListHooks()


class SUPERSKIN_UL_bind_mesh_list(_BindMeshListHooks, bpy.types.UIList):
    pass


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
        draw_list_with_sidebar(
            layout, context,
            ui_list_idname="SUPERSKIN_UL_bind_mesh_list",
            data=context.scene,
            collection_prop="objects",
            active_data=context.window_manager,
            active_prop="superskin_bind_mesh_index",
            rows=8,
            button_defs=[
                ("operator", "superskin.layer_viewer_toggle_mesh_filter",
                 _mesh_filter_icon, "", {}, _mesh_filter_only_layer_data),
            ],
        )
        layout.separator()
        row_bake = layout.row(align=True)
        row_bake.operator("superskin.layer_remove_selected_data", text="Bake Selected")
        row_bake.operator("superskin.layer_remove_all_data", text="Bake All")


_MESH_POPOVER_MIN_UNITS = 8
_MESH_POPOVER_MAX_UNITS = 32
_MESH_ROW_CHROME_UNITS = 6.0


class SUPERSKIN_OT_layer_viewer_open_mesh_options(bpy.types.Operator):
    """Choose the mesh to work on and manage its layer data"""
    bl_idname = "superskin.layer_viewer_open_mesh_options"
    bl_label = "Mesh Options"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        names = [
            obj.name for obj in context.view_layer.objects
            if obj.type == 'MESH' and _is_listed_mesh(obj)
        ]
        units = fit_width_units(
            context, names, _MESH_ROW_CHROME_UNITS,
            _MESH_POPOVER_MIN_UNITS, _MESH_POPOVER_MAX_UNITS,
        )
        open_popover_at_width(SUPERSKIN_PT_layer_viewer_mesh_options, units)
        return {'FINISHED'}


def _mesh_filter_only_layer_data(context):
    return context.window_manager.superskin_bind_mesh_only_layer_data


def _mesh_filter_icon(context):
    if _mesh_filter_only_layer_data(context):
        icon_id = get_mesh_toggle_icon_id()
        return icon_id if icon_id else 'FILEBROWSER'
    return 'LONGDISPLAY'


class SUPERSKIN_OT_layer_viewer_toggle_mesh_filter(bpy.types.Operator):
    """Toggle listing only meshes that already have layer data"""
    bl_idname = "superskin.layer_viewer_toggle_mesh_filter"
    bl_label = "Show Only Layer Data"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        wm.superskin_bind_mesh_only_layer_data = not wm.superskin_bind_mesh_only_layer_data
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


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

    def invoke(self, context, event):
        global _last_clicked_mesh, _mesh_click_history
        if not (event.ctrl or event.shift):
            _last_clicked_mesh, _mesh_click_history = self.mesh_name, [self.mesh_name]
            return self.execute(context)
        view_objects = context.view_layer.objects
        clicked = view_objects.get(self.mesh_name)
        if clicked is None or clicked.type != 'MESH':
            return {'CANCELLED'}

        visible = _bind_mesh_hooks.compute_visible_keys(context, context.scene, "objects")
        listed = [view_objects[k] for k in visible if k in view_objects]
        visual_order = [obj.name for obj in listed]
        if clicked.name not in visual_order:
            listed.append(clicked)
            visual_order.append(clicked.name)
        selected = {obj.name for obj in listed if obj.select_get()}
        history = [name for name in _mesh_click_history if name in selected]
        last = _last_clicked_mesh if _last_clicked_mesh in visual_order else None
        selected, last, history = resolve_row_click_selection(
            selected, last, history, clicked.name, visual_order, event,
        )
        _last_clicked_mesh, _mesh_click_history = last, history

        for obj in listed:
            obj.select_set(obj.name in selected)
        if clicked.name in selected:
            view_objects.active = clicked
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


_last_clicked_mesh = None
_mesh_click_history = []



def draw_object_selectors(layout, context):
    mesh = get_effective_mesh(context)
    if not _is_listed_mesh(mesh):
        mesh = None

    row = layout.row(align=True)
    mesh_label = mesh.name if mesh else ""
    icon_kwargs = _mesh_state_icon_kwargs(mesh) if mesh else {"icon": 'NONE'}
    row.operator(
        SUPERSKIN_OT_layer_viewer_open_mesh_options.bl_idname, text=mesh_label, **icon_kwargs,
    )
    return row



def register():
    bpy.types.WindowManager.superskin_bind_mesh_only_layer_data = bpy.props.BoolProperty(
        name="Show Only Layer Data",
        description="List only meshes that already have layer data",
        default=True,
        options={'SKIP_SAVE'},
    )
    bpy.types.WindowManager.superskin_bind_mesh_index = bpy.props.IntProperty(
        get=_get_bind_mesh_index, set=_set_bind_mesh_index, options={'SKIP_SAVE'},
    )
    bpy.utils.register_class(SUPERSKIN_UL_bind_mesh_list)
    bpy.utils.register_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    bpy.utils.register_class(SUPERSKIN_OT_layer_viewer_select_mesh)
    bpy.utils.register_class(SUPERSKIN_OT_layer_viewer_open_mesh_options)
    bpy.utils.register_class(SUPERSKIN_OT_layer_viewer_toggle_mesh_filter)


def unregister():
    global _last_mesh_name
    _last_mesh_name = ""
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_viewer_toggle_mesh_filter)
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_viewer_open_mesh_options)
    bpy.utils.unregister_class(SUPERSKIN_OT_layer_viewer_select_mesh)
    bpy.utils.unregister_class(SUPERSKIN_PT_layer_viewer_mesh_options)
    bpy.utils.unregister_class(SUPERSKIN_UL_bind_mesh_list)
    del bpy.types.WindowManager.superskin_bind_mesh_index
    del bpy.types.WindowManager.superskin_bind_mesh_only_layer_data
