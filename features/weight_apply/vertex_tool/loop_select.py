
import math

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


def _tangent(vec, normal):
    return vec - normal * vec.dot(normal)


def _turn(incoming, vert, edge):
    out = _tangent(edge.other_vert(vert).co - vert.co, vert.normal)
    if out.length_squared < 1e-24 or incoming.length_squared < 1e-24:
        return math.pi
    return incoming.angle(out)


def _straightest_edge(edge, vert, edges, avoid):
    incoming = _tangent(vert.co - edge.other_vert(vert).co, vert.normal)
    best = _opposite_edge(edge, vert, edges)
    if best is not None and _turn(incoming, vert, best) <= _MAX_TURN:
        return best
    best, best_turn = None, _MAX_TURN
    for e in edges:
        if e is edge or len(_visible_faces(e)) != 2 or e.other_vert(vert) in avoid:
            continue
        turn = _turn(incoming, vert, e)
        if turn <= best_turn:
            best, best_turn = e, turn
    return best


def _walk_loop(start_edge, source_layer=None):
    faces = len(_visible_faces(start_edge))
    kind = 'BOUNDARY' if faces == 1 else 'WIRE' if faces == 0 else 'MANIFOLD'
    loop = {start_edge}
    used = set(start_edge.verts)
    far = start_edge.verts[1]
    mid = (start_edge.verts[0].co + start_edge.verts[1].co) / 2.0
    axis = start_edge.verts[1].co - start_edge.verts[0].co
    for direction_vert in start_edge.verts:
        edge, vert = start_edge, direction_vert
        walked = 0.0
        last_side = (vert.co - mid).dot(axis) > 0.0
        while True:
            steered = (kind == 'MANIFOLD' and source_layer is not None
                       and any(not f[source_layer] for f in vert.link_faces))
            if steered:
                edge = _straightest_edge(edge, vert, _visible_edges(vert), used - {far})
            else:
                edge = _next_edge(edge, vert, kind)
            if edge is None or edge in loop:
                break
            nxt = edge.other_vert(vert)
            if steered and nxt in used and nxt is not far:
                break
            loop.add(edge)
            vert = nxt
            if steered and nxt in used:
                break
            used.add(nxt)
            walked += edge.calc_length()
            side = (nxt.co - mid).dot(axis) > 0.0
            if (steered and side != last_side and len(loop) >= 4
                    and (nxt.co - mid).length < walked / 6.0):
                break
            last_side = side
        far = vert
    return loop


_loop_cache_key = None
_loop_cache = None

_JOIN_FACE_ANGLE = math.radians(40.0)
_JOIN_SHAPE_ANGLE = math.radians(40.0)
_MAX_TURN = math.radians(35.0)


def _edges_by_screen_distance(face_verts, sx, sy, mouse):
    mx, my = mouse
    ranked = []
    for a, b in zip(face_verts, face_verts[1:] + face_verts[:1]):
        ex, ey = sx[b] - sx[a], sy[b] - sy[a]
        length2 = ex * ex + ey * ey
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((mx - sx[a]) * ex + (my - sy[a]) * ey) / length2))
        ranked.append(((mx - sx[a] - ex * t) ** 2 + (my - sy[a] - ey * t) ** 2, (a, b)))
    ranked.sort(key=lambda item: item[0])
    return tuple(edge for _, edge in ranked)


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
    candidates = _edges_by_screen_distance(face_verts, sx, sy, mouse)
    hidden = mesh_flags.vertex_hide(mesh)
    rep = welded.weld_reps(mesh)
    key = (mesh.as_pointer(), len(mesh.vertices), len(mesh.edges), tuple(frozenset(e) for e in candidates),
           hash(hidden.tobytes()), None if rep is None else id(rep))
    if key != _loop_cache_key:
        _loop_cache_key, _loop_cache = key, _resolve_loop(mesh, candidates, rep)
    return _loop_cache


def loop_vertices(mesh, edge, rep=None):
    found = _resolve_loop(mesh, (edge,), rep)
    return None if found is None else found[0]


def _resolve_loop(mesh, candidates, rep):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        original = bm.verts.layers.int.new("ssp_original_index")
        for v in bm.verts:
            v[original] = v.index
        if rep is not None:
            bm.verts.ensure_lookup_table()
            bmesh.ops.weld_verts(bm, targetmap={v: bm.verts[rep[v.index]] for v in bm.verts if rep[v.index] != v.index})
        source_layer = bm.faces.layers.int.new("ssp_not_triangle")
        for f in bm.faces:
            f[source_layer] = len(f.verts) != 3
        tris = [f for f in bm.faces if len(f.verts) == 3 and not f.hide]
        if len(tris) > 1:
            bmesh.ops.join_triangles(bm, faces=tris, angle_face_threshold=_JOIN_FACE_ANGLE,
                                     angle_shape_threshold=_JOIN_SHAPE_ANGLE)
        bm.normal_update()
        by_original = {v[original]: v for v in bm.verts}
        for edge in candidates:
            ends = edge if rep is None else (rep[edge[0]], rep[edge[1]])
            a, b = by_original.get(ends[0]), by_original.get(ends[1])
            start = bm.edges.get((a, b)) if a is not None and b is not None and a is not b else None
            if start is not None:
                break
        else:
            return None
        verts = {v[original] for e in _walk_loop(start, source_layer) for v in e.verts if not v.hide}
    finally:
        bm.free()
    if rep is not None:
        shown = welded.expand(rep, np.isin(np.arange(len(rep)), list(verts))) & ~mesh_flags.vertex_hide(mesh)
        verts = set(np.flatnonzero(shown).tolist())
    return verts, edge


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
