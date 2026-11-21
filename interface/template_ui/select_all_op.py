
import bpy

from .base_list import list_class_for
from .select_ops import get_adapter

_keymaps = []


def select_all_with_adapter(context, domain) -> bool:
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return False
    adapter = get_adapter(domain)
    _selected, last_key, _history = adapter.read_selection(context, obj)
    visual_order = list(adapter.get_keys_in_visual_order(context, obj))
    if not visual_order:
        return False
    if last_key in visual_order:
        visual_order.remove(last_key)
        visual_order.append(last_key)
    adapter.write_selection(context, obj, set(visual_order), last_key, visual_order)
    return True


class SUPERSKIN_OT_template_list_select_all(bpy.types.Operator):
    """Select every visible row of the list under the mouse"""
    bl_idname = "superskin.template_list_select_all"
    bl_label = "Select All Rows"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ui_list = getattr(context, "ui_list", None)
        if ui_list is None:
            return False
        list_cls = list_class_for(ui_list.bl_idname)
        return list_cls is not None and list_cls.supports_select_all()

    def execute(self, context):
        list_cls = list_class_for(context.ui_list.bl_idname)
        if not list_cls.select_all_rows(context):
            return {'CANCELLED'}
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_template_list_select_all)
    kc = bpy.context.window_manager.keyconfigs.addon
    if not kc:
        return
    km = kc.keymaps.new(name='User Interface', space_type='EMPTY')
    kmi = km.keymap_items.new(
        SUPERSKIN_OT_template_list_select_all.bl_idname, type='A', value='PRESS', ctrl=True,
    )
    _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()
    bpy.utils.unregister_class(SUPERSKIN_OT_template_list_select_all)
