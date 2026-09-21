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
        state = getattr(context.scene, "superskin_weight_transfer_state", None)
        if state is None:
            return False
        source_entry = next((e for e in state.entries if e.is_source), None)
        source = source_entry.object if source_entry else None
        return (CoreFacade.is_system_activated() and
                source is not None and source.type == 'MESH' and
                any(e.object and e.object.type == 'MESH' and not e.is_source for e in state.entries))

    def execute(self, context):
        prefs = _get_prefs()
        layer_output = 'SEPARATE'
        insert_method = 'APPEND' if (prefs and prefs.keep_old_layer_data) else 'REPLACE'
        transfer_method = prefs.transfer_method if prefs else 'CLOSEST_DISTANCE'

        state = context.scene.superskin_weight_transfer_state
        source_entry = next((e for e in state.entries if e.is_source), None)
        source_obj = source_entry.object if source_entry else None
        use_selected_source = source_entry.use_selected_verts if source_entry else False

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): layer_output={layer_output} "
            f"insert_method={insert_method} transfer_method={transfer_method}",
        )

        if not source_obj or source_obj.type != 'MESH':
            self.report({'ERROR'}, "No Source Mesh selected in the Transfer popup")
            return {'CANCELLED'}

        target_entries = [e for e in state.entries if e.object and e.object.type == 'MESH' and not e.is_source]
        if not target_entries:
            self.report({'ERROR'}, "No Target Mesh in the Transfer popup")
            return {'CANCELLED'}

        target_objs = [t.object for t in target_entries]
        source_vg_names = [vg.name for vg in source_obj.vertex_groups]

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): source={source_obj.name!r} "
            f"source_vg_names={source_vg_names} targets={[t.name for t in target_objs]}",
        )

        if not source_vg_names:
            self.report({'ERROR'}, "Source mesh has no Vertex Groups")
            return {'CANCELLED'}

        source_armature = next((m.object for m in source_obj.modifiers if m.type == 'ARMATURE' and m.object), None)
        if not source_armature:
            self.report({'ERROR'}, f"Source '{source_obj.name}' has no Armature deformer!")
            return {'CANCELLED'}

        allowed_source_verts = None
        if use_selected_source:
            allowed_source_verts = {v.index for v in source_obj.data.vertices if v.select}
            if not allowed_source_verts:
                self.report(
                    {'ERROR'},
                    f"Source '{source_obj.name}' has no selected vertices "
                    f"(Use Selected Vertices Only is enabled for Source)",
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
                    f"Transfer Method 'Vertex ID' requires the same vertex count between Source and Target -- mismatched: {', '.join(mismatched)}",
                )
                return {'CANCELLED'}

        composite_weights = self._compute_weight_map_vertex_id(source_obj, source_vg_names)
        composite_mask = {v_idx: 1.0 for v_idx in composite_weights}

        source_surface = None
        weight_surface = None
        if transfer_method == 'CLOSEST_DISTANCE':
            positions, triangles = self._source_geometry(context, source_obj)
            source_surface = transfer_core.build_surface(positions, triangles)
            if allowed_source_verts is not None:
                weight_surface = transfer_core.build_restricted_surface(
                    positions, triangles, allowed_source_verts,
                )
                if weight_surface is None:
                    self.report(
                        {'ERROR'},
                        f"The selected vertices on Source '{source_obj.name}' don't form "
                        f"a single face -- select all 3 vertices of at least one face",
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

        for entry in target_entries:
            target = entry.object
            allowed_target_verts = None
            if entry.use_selected_verts:
                allowed_target_verts = {v.index for v in target.data.vertices if v.select}
                if not allowed_target_verts:
                    self.report(
                        {'WARNING'},
                        f"Target '{target.name}' has no selected vertices, skipping "
                        f"(Use Selected Vertices Only is enabled for this Target)",
                    )
                    continue

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

    def _world_positions(self, context, obj):
        """World-space vertex positions, aligned to obj.data.vertices index order."""
        matrix_world = obj.matrix_world
        return [matrix_world @ v.co for v in obj.data.vertices]

    def _source_geometry(self, context, source_obj):
        """World-space vertex positions + triangulated face index tuples."""
        positions = self._world_positions(context, source_obj)

        mesh = source_obj.data
        mesh.calc_loop_triangles()
        triangles = [tuple(lt.vertices) for lt in mesh.loop_triangles]
        return positions, triangles

    def _get_source_layers(self, context, source_obj, source_vg_names):
        """Read the source's own SuperSkinPro Layers as (name, weight_dict, mask_dict,
        mask_default) tuples."""
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
                facade.switch_to_layer(idx)
                layers.append((name, facade.read_active_layer(), facade.get_active_mask_dict(), mask_default))

            if prev_layer_idx is not None and prev_layer_idx >= 0:
                facade.switch_to_layer(prev_layer_idx)
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
