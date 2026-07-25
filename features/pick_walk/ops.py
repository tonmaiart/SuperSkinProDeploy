"""Modal operator for Pick Walk Selection (Alt+Ctrl+MMB hold+drag).

Maya-style pick walk: while held, every currently-selected vertex/edge/face
(depending on the active select mode) shifts to its topological neighbor
most aligned with the drag direction, replacing the whole selection at once
so a selected loop shifts sideways instead of growing/shrinking (see
`logic.step_selection()`). The raw mouse delta is snapped to the nearest
cardinal direction (up/down/left/right, `logic.snap_to_cardinal_direction()`)
before being used -- a deliberately simple, axis-locked read of "which way
did the mouse move" rather than its exact angle. Each `step_pixels` (from
`superskin_pick_walk_prefs`) of continued drag performs one more hop, so a
single hold+drag can walk several loops over without releasing and
re-triggering.
"""

import bpy
import bmesh
from mathutils import Vector

from . import logic


class SUPERSKIN_OT_pick_walk(bpy.types.Operator):
    """Alt+Ctrl+MMB hold+drag: shift the selection along the mesh in the
    drag direction, Maya pick-walk style."""
    bl_idname = "superskin.pick_walk"
    bl_label = "Pick Walk Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'

    @staticmethod
    def _snapshot_selection(bm):
        return {
            'VERT': {v.index for v in bm.verts if v.select},
            'EDGE': {e.index for e in bm.edges if e.select},
            'FACE': {f.index for f in bm.faces if f.select},
        }

    @staticmethod
    def _restore_selection(obj, snapshot):
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        for v in bm.verts:
            v.select = v.index in snapshot['VERT']
        for e in bm.edges:
            e.select = e.index in snapshot['EDGE']
        for f in bm.faces:
            f.select = f.index in snapshot['FACE']
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            delta = Vector((
                event.mouse_x - self._ref_x,
                event.mouse_y - self._ref_y,
            ))
            prefs = context.window_manager.superskin_pick_walk_prefs
            if delta.length >= prefs.step_pixels:
                obj = context.active_object
                bm = bmesh.from_edit_mesh(obj.data)
                mode_key = logic.select_mode_key(context.tool_settings.mesh_select_mode)
                direction = logic.snap_to_cardinal_direction(delta)
                if logic.step_selection(context, obj, bm, mode_key, direction):
                    self._ref_x = event.mouse_x
                    self._ref_y = event.mouse_y

        elif event.type == self._trigger_type and event.value == 'RELEASE':
            context.area.header_text_set(None)
            return {'FINISHED'}

        elif event.type == 'ESC':
            context.area.header_text_set(None)
            self._restore_selection(context.active_object, self._original_selection)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        if context.space_data.type != 'VIEW_3D':
            return {'CANCELLED'}
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        self._original_selection = self._snapshot_selection(bm)
        self._trigger_type = event.type
        self._ref_x = event.mouse_x
        self._ref_y = event.mouse_y
        context.area.header_text_set("Pick Walk: drag to shift selection")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


_classes = (SUPERSKIN_OT_pick_walk,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
