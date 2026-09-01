"""Modal operator for the Sketch Weight Guide gesture: drag a stroke across
the mesh in the current viewport, release to solve and apply multi-bone
inverse-LBS weights for every affected vertex near the stroke.

Invoked directly from the N-panel "Draw Weight Guide" button
(``sketch_weight_feature.py``'s ``draw_section()``) -- no keymap, unlike
``bone_picker``/``circle_tool_adjust``'s hotkey-triggered modals, since this
is a deliberate, occasional action rather than a hold-to-use gesture.

See ``docs/domains/sketch_weight.md`` for the full math + scope writeup.
"""

import bmesh
import bpy
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import logic
from . import draw as guide_draw

_MIN_STROKE_SAMPLES = 2
_MIN_SAMPLE_SPACING_PX = 3.0  # skip near-duplicate samples on a slow drag


class MESH_OT_ssp_sketch_guide_draw(bpy.types.Operator):
    """Draw a guide stroke on the mesh surface; on release, solves
    multi-bone weights so the affected vertices follow the drawn
    silhouette as closely as the existing rig allows."""
    bl_idname = "mesh.ssp_sketch_guide_draw"
    bl_label = "Draw Weight Guide Stroke"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'EDIT'

    def invoke(self, context, event):
        if context.space_data is None or context.space_data.type != 'VIEW_3D':
            return {'CANCELLED'}

        # This operator is invoked from the N-panel's "Draw Weight Guide"
        # button, which lives in the sidebar's 'UI' region -- NOT the
        # viewport's 'WINDOW' region. context.region/context.region_data at
        # the moment of that click are the N-panel's own (region_data is
        # None for a 'UI' region), so every raycast in _try_sample() would
        # silently miss if we captured them directly here. Resolve the
        # actual viewport WINDOW region + its RegionView3D explicitly
        # instead -- region_3d lives on the space, not the region, so it's
        # valid regardless of which region within this VIEW_3D area was
        # actually clicked.
        region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        rv3d = context.space_data.region_3d
        if region is None or rv3d is None:
            self.report({'WARNING'}, "Could not find a 3D Viewport to draw into.")
            return {'CANCELLED'}

        obj = context.active_object
        depsgraph = context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        mesh_eval = obj_eval.to_mesh()
        try:
            positions = [obj.matrix_world @ v.co for v in mesh_eval.vertices]
            mesh_eval.calc_loop_triangles()
            triangles = [tuple(lt.vertices) for lt in mesh_eval.loop_triangles]
        finally:
            obj_eval.to_mesh_clear()

        if not positions or not triangles:
            self.report({'WARNING'}, "Mesh has no surface to sketch on.")
            return {'CANCELLED'}

        self._bvh = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
        self._region = region
        self._rv3d = rv3d
        self._stroke = []
        self._last_depth_point = None  # last actual surface hit this stroke, if any

        # Fallback depth anchor for samples that never touch the mesh at all
        # (a stroke drawn entirely in open air, before the surface has
        # bulged out to meet it yet). Preferred anchor: the evaluated
        # world-space CENTROID OF THE CURRENT SELECTION, when one exists --
        # exactly the area the user is about to sketch on (logic.py's own
        # candidate-scoping auto-detects a selection the same way). Without
        # a selection, find whichever mesh vertex is nearest to the INITIAL mouse
        # position on screen (a genuine 2D screen-space comparison, not a
        # 3D distance guess) rather than the whole mesh's bounding-box
        # center -- on a large/multi-part character, starting a stroke at
        # an extremity (a hand, a finger, an ear) with no selection made
        # the bbox-center fallback jump straight to the torso/navel,
        # producing a wildly wrong depth for that whole segment. One-time
        # O(V) scan, only at invoke() -- see "Depth Ambiguity" in
        # docs/domains/sketch_weight.md.
        bm = bmesh.from_edit_mesh(obj.data)
        selected_indices = [v.index for v in bm.verts if v.select and v.index < len(positions)]
        if selected_indices:
            centroid = sum((positions[i] for i in selected_indices), Vector((0.0, 0.0, 0.0)))
            centroid /= len(selected_indices)
            self._fallback_depth_point = centroid
        else:
            mouse_screen = (event.mouse_x - region.x, event.mouse_y - region.y)
            best_dist_sq = None
            best_pos = None
            for pos in positions:
                screen_co = view3d_utils.location_3d_to_region_2d(region, rv3d, pos)
                if screen_co is None:
                    continue
                dx, dy = screen_co[0] - mouse_screen[0], screen_co[1] - mouse_screen[1]
                dist_sq = dx * dx + dy * dy
                if best_dist_sq is None or dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_pos = pos
            if best_pos is None:
                local_center = sum((Vector(c) for c in obj.bound_box), Vector((0.0, 0.0, 0.0))) / 8.0
                best_pos = obj.matrix_world @ local_center
            self._fallback_depth_point = best_pos

        guide_draw.show()
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _try_sample(self, event):
        # event.mouse_x/mouse_y are window-absolute; convert to self._region
        # -relative coordinates ourselves rather than trusting
        # event.mouse_region_x/y, which are relative to whatever region
        # Blender currently considers "active" for this event -- not
        # necessarily the viewport WINDOW region we resolved in invoke(),
        # since this modal wasn't entered by clicking inside that region.
        mouse_co = (event.mouse_x - self._region.x, event.mouse_y - self._region.y)
        if self._stroke:
            last_x, last_y = self._stroke[-1]["screen"]
            dx, dy = mouse_co[0] - last_x, mouse_co[1] - last_y
            if (dx * dx + dy * dy) < (_MIN_SAMPLE_SPACING_PX ** 2):
                return
        origin = view3d_utils.region_2d_to_origin_3d(self._region, self._rv3d, mouse_co)
        direction = view3d_utils.region_2d_to_vector_3d(self._region, self._rv3d, mouse_co)
        hit, _normal, _index, _dist = self._bvh.ray_cast(origin, direction)
        if hit is not None:
            self._last_depth_point = hit.copy()
            world_pos = hit
        else:
            # Off the mesh entirely -- e.g. sketching a bulge the current
            # surface doesn't reach yet. Re-estimate depth along THIS ray
            # via the BVH's nearest-surface-point query (find_nearest() is
            # O(log n) -- cheap enough to run on every off-mesh MOUSEMOVE,
            # unlike a linear per-vertex screen-space scan) rather than
            # staying pinned to one static anchor for the whole stroke:
            # probe from the current ray at the best depth estimate found
            # so far, so the anchor smoothly tracks the mouse across
            # different body parts (a hand, then an ear) even while the
            # stroke never touches the surface.
            #
            # The refined depth is still only used to pick a PLANE via
            # region_2d_to_location_3d(), not returned directly as
            # world_pos -- this tool exists specifically to let a stroke
            # drawn BEYOND the current surface pull it outward (a bulge
            # from open air); snapping straight to the nearest real
            # surface point would silently undo that.
            anchor = self._last_depth_point or self._fallback_depth_point
            probe = origin + direction * (anchor - origin).length
            nearest_loc, _n, _i, _d = self._bvh.find_nearest(probe)
            depth_point = anchor
            if nearest_loc is not None:
                depth_point = nearest_loc
                self._last_depth_point = nearest_loc.copy()
            world_pos = view3d_utils.region_2d_to_location_3d(
                self._region, self._rv3d, mouse_co, depth_point
            )
        self._stroke.append({"screen": mouse_co, "world": world_pos})
        guide_draw.update([s["screen"] for s in self._stroke])

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self._try_sample(event)
            return {'RUNNING_MODAL'}

        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            guide_draw.hide()
            if len(self._stroke) < _MIN_STROKE_SAMPLES:
                self.report({'WARNING'}, "Guide stroke too short.")
                return {'CANCELLED'}
            return self._solve(context)

        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            guide_draw.hide()
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _solve(self, context):
        from ...core.facade import CoreFacade

        prefs = context.window_manager.superskin_sketch_weight_prefs
        context.scene.superskin_internal_transaction = True
        try:
            facade = CoreFacade(context)
            touched = logic.solve_stroke(
                facade, context, self._stroke, self._region, self._rv3d, self._bvh,
                radius_px=prefs.guide_radius,
            )
            if touched == 0:
                self.report({'WARNING'}, "No affected vertices near the guide stroke.")
                return {'CANCELLED'}
            facade.show_toast(f"Sketch Weight: {touched} vertex(es) updated", 1.2)
            return {'FINISHED'}
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            context.scene.superskin_internal_transaction = False


_classes = (MESH_OT_ssp_sketch_guide_draw,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
