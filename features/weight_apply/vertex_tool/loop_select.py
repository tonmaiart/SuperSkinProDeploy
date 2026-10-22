
import bmesh
import bpy
import numpy as np

from .. import mesh_flags
from . import pick
from .common import poll_session_mesh


def _visible_edges(vert):
    return [e for e in vert.link_edges if not e.hide]


def _visible_faces(edge):
    return {f for f in edge.link_faces if not f.hide}


def _walk_loop(start_edge):
    loop = {start_edge}
    for direction_vert in start_edge.verts:
        edge, vert = start_edge, direction_vert
        while True:
            edges = _visible_edges(vert)
            if len(edges) != 4:
                break
            faces = _visible_faces(edge)
            nxt = [e for e in edges if e is not edge and not (faces & _visible_faces(e))]
            if len(nxt) != 1:
                break
            edge = nxt[0]
            if edge in loop:
                break
            loop.add(edge)
            vert = edge.other_vert(vert)
    return loop


_loop_cache_key = None
_loop_cache = None


def _nearest_screen_edge(face_verts, sx, sy, mouse):
    mx, my = mouse
    best, best_dist = None, None
    for a, b in zip(face_verts, face_verts[1:] + face_verts[:1]):
        ex, ey = sx[b] - sx[a], sy[b] - sy[a]
        length2 = ex * ex + ey * ey
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((mx - sx[a]) * ex + (my - sy[a]) * ey) / length2))
        dist = (mx - sx[a] - ex * t) ** 2 + (my - sy[a] - ey * t) ** 2
        if best_dist is None or dist < best_dist:
            best, best_dist = (a, b), dist
    return best


def loop_under_cursor(context, obj, mouse):
    global _loop_cache_key, _loop_cache
    hit = pick.face_under_cursor(context, obj, mouse)
    if hit is None:
        return None
    face_index, sx, sy, in_front = hit
    mesh = obj.data
    face_verts = [v for v in mesh.polygons[face_index].vertices if in_front[v]]
    if len(face_verts) < 2:
        return None
    edge = _nearest_screen_edge(face_verts, sx, sy, mouse)
    hidden = mesh_flags.vertex_hide(mesh)
    key = (mesh.as_pointer(), len(mesh.vertices), len(mesh.edges), frozenset(edge), hash(hidden.tobytes()))
    if key != _loop_cache_key:
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.ensure_lookup_table()
            start = bm.edges.get((bm.verts[edge[0]], bm.verts[edge[1]]))
            verts = None if start is None else {v.index for e in _walk_loop(start) for v in e.verts if not v.hide}
        finally:
            bm.free()
        _loop_cache_key, _loop_cache = key, verts
    if _loop_cache is None:
        return None
    return _loop_cache, edge


class SUPERSKIN_OT_weight_select_loop(bpy.types.Operator):
    """Select the edge loop under the mouse"""
    bl_idname = "superskin.weight_select_loop"
    bl_label = "Select Edge Loop"
    bl_options = {'UNDO'}

    mode: bpy.props.EnumProperty(
        items=(('SET', "Set", ""), ('ADD', "Extend", ""), ('SUB', "Subtract", ""),
               ('TOGGLE', "Toggle", "Remove the loop if it is already selected, otherwise add it")),
        default='SET',
    )
    location: bpy.props.IntVectorProperty(size=2, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return (poll_session_mesh(context)
                and context.region is not None and context.region_data is not None)

    def invoke(self, context, event):
        self.location = (event.mouse_region_x, event.mouse_region_y)
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        found = loop_under_cursor(context, obj, tuple(self.location))
        if found is None:
            return {'CANCELLED'}
        loop_verts, best = found

        current = set(np.flatnonzero(mesh_flags.vertex_select(mesh)).tolist())
        mode = self.mode
        if mode == 'TOGGLE':
            mode = 'SUB' if best[0] in current and best[1] in current else 'ADD'
        if mode == 'ADD':
            result = current | loop_verts
        elif mode == 'SUB':
            result = current - loop_verts
        else:
            result = loop_verts
        flags = np.zeros(len(mesh.vertices), dtype=np.bool_)
        flags[list(result)] = True
        mesh_flags.write_vertex_select(mesh, flags)
        mesh.update()
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_select_loop)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_select_loop)
