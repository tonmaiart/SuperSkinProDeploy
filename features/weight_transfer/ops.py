"""Weight Transfer operators — moved from operators/ops_tools.py."""

import bpy

from ...core.facade import CoreFacade
from ...interface.utils.utils import _has_layer_system
from . import transfer_core


def _get_prefs():
    wm = bpy.context.window_manager
    return getattr(wm, "superskin_weight_transfer_prefs", None)


class OBJECT_OT_mw_copy_skin_weight_maya(bpy.types.Operator):
    """Copy skin weights with perfect axial Linear Interpolation based on group centers"""
    bl_idname = "object.mw_copy_skin_weight_maya"
    bl_label = "Copy Skin Weight (Maya Style)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (CoreFacade.is_system_activated() and
                len(context.selected_objects) >= 2 and
                all(o.type == 'MESH' for o in context.selected_objects))

    def execute(self, context):
        prefs = _get_prefs()
        layer_output = prefs.layer_output if prefs else 'SEPARATE'
        insert_method = prefs.insert_method if prefs else 'REPLACE'
        transfer_method = prefs.transfer_method if prefs else 'CLOSEST_DISTANCE'
        use_selected_source = prefs.use_selected_source_verts if prefs else False
        use_selected_target = prefs.use_selected_target_verts if prefs else False
        use_current_pose = (prefs.pose_mode if prefs else 'REST_POSE') == 'CURRENT_POSE'

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): layer_output={layer_output} "
            f"insert_method={insert_method} transfer_method={transfer_method} "
            f"use_selected_source={use_selected_source} use_selected_target={use_selected_target} "
            f"use_current_pose={use_current_pose}",
        )

        selected_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']

        non_active_objs = [obj for obj in selected_objs if obj != context.active_object]
        source_obj = next((obj for obj in non_active_objs if any(m.type == 'ARMATURE' and m.object for m in obj.modifiers)), None)
        if not source_obj:
            source_obj = next((obj for obj in non_active_objs if len(obj.vertex_groups) >= 2), None)
        if not source_obj and non_active_objs:
            source_obj = non_active_objs[0]

        if not source_obj:
            self.report({'ERROR'}, "ไม่สามารถระบุวัตถุ Source (Proxy Mesh) ได้!")
            return {'CANCELLED'}

        target_objs = [obj for obj in selected_objs if obj != source_obj]
        source_vg_names = [vg.name for vg in source_obj.vertex_groups]

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): source={source_obj.name!r} "
            f"source_vg_names={source_vg_names} targets={[t.name for t in target_objs]}",
        )

        if len(source_vg_names) < 2:
            self.report({'ERROR'}, "Proxy Mesh ต้องมี Vertex Group อย่างน้อย 2 กลุ่มขึ้นไป")
            return {'CANCELLED'}

        source_armature = next((m.object for m in source_obj.modifiers if m.type == 'ARMATURE' and m.object), None)
        if not source_armature:
            self.report({'ERROR'}, f"Source '{source_obj.name}' ไม่มีกระดูกผูกอยู่!")
            return {'CANCELLED'}

        allowed_source_verts = None
        if use_selected_source:
            allowed_source_verts = {v.index for v in source_obj.data.vertices if v.select}
            if not allowed_source_verts:
                self.report(
                    {'ERROR'},
                    f"Source '{source_obj.name}' ไม่มี Vertex ที่เลือกไว้ "
                    f"(เปิด Use Selected Source Vertices อยู่)",
                )
                return {'CANCELLED'}

        if transfer_method == 'VERTEX_ID':
            mismatched = [t.name for t in target_objs if len(t.data.vertices) != len(source_obj.data.vertices)]
            if mismatched:
                CoreFacade.debug_log(
                    "feature_domains",
                    f"weight_transfer.execute(): VERTEX_ID vertex-count mismatch on {mismatched}",
                )
                self.report(
                    {'ERROR'},
                    f"Transfer Method 'Vertex ID' ต้องการจำนวน Vertex เท่ากันระหว่าง Source และ Target ไม่ตรงกันที่: {', '.join(mismatched)}",
                )
                return {'CANCELLED'}

        composite_weights = self._compute_weight_map_vertex_id(source_obj, source_vg_names)
        composite_mask = {v_idx: 1.0 for v_idx in composite_weights}

        # The BVH + triangle data only depend on the source's static geometry,
        # never on which Layer's weights are being blended, so it is built
        # once and reused across every target and every source Layer below.
        source_surface = None
        weight_surface = None
        if transfer_method == 'CLOSEST_DISTANCE':
            positions, triangles = self._source_geometry(context, source_obj, use_current_pose)
            source_surface = transfer_core.build_surface(positions, triangles)
            if allowed_source_verts is not None:
                weight_surface = transfer_core.build_restricted_surface(
                    positions, triangles, allowed_source_verts,
                )
                if weight_surface is None:
                    self.report(
                        {'ERROR'},
                        f"Vertex ที่เลือกไว้บน Source '{source_obj.name}' ไม่ประกอบกันเป็นหน้า "
                        f"(Face) เดียวเลย ต้องเลือก Vertex ให้ครบทั้ง 3 จุดของอย่างน้อย 1 หน้า",
                    )
                    return {'CANCELLED'}

        source_layers = None
        if layer_output == 'SEPARATE':
            source_layers = self._get_source_layers(context, source_obj, source_vg_names)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer.execute(): source_layers="
                f"{[(name, len(w), md) for name, w, _m, md in source_layers]}",
            )

        for target in target_objs:
            allowed_target_verts = None
            if use_selected_target:
                allowed_target_verts = {v.index for v in target.data.vertices if v.select}
                if not allowed_target_verts:
                    self.report(
                        {'WARNING'},
                        f"Target '{target.name}' ไม่มี Vertex ที่เลือกไว้ ข้ามไป "
                        f"(เปิด Use Selected Target Vertices อยู่)",
                    )
                    continue

            # Snapshot BEFORE any mutation below (REPLACE's vertex_groups.clear(),
            # ensure_armature_modifier() re-pointing the Armature modifier at the
            # source's own armature) — those steps can reset whatever deform the
            # target's own current pose was producing, so "Current Pose" must
            # read the target's real pre-transfer shape first or it would end
            # up reading rest pose anyway.
            target_positions = None
            if transfer_method == 'CLOSEST_DISTANCE' and use_current_pose:
                target_positions = self._world_positions(context, target, use_current_pose)

            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer.execute(): writing target={target.name!r} "
                f"vert_count={len(target.data.vertices)}",
            )

            if insert_method == 'REPLACE':
                target.vertex_groups.clear()

            transfer_core.ensure_armature_modifier(target, source_armature)
            transfer_core.ensure_native_vertex_groups(target, source_vg_names)

            context.view_layer.objects.active = target

            layer_payloads = transfer_core.compute_layer_payloads(
                layer_output, transfer_method, target, source_surface,
                composite_weights, composite_mask, source_layers or [],
                merge_name=f"Transfer from {source_obj.name}",
                allowed_source_verts=allowed_source_verts,
                allowed_target_verts=allowed_target_verts,
                weight_surface=weight_surface,
                target_positions=target_positions,
            )

            transfer_core.write_layers_to_target(context, target, insert_method, layer_payloads)

            target.data.update()

        context.view_layer.objects.active = source_obj

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): done, {len(target_objs)} target(s) written",
        )
        self.report({'INFO'}, "Successfully applied accurate center-aligned linear weights!")
        return {'FINISHED'}

    def _compute_weight_map_vertex_id(self, source_obj, source_vg_names):
        """Copy every source vertex group's weight to the target vertex of the same index."""
        src_index_to_name = {vg.index: vg.name for vg in source_obj.vertex_groups}

        weight_map = {}
        for i, src_v in enumerate(source_obj.data.vertices):
            bone_weights = {
                src_index_to_name[g.group]: g.weight
                for g in src_v.groups
                if g.group in src_index_to_name and g.weight > 0.0
            }
            if bone_weights:
                weight_map[i] = bone_weights

        return weight_map

    def _world_positions(self, context, obj, use_current_pose):
        """World-space vertex positions, aligned to obj.data.vertices index order.

        Rest Pose (default, and always for JSON export/import): read straight
        from `obj.data.vertices` — the mesh's stored/bind shape, unaffected by
        whatever pose the bound Armature (or any other modifier) currently has
        it deformed into.

        Current Pose: read from the depsgraph-evaluated object instead, i.e.
        the mesh's ACTUAL currently-deformed shape. Topology (vertex count and
        order) is assumed unchanged by the modifier stack — true for an
        Armature modifier, which only moves vertices, never adds/removes them.
        """
        matrix_world = obj.matrix_world
        if not use_current_pose:
            return [matrix_world @ v.co for v in obj.data.vertices]

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        return [matrix_world @ v.co for v in eval_obj.data.vertices]

    def _source_geometry(self, context, source_obj, use_current_pose):
        """World-space vertex positions + triangulated face index tuples.

        Triangulation always comes from the source's own (rest-pose) mesh
        data-block — an Armature modifier never changes topology, only
        vertex positions, so it stays valid whichever *use_current_pose*
        supplies the positions themselves.
        """
        positions = self._world_positions(context, source_obj, use_current_pose)

        mesh = source_obj.data
        mesh.calc_loop_triangles()
        triangles = [tuple(lt.vertices) for lt in mesh.loop_triangles]
        return positions, triangles

    def _get_source_layers(self, context, source_obj, source_vg_names):
        """Read the source's own SuperSkinPro Layers as (name, weight_dict, mask_dict, mask_default) tuples.

        `mask_dict` only holds vertices explicitly painted *differently* from
        the layer's own `mask_default` (e.g. a "Fill Mask" action just sets
        `mask_default` and never writes a dense per-vertex dict at all) — the
        real, effective mask value for any vertex missing from `mask_dict` is
        `mask_default`, not 0.0. See `core/layer_storage/topology_heal.py`
        (`layer.get("mask_default", 1.0)`) for the authoritative default.

        Falls back to a single synthetic layer — named after the source
        object, built from its flattened native Vertex Groups, mask = 1.0
        wherever weighted, mask_default = 0.0 elsewhere — when the source has
        no Layer system of its own. Matches the confirmed behavior that
        MERGE and SEPARATE are identical when the source has exactly one
        layer (or none at all).
        """
        if not _has_layer_system(source_obj):
            weight_dict = self._compute_weight_map_vertex_id(source_obj, source_vg_names)
            mask_dict = {v_idx: 1.0 for v_idx in weight_dict}
            return [(source_obj.name, weight_dict, mask_dict, 0.0)]

        prev_active = context.view_layer.objects.active
        context.view_layer.objects.active = source_obj
        try:
            facade = CoreFacade(context)
            meta = facade.get_meta_list()
            prev_layer_idx = facade.get_active_layer_index()

            layers = []
            for m in meta:
                idx = m.get("index", -1)
                if idx < 0:
                    continue
                name = m.get("name") or f"Layer {idx}"
                mask_default = float(m.get("mask_default", 1.0))
                facade.switch_to_layer(idx, push_undo=False)
                layers.append((name, facade.read_active_layer(), facade.get_active_mask_dict(), mask_default))

            if prev_layer_idx is not None and prev_layer_idx >= 0:
                facade.switch_to_layer(prev_layer_idx, push_undo=False)
        finally:
            context.view_layer.objects.active = prev_active

        if layers:
            return layers

        weight_dict = self._compute_weight_map_vertex_id(source_obj, source_vg_names)
        mask_dict = {v_idx: 1.0 for v_idx in weight_dict}
        return [(source_obj.name, weight_dict, mask_dict, 0.0)]


def register():
    bpy.utils.register_class(OBJECT_OT_mw_copy_skin_weight_maya)


def unregister():
    bpy.utils.unregister_class(OBJECT_OT_mw_copy_skin_weight_maya)
