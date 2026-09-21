"""Edge-loop vertex selection for Weight Paint Mode."""

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector


def _walk_loop(start_edge):
    loop = {start_edge}
    for direction_vert in start_edge.verts:
        edge, vert = start_edge, direction_vert
        while True:
            if len(vert.link_edges) != 4:
                break
            faces = set(edge.link_faces)
            nxt = [e for e in vert.link_edges if e is not edge and not (faces & set(e.link_faces))]
            if len(nxt) != 1:
                break
            edge = nxt[0]
            if edge in loop:
                break
            loop.add(edge)
            vert = edge.other_vert(vert)
    return loop


class SUPERSKIN_OT_weight_select_loop(bpy.types.Operator):
    """Select the edge loop under the cursor (Weight Paint vertex selection)."""
    bl_idname = "superskin.weight_select_loop"
    bl_label = "Select Edge Loop"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        items=(('SET', "Set", ""), ('ADD', "Extend", ""), ('SUB', "Subtract", "")),
        default='SET',
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj is not None and obj.type == 'MESH' and context.mode == 'PAINT_WEIGHT'
                and context.region is not None and context.region_data is not None)

    def invoke(self, context, event):
        obj = context.active_object
        region, rv3d = context.region, context.region_data
        coord = (event.mouse_region_x, event.mouse_region_y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        inv = obj.matrix_world.inverted()
        origin_local = inv @ origin
        direction_local = (inv.to_3x3() @ direction).normalized()

        hit, location, _normal, face_index = obj.ray_cast(origin_local, direction_local)
        if not hit or face_index < 0:
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()
        eval_mesh = obj.evaluated_get(depsgraph).data
        mesh = obj.data
        if len(eval_mesh.vertices) != len(mesh.vertices):
            self.report({'WARNING'}, "Loop select needs a modifier stack that keeps the vertex count")
            return {'CANCELLED'}

        coords = [v.co for v in eval_mesh.vertices]
        poly = eval_mesh.polygons[face_index]
        best, best_dist = None, None
        for a, b in poly.edge_keys:
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
            loop_verts = {v.index for e in _walk_loop(start) for v in e.verts}
        finally:
            bm.free()

        current = {v.index for v in mesh.vertices if v.select}
        if self.mode == 'ADD':
            result = current | loop_verts
        elif self.mode == 'SUB':
            result = current - loop_verts
        else:
            result = loop_verts
        mesh.vertices.foreach_set("select", [i in result for i in range(len(mesh.vertices))])
        mesh.update()
        return {'FINISHED'}


def register():
    bpy.utils.register_class(SUPERSKIN_OT_weight_select_loop)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_weight_select_loop)
