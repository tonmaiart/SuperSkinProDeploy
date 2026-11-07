
import bmesh
import bpy
import numpy as np

from .. import mesh_flags
from . import pick, welded
from .common import poll_session_mesh


def _visible_edges(vert):
    return [e for e in vert.link_edges if not e.hide]


def _visible_faces(edge):
    return {f for f in edge.link_faces if not f.hide}


def _opposite_edge(edge, vert, edges):
    if len(edges) < 4 or len(edges) % 2 or any(len(_visible_faces(e)) != 2 for e in edges):
        return None
    for face in _visible_faces(edge):
        current = edge
        for _ in range(len(edges) // 2 - 1):
            current = _fan_step(face, current, vert)
            faces = _visible_faces(current) if current else ()
            if len(faces) != 2:
                break
            face = next(iter(faces - {face}))
        else:
            found = _fan_step(face, current, vert)
            if found is not None:
                return found
    return None


def _fan_step(face, edge, vert):
    if face is None:
        return None
    nxt = [e for e in face.edges if e is not edge and vert in e.verts and not e.hide]
    return nxt[0] if len(nxt) == 1 else None


def _next_edge(edge, vert, kind):
    edges = _visible_edges(vert)
    if kind == 'MANIFOLD':
        return _opposite_edge(edge, vert, edges)
    want = 1 if kind == 'BOUNDARY' else 0
    nxt = [e for e in edges if e is not edge and len(_visible_faces(e)) == want]
    if kind == 'WIRE' and len(edges) != 2:
        return None
    return nxt[0] if len(nxt) == 1 else None


def _walk_loop(start_edge):
    faces = len(_visible_faces(start_edge))
    kind = 'BOUNDARY' if faces == 1 else 'WIRE' if faces == 0 else 'MANIFOLD'
    loop = {start_edge}
    for direction_vert in start_edge.verts:
        edge, vert = start_edge, direction_vert
        while True:
            edge = _next_edge(edge, vert, kind)
            if edge is None or edge in loop:
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
    rep = welded.weld_reps(mesh)
    key = (mesh.as_pointer(), len(mesh.vertices), len(mesh.edges), frozenset(edge), hash(hidden.tobytes()),
           None if rep is None else id(rep))
    if key != _loop_cache_key:
        _loop_cache_key, _loop_cache = key, loop_vertices(mesh, edge, rep)
    if _loop_cache is None:
        return None
    return _loop_cache, edge


def loop_vertices(mesh, edge, rep=None):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        original = bm.verts.layers.int.new("ssp_original_index")
        for v in bm.verts:
            v[original] = v.index
        a, b = bm.verts[edge[0]], bm.verts[edge[1]]
        if rep is not None:
            a, b = bm.verts[rep[edge[0]]], bm.verts[rep[edge[1]]]
            bmesh.ops.weld_verts(bm, targetmap={v: bm.verts[rep[v.index]] for v in bm.verts if rep[v.index] != v.index})
        start = bm.edges.get((a, b))
        if start is None:
            return None
        verts = {v[original] for e in _walk_loop(start) for v in e.verts if not v.hide}
    finally:
        bm.free()
    if rep is None:
        return verts
    shown = welded.expand(rep, np.isin(np.arange(len(rep)), list(verts))) & ~mesh_flags.vertex_hide(mesh)
    return set(np.flatnonzero(shown).tolist())


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
        chosen = np.zeros(len(mesh.vertices), dtype=np.bool_)
        chosen[list(loop_verts)] = True
        welded.write_selection(mesh, flags, chosen)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_select_loop)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_select_loop)
