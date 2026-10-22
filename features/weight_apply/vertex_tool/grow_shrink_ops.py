
import bpy
import numpy as np

from .. import mesh_flags
from .common import poll_session_mesh



def _read_topology(mesh):
    n_verts, n_edges, n_polys = len(mesh.vertices), len(mesh.edges), len(mesh.polygons)
    sel = mesh_flags.vertex_select(mesh)
    vert_hide = mesh_flags.vertex_hide(mesh)
    edge_verts = np.zeros(n_edges * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_verts)
    edge_hide = mesh_flags.edge_hide(mesh)
    loop_start = np.zeros(n_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_start)
    loop_total = np.zeros(n_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", loop_total)
    poly_hide = mesh_flags.polygon_hide(mesh)
    corner_vert = np.zeros(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", corner_vert)
    return {
        "sel": sel, "vert_hide": vert_hide,
        "edge_verts": edge_verts.reshape(-1, 2), "edge_hide": edge_hide,
        "loop_start": loop_start, "loop_total": loop_total, "poly_hide": poly_hide,
        "corner_vert": corner_vert,
    }


def _touching(mask, topo):
    out = np.zeros_like(mask)
    ev = topo["edge_verts"]
    if len(ev):
        hit = (mask[ev[:, 0]] | mask[ev[:, 1]]) & ~topo["edge_hide"]
        out[ev[hit].ravel()] = True
    corner_vert, loop_start = topo["corner_vert"], topo["loop_start"]
    if len(loop_start):
        order = np.argsort(loop_start, kind="stable")
        starts, totals = loop_start[order], topo["loop_total"][order]
        face_hit = np.logical_or.reduceat(mask[corner_vert], starts) & ~topo["poly_hide"][order]
        corners = np.repeat(starts, totals) + (np.arange(totals.sum()) - np.repeat(np.cumsum(totals) - totals, totals))
        out[corner_vert[corners[np.repeat(face_hit, totals)]]] = True
    return out



def grow_step(topo):
    sel = topo["sel"]
    return sel | (_touching(sel, topo) & ~topo["vert_hide"])


def shrink_step(topo):
    sel = topo["sel"]
    return sel & ~_touching(~sel, topo)



class SUPERSKIN_OT_grow_shrink_selection(bpy.types.Operator):
    """Grow or shrink the vertex selection"""
    bl_idname = "superskin.grow_shrink_selection"
    bl_label = "Grow/Shrink Selection"
    bl_options = {'UNDO'}

    direction: bpy.props.IntProperty(
        name="Direction", default=1,
        description="+1 grows the selection, -1 shrinks it",
    )

    @classmethod
    def poll(cls, context):
        return poll_session_mesh(context)

    def execute(self, context):
        mesh = context.active_object.data
        topo = _read_topology(mesh)
        if not topo["sel"].any():
            return {'CANCELLED'}
        result = grow_step(topo) if self.direction > 0 else shrink_step(topo)
        if np.array_equal(result, topo["sel"]):
            return {'CANCELLED'}
        mesh_flags.write_vertex_select(mesh, result)
        mesh.update()
        return {'FINISHED'}


class SUPERSKIN_OT_grow_selection(SUPERSKIN_OT_grow_shrink_selection):
    """Grow the vertex selection by one step"""
    bl_idname = "superskin.grow_selection"
    bl_label = "Grow Selection"

    def execute(self, context):
        self.direction = 1
        return super().execute(context)


class SUPERSKIN_OT_shrink_selection(SUPERSKIN_OT_grow_shrink_selection):
    """Shrink the vertex selection by one step"""
    bl_idname = "superskin.shrink_selection"
    bl_label = "Shrink Selection"

    def execute(self, context):
        self.direction = -1
        return super().execute(context)


_classes = (SUPERSKIN_OT_grow_shrink_selection, SUPERSKIN_OT_grow_selection, SUPERSKIN_OT_shrink_selection)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
