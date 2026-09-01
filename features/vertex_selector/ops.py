"""Operators for VertexSelector: Grow/Shrink Selection (hop-count only).

`SUPERSKIN_OT_grow_shrink_selection` (moved here from the former
`features/circle_tool_adjust/`): Alt+Ctrl+Scroll, each wheel notch wraps
native `mesh.select_more()`/`mesh.select_less()` directly -- no drag/
preview/baseline-snapshot machinery to protect, see the domain's README.

Pick Walk and the geodesic-distance Grow/Shrink mode (formerly here) have
been removed entirely, along with their keymap bindings and `logic.py`
helpers.
"""

import bpy
import bmesh


# ==============================================================================
# Grow/Shrink Selection
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
    native operators directly instead of a custom adjacency-BFS
    implementation."""
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


_classes = (SUPERSKIN_OT_grow_shrink_selection,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
