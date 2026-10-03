
import bmesh
import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .. import mesh_flags
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
        region, rv3d = context.region, context.region_data
        coord = tuple(self.location)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        inv = obj.matrix_world.inverted()
        origin_local = inv @ origin
        direction_local = (inv.to_3x3() @ direction).normalized()

        depsgraph = context.evaluated_depsgraph_get()
        eval_mesh = obj.evaluated_get(depsgraph).data
        mesh = obj.data
        if len(eval_mesh.vertices) != len(mesh.vertices):
            self.report({'WARNING'}, "Loop select needs a modifier stack that keeps the vertex count")
            return {'CANCELLED'}

        coords = [v.co.copy() for v in eval_mesh.vertices]
        hidden = mesh_flags.vertex_hide(mesh).tolist()
        visible_polys = [tuple(p.vertices) for p in eval_mesh.polygons
                         if not any(hidden[i] for i in p.vertices)]
        if not visible_polys:
            return {'CANCELLED'}
        location, _normal, hit_index, _dist = BVHTree.FromPolygons(coords, visible_polys).ray_cast(
            origin_local, direction_local)
        if location is None:
            return {'CANCELLED'}

        face_verts = visible_polys[hit_index]
        best, best_dist = None, None
        for a, b in zip(face_verts, face_verts[1:] + face_verts[:1]):
            pa, pb = coords[a], coords[b]
            seg = pb - pa
            t = 0.0 if seg.length_squared == 0 else max(0.0, min(1.0, (location - pa).dot(seg) / seg.length_squared))
            dist = (location - (pa + seg * t)).length
            if best_dist is None or dist < best_dist:
                best, best_dist = (a, b), dist

        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.edges.ensure_lookup_table()
            bm.verts.ensure_lookup_table()
            start = bm.edges.get((bm.verts[best[0]], bm.verts[best[1]]))
            if start is None:
                return {'CANCELLED'}
            loop_verts = {v.index for e in _walk_loop(start) for v in e.verts if not v.hide}
        finally:
            bm.free()

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
