"""Modal operator for the circle brush radius adjustment / select-tool toggle
gesture (Alt+Shift+RMB).

Single trigger, branching on drag threshold like `weight_apply`'s
`SUPERSKIN_OT_weight_gesture`:
  - Plain click (never crosses the drag threshold): toggles the active tool
    between Select Circle and Select Box.
  - Hold + drag: live-adjusts the circle-select brush radius, but only
    while Select Circle was the active tool at invoke -- Select Box (or
    anything else) has no radius to adjust, so the drag is a no-op in that
    case (the release still doesn't fall back to toggling the tool, since
    it wasn't a plain click).

Grow/Shrink Selection (formerly merged in here from the `auto_grow` domain)
has moved to `features/vertex_selector/` — see that domain's README. It
now also owns Pick Walk, since both directly mutate the active selection,
unlike this domain's tool-configuration gesture.
"""

import bpy

_CIRCLE_TOOL_IDNAME = 'builtin.select_circle'
_BOX_TOOL_IDNAME = 'builtin.select_box'

_DRAG_THRESHOLD = 4  # pixels before a click becomes a drag (matches weight_apply's gesture)


def _get_active_tool_idname(context):
    """Return the active VIEW_3D/EDIT_MESH tool's idname, or None."""
    if context.workspace is None:
        return None
    tool = context.workspace.tools.from_space_view3d_mode('EDIT_MESH', create=False)
    return tool.idname if tool else None


class SUPERSKIN_OT_circle_tool_adjust_radius(bpy.types.Operator):
    """Alt+Shift+RMB: hold+drag resizes the circle selection brush,
    auto-switching to Select Circle first if some other tool is active; a
    plain click toggles between Select Circle and Select Box."""
    bl_idname = "superskin.circle_tool_adjust_radius"
    bl_label = "Adjust Circle Radius / Toggle Select Tool"
    bl_options = {'REGISTER', 'UNDO'}

    _initial_x: int = 0
    _initial_y: int = 0
    _backup_radius: int = 30

    def modal(self, context, event):
        prefs = context.window_manager.superskin_circle_tool_adjust_prefs

        if event.type == 'MOUSEMOVE':
            delta = event.mouse_x - self._initial_x
            if not self._is_dragging and abs(delta) > _DRAG_THRESHOLD:
                self._is_dragging = True
                if not self._adjusting:
                    # Holding to drag with a non-Circle tool active makes
                    # the user's intent to adjust the radius unambiguous --
                    # switch to Select Circle immediately instead of
                    # silently consuming the drag as a no-op.
                    bpy.ops.wm.tool_set_by_id(name=_CIRCLE_TOOL_IDNAME)
                    self._active_tool = _CIRCLE_TOOL_IDNAME
                    self._adjusting = True
            if self._is_dragging and self._adjusting:
                new_radius = max(1, min(300, prefs.brush_radius_value + int(delta * 0.3)))
                prefs.brush_radius_value = new_radius
                context.area.header_text_set(f"Brush Radius: {new_radius}")
                context.window.cursor_warp(self._initial_x, self._initial_y)

        elif event.type == self._trigger_type and event.value == 'RELEASE':
            context.window.cursor_modal_restore()
            context.area.header_text_set(None)
            if not self._is_dragging:
                # Plain click, never dragged -- toggle Select Circle <-> Select Box.
                target = (
                    _BOX_TOOL_IDNAME if self._active_tool == _CIRCLE_TOOL_IDNAME
                    else _CIRCLE_TOOL_IDNAME
                )
                bpy.ops.wm.tool_set_by_id(name=target)
                return {'FINISHED'}
            return {'FINISHED'} if self._adjusting else {'CANCELLED'}

        elif event.type == 'ESC':
            context.window.cursor_modal_restore()
            if self._adjusting:
                prefs.brush_radius_value = self._backup_radius
            context.area.header_text_set(None)
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        if context.space_data.type != 'VIEW_3D':
            return {'CANCELLED'}
        prefs = context.window_manager.superskin_circle_tool_adjust_prefs
        self._trigger_type = event.type
        self._initial_x = event.mouse_x
        self._initial_y = event.mouse_y
        self._backup_radius = prefs.brush_radius_value
        self._is_dragging = False
        self._active_tool = _get_active_tool_idname(context)
        # Just the starting value -- Select Box (or any other tool) has no
        # radius, but a plain click should still fall through to the
        # tool-toggle branch regardless of which tool is active, so this
        # isn't gated here. If the drag threshold is crossed while this is
        # still False, modal()'s MOUSEMOVE handler auto-switches to Select
        # Circle and flips this to True instead of leaving the drag a no-op.
        self._adjusting = (self._active_tool == _CIRCLE_TOOL_IDNAME)
        context.window.cursor_modal_set('NONE')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


_classes = (SUPERSKIN_OT_circle_tool_adjust_radius,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
