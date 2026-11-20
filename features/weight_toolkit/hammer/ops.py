
import bpy

from ....core.facade import CoreFacade
from ....interface.template_ui import SuperSkinListMixin
from ....interface.utils.icons import get_hammer_icon_id
from ....interface.utils.op_exec import run_domain_via_unified
from . import logic

_ACTIVE = "ACTIVE"
_MIN_PERCENT = 1.0
_PANEL = "SUPERSKIN_PT_hammer_popup"
_LIST = "SUPERSKIN_UL_hammer_influences"

_message = None
_writing_index = False


def _percent(score, total):
    pct = 100.0 * score / total
    return "<1%" if pct < _MIN_PERCENT else f"{round(pct)}%"


def _row_id(row):
    return row.layer_index, row.is_mask


def _current_id(context):
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return None
    layer = CoreFacade(context).get_active_layer_index()
    return layer, bool(obj.superskin_storage.active_is_mask)


def _pick_row(rows, wanted):
    ids = [_row_id(r) for r in rows]
    return ids.index(wanted) if wanted in ids else -1


def _set_row_index(wm, index):
    global _writing_index
    _writing_index = True
    try:
        wm.superskin_hammer_row_index = index
    finally:
        _writing_index = False


def _on_row_index(wm, context):
    if _writing_index:
        return
    bpy.ops.superskin.hammer_snap_influence('INVOKE_DEFAULT', row=wm.superskin_hammer_row_index)


def _refresh(context, keep=None):
    global _message
    try:
        targets = logic.rank_targets(CoreFacade(context))
        _message = None
    except ValueError as e:
        targets, _message = [], str(e)
    wm = context.window_manager
    rows = wm.superskin_hammer_rows
    rows.clear()
    total = sum(t.score for t in targets) or 1.0
    current = _current_id(context)
    for target in targets:
        is_mask = target.channel == logic.MASK
        if 100.0 * target.score / total < _MIN_PERCENT:
            continue
        row = rows.add()
        row.row_index = len(rows) - 1
        row.name = "Mask" if is_mask else "Bone"
        row.is_mask = is_mask
        row.layer_index = target.layer_index
        row.layer_name = target.name
        row.percent = _percent(target.score, total)
    ids = [_row_id(r) for r in rows]
    _set_row_index(wm, _pick_row(rows, keep if keep in ids else current))


def _row_key(row) -> str:
    return f"{row.layer_index}:{logic.MASK if row.is_mask else logic.WEIGHT}"


class SSPHammerRow(bpy.types.PropertyGroup):
    row_index: bpy.props.IntProperty()
    is_mask: bpy.props.BoolProperty()
    layer_index: bpy.props.IntProperty()
    layer_name: bpy.props.StringProperty()
    percent: bpy.props.StringProperty()


class SUPERSKIN_UL_hammer_influences(SuperSkinListMixin, bpy.types.UIList):
    def domain(self) -> str:
        return 'HAMMER'

    def get_item_key(self, item) -> str:
        return f"{item.layer_index}:{item.name}"

    def get_display_order(self, context, data):
        return list(range(len(data.superskin_hammer_rows)))

    def is_selected(self, context, data, key: str) -> bool:
        return False

    def get_search_text(self, item) -> str:
        return item.layer_name

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if not item:
            return
        row = layout.row()
        row.label(text=item.layer_name, icon='MOD_MASK' if item.is_mask else 'BONE_DATA')
        percent = row.row()
        percent.ui_units_x = 2.2
        percent.alignment = 'RIGHT'
        percent.label(text=item.percent)


