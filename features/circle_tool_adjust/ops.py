"""Modal operator for the circle brush radius adjustment / select-tool toggle
gesture, plus the (merged-in) vertex-selection Grow/Shrink scroll gesture.

Radius/tool-toggle: single trigger (Alt+Shift+RMB), branching on drag
threshold like `weight_apply`'s `SUPERSKIN_OT_weight_gesture`:
  - Plain click (never crosses the drag threshold): toggles the active tool
    between Select Circle and Select Box.
  - Hold + drag: live-adjusts the circle-select brush radius, but only
    while Select Circle was the active tool at invoke -- Select Box (or
    anything else) has no radius to adjust, so the drag is a no-op in that
    case (the release still doesn't fall back to toggling the tool, since
    it wasn't a plain click).

Grow/Shrink: Alt+Ctrl+Scroll (`SUPERSKIN_OT_grow_shrink_selection`,
formerly the standalone `auto_grow` domain's click/hold-drag gesture,
merged into this domain -- see the domain's README). Each wheel notch is
now one independent, atomic step; there is no drag/preview/baseline-
snapshot machinery left to avoid, so this just wraps the native
`mesh.select_more()`/`mesh.select_less()` directly instead of the old
custom adjacency-BFS implementation.
"""

import bpy
import bmesh

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


# ==============================================================================
# Grow/Shrink Selection (Alt+Ctrl+Scroll) -- merged in from `auto_grow`
# ==============================================================================

def _selected_islands(bm):
    """Group currently-selected vertices into topologically-disjoint islands,
    connected only through edges whose both endpoints are selected. Two
    separate minimal loops (e.g. selected via two Alt+Click loop-selects that
    never touch) come back as two islands; a single loop or a contiguous
    patch comes back as one."""
    selected = {v for v in bm.verts if v.select}
    visited = set()
    islands = []
    for start in selected:
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        island = set()
        while stack:
            v = stack.pop()
            island.add(v)
            for e in v.link_edges:
                other = e.other_vert(v)
                if other in selected and other not in visited:
                    visited.add(other)
                    stack.append(other)
        islands.append(island)
    islands.sort(key=lambda island: min(v.index for v in island))
    return islands


class SUPERSKIN_OT_grow_shrink_selection(bpy.types.Operator):
    """One Grow (`mesh.select_more`) or Shrink (`mesh.select_less`) step per
    scroll-wheel notch. Each notch is a fully independent, atomic action --
    unlike the old `auto_grow` domain's click/hold-drag gesture, there is no
    drag preview or baseline snapshot to recompute from, so this wraps the
    native operators directly instead of the old custom adjacency-BFS
    grow/shrink implementation."""
    bl_idname = "superskin.grow_shrink_selection"
    bl_label = "Grow/Shrink Selection"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.IntProperty(
        name="Direction", default=1,
        description="+1 grows the selection, -1 shrinks it",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode == 'EDIT_MESH'

    def execute(self, context):
        if self.direction > 0:
            bpy.ops.mesh.select_more()
            return {'FINISHED'}

        # Shrink -- guard against vanishing the selection entirely. A naive
        # "selected count <= 1" check (mirroring the old
        # SUPERSKIN_OT_safe_shrink in features/controller/ops_tools.py, itself
        # dead code -- see that domain's README) only reproduces Blender's
        # native single-vertex protection. It does nothing for a vertex loop:
        # every vertex in a thin loop is a boundary vertex, so
        # mesh.select_less() can collapse the whole loop to an empty
        # selection in one step, with no way back via select_more(). Predict
        # the outcome first using bmesh.ops.region_extend(use_contract=True),
        # the same primitive BM_mesh_select_less() uses internally to find
        # the boundary to deselect.
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        geom = (
            [v for v in bm.verts if v.select]
            + [e for e in bm.edges if e.select]
            + [f for f in bm.faces if f.select]
        )
        if not geom:
            return {'CANCELLED'}

        use_faces = context.tool_settings.mesh_select_mode[2]
        boundary = set(bmesh.ops.region_extend(
            bm, geom=geom, use_contract=True, use_faces=use_faces, use_face_step=True,
        )['geom'])

        if not all(el in boundary for el in geom):
            # At least one element survives -- a normal, safe partial shrink.
            bpy.ops.mesh.select_less()
            return {'FINISHED'}

        # Every currently-selected element would be removed. If this is one
        # connected island (a single loop or patch), there's no partial state
        # to fall back to -- block entirely, same as the "selected count <= 1"
        # case this replaces.
        #
        # But if it's several disjoint islands (e.g. two separate minimal
        # loops selected via two Alt+Click loop-selects that never touch),
        # region_extend's boundary check is purely local per island, so it
        # reports the WHOLE selection as boundary-only even though the
        # islands are independent -- calling the real select_less() here
        # would wipe every island at once. Instead, peel away exactly one
        # island and leave the rest untouched, so N selected loops take N
        # shrink steps to reach one remaining loop, which then blocks --
        # mirroring Maya's per-loop shrink instead of an all-or-nothing wipe.
        #
        # A lone selected vertex (island size 1) is never a peel candidate,
        # even alongside other islands: e.g. 3 separate single vertices
        # picked at unrelated spots on the mesh are 3 size-1 islands, and the
        # user expects shrink to leave all 3 alone (mirrors Blender's own
        # single-vertex protection, just applied per-vertex instead of only
        # when exactly one vertex total is selected) rather than quietly
        # deleting one of them as if it were a whole loop. Only islands with
        # more than one vertex (an actual loop/patch structure) get peeled.
        islands = _selected_islands(bm)
        if len(islands) <= 1:
            return {'CANCELLED'}

        peelable = [island for island in islands if len(island) > 1]
        if not peelable:
            return {'CANCELLED'}

        for v in peelable[0]:
            v.select = False
        bm.select_flush_mode()
        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}


_classes = (SUPERSKIN_OT_circle_tool_adjust_radius, SUPERSKIN_OT_grow_shrink_selection)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