class SUPERSKIN_PT_hammer_popup(bpy.types.Panel):
    """Hammer the selection and see which bone or mask is most likely deforming it"""
    bl_idname = _PANEL
    bl_label = "Hammer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 9

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        rows = wm.superskin_hammer_rows
        if _message:
            layout.label(text=_message, icon='INFO')
        else:
            layout.template_list(_LIST, "", wm, "superskin_hammer_rows", wm, "superskin_hammer_row_index",
                                 rows=4)
        index = wm.superskin_hammer_row_index
        chosen = rows[index] if 0 <= index < len(rows) else None
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = chosen is not None
        row.operator_context = 'EXEC_DEFAULT'
        icon_id = get_hammer_icon_id()
        icon = {"icon_value": icon_id} if icon_id else {"icon": 'MOD_SMOOTH'}
        op = row.operator("mesh.ssp_inmesh_hammer", text="Hammer Selected", **icon)
        if chosen is not None:
            op.target = _row_key(chosen)
            op.refresh = True


class SUPERSKIN_OT_hammer_snap_influence(bpy.types.Operator):
    """Make this layer active and select this bone or its mask"""
    bl_idname = "superskin.hammer_snap_influence"
    bl_label = "Go to Influence"
    bl_options = {'INTERNAL', 'UNDO'}

    row: bpy.props.IntProperty()

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        wm = context.window_manager
        rows = wm.superskin_hammer_rows
        obj = context.active_object
        if not (0 <= self.row < len(rows)) or obj is None or obj.type != 'MESH':
            return {'CANCELLED'}
        item = rows[self.row]
        from ...deform_layer_viewer.layer_viewer.public_api import select_layer_channel
        if not select_layer_channel(context, obj, item.layer_index, item.is_mask):
            self.report({'WARNING'}, f"Could not switch to {item.layer_name} ({item.name})")
        _set_row_index(wm, self.row)
        if context.area is not None:
            context.area.tag_redraw()
        return {'FINISHED'}


class MESH_OT_ssp_inmesh_hammer(bpy.types.Operator):
    bl_idname = "mesh.ssp_inmesh_hammer"
    bl_label = "Hammer"
    bl_description = "Average selected vertex weights from neighbors on active layer"
    bl_options = {'UNDO'}

    target: bpy.props.StringProperty(name="Layer", default=_ACTIVE, options={'HIDDEN'})
    refresh: bpy.props.BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (context.active_object is not None and
                context.active_object.type == 'MESH')

    def invoke(self, context, event):
        bpy.ops.superskin.open_hammer_popup()
        return {'CANCELLED'}

    def execute(self, context):
        wm = context.window_manager
        index = wm.superskin_hammer_row_index
        chosen = _row_id(wm.superskin_hammer_rows[index]) if 0 <= index < len(wm.superskin_hammer_rows) else None
        if self.target == _ACTIVE:
            logic.set_target(None)
        else:
            idx, channel = self.target.split(":")
            logic.set_target(int(idx), channel)
        try:
            result = run_domain_via_unified(context, "weight_toolkit", "hammer", op=self)
        finally:
            logic.set_target(None)
        if self.refresh and 'FINISHED' in result:
            _refresh(context, keep=chosen)
        return result


class SUPERSKIN_OT_open_hammer_popup(bpy.types.Operator):
    bl_idname = "superskin.open_hammer_popup"
    bl_label = "Hammer"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return MESH_OT_ssp_inmesh_hammer.poll(context)

    @classmethod
    def description(cls, context, properties):
        return MESH_OT_ssp_inmesh_hammer.bl_description

    def execute(self, context):
        _refresh(context)
        bpy.ops.wm.call_panel(name=_PANEL, keep_open=True)
        return {'FINISHED'}


_CLASSES = (SSPHammerRow, SUPERSKIN_UL_hammer_influences, SUPERSKIN_PT_hammer_popup,
            SUPERSKIN_OT_hammer_snap_influence, MESH_OT_ssp_inmesh_hammer, SUPERSKIN_OT_open_hammer_popup)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_hammer_rows = bpy.props.CollectionProperty(type=SSPHammerRow)
    bpy.types.WindowManager.superskin_hammer_row_index = bpy.props.IntProperty(update=_on_row_index)


def unregister():
    del bpy.types.WindowManager.superskin_hammer_row_index
    del bpy.types.WindowManager.superskin_hammer_rows
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
