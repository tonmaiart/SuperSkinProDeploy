
import bpy
import numpy as np

from ...core.facade import CoreFacade
from ...interface.utils.utils import _has_layer_system
from ..weight_apply.public_api import read_vertex_select
from . import transfer_core


def _get_prefs():
    wm = bpy.context.window_manager
    return getattr(wm, "superskin_weight_transfer_prefs", None)


class OBJECT_OT_mw_copy_skin_weight_transfer(bpy.types.Operator):
    """Transfer weights from the source meshes to the other meshes in the list"""
    bl_idname = "object.mw_copy_skin_weight_transfer"
    bl_label = "Copy Skin Weight"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        state = getattr(context.scene, "superskin_weight_transfer_state", None)
        if state is None:
            return False
        return (CoreFacade.is_system_activated() and
                any(e.object and e.object.type == 'MESH' and e.is_source for e in state.entries) and
                any(e.object and e.object.type == 'MESH' and not e.is_source for e in state.entries))

    def execute(self, context):
        prefs = _get_prefs()
        layer_output = 'SEPARATE'
        insert_method = 'APPEND' if (prefs and prefs.keep_old_layer_data) else 'REPLACE'
        transfer_method = prefs.transfer_method if prefs else 'CLOSEST_DISTANCE'

        state = context.scene.superskin_weight_transfer_state
        source_entries = [e for e in state.entries if e.object and e.object.type == 'MESH' and e.is_source]

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): layer_output={layer_output} "
            f"insert_method={insert_method} transfer_method={transfer_method}",
        )

        if not source_entries:
            self.report({'ERROR'}, "No Source Mesh selected in the Transfer popup")
            return {'CANCELLED'}

        target_entries = [e for e in state.entries if e.object and e.object.type == 'MESH' and not e.is_source]
        if not target_entries:
            self.report({'ERROR'}, "No Target Mesh in the Transfer popup")
            return {'CANCELLED'}

        source_objs = [e.object for e in source_entries]
        source_label = " + ".join(obj.name for obj in source_objs)
        target_objs = [t.object for t in target_entries]
        source_vg_names = list(dict.fromkeys(
            vg.name for obj in source_objs for vg in obj.vertex_groups
        ))

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): sources={[o.name for o in source_objs]} "
            f"source_vg_names={source_vg_names} targets={[t.name for t in target_objs]}",
        )

        if not source_vg_names:
            self.report({'ERROR'}, "Source mesh has no Vertex Groups")
            return {'CANCELLED'}

        source_armatures = list(dict.fromkeys(
            m.object for obj in source_objs for m in obj.modifiers if m.type == 'ARMATURE' and m.object
        ))
        if not source_armatures:
            self.report({'ERROR'}, f"Source '{source_label}' has no Armature deformer!")
            return {'CANCELLED'}
        source_armature = source_armatures[0]
        if len(source_armatures) > 1:
            self.report(
                {'WARNING'},
                f"Sources use different Armatures -- targets will be bound to '{source_armature.name}'",
            )

        offsets = []
        total_source_verts = 0
        for obj in source_objs:
            offsets.append(total_source_verts)
            total_source_verts += len(obj.data.vertices)

        allowed_source_verts = None
        if any(e.use_selected_verts for e in source_entries):
            allowed_source_verts = set()
            for entry, offset in zip(source_entries, offsets):
                verts = entry.object.data.vertices
                if not entry.use_selected_verts:
                    allowed_source_verts.update(range(offset, offset + len(verts)))
                    continue
                selected = (np.flatnonzero(read_vertex_select(entry.object.data)) + offset).tolist()
                if not selected:
                    self.report(
                        {'ERROR'},
                        f"Source '{entry.object.name}' has no selected vertices "
                        f"(Use Selected Vertices Only is enabled for this Source)",
                    )
                    return {'CANCELLED'}
                allowed_source_verts.update(selected)

        if transfer_method == 'VERTEX_ID':
            mismatched = [t.name for t in target_objs if len(t.data.vertices) != total_source_verts]
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

        composite_weights = {}
        for obj, offset in zip(source_objs, offsets):
            for v_idx, bone_weights in self._compute_weight_map_vertex_id(obj, source_vg_names).items():
                composite_weights[v_idx + offset] = bone_weights
        composite_mask = {v_idx: 1.0 for v_idx in composite_weights}

        source_surface = None
        weight_surface = None
        if transfer_method == 'CLOSEST_DISTANCE':
            positions, triangles = [], []
            for obj, offset in zip(source_objs, offsets):
                obj_positions, obj_triangles = self._source_geometry(context, obj)
                positions.extend(obj_positions)
                triangles.extend(tuple(offset + i for i in tri) for tri in obj_triangles)
            source_surface = transfer_core.build_surface(positions, triangles)
            if allowed_source_verts is not None:
                weight_surface = transfer_core.build_restricted_surface(
                    positions, triangles, allowed_source_verts,
                )
                if weight_surface is None:
                    self.report(
                        {'ERROR'},
                        f"The selected vertices on Source '{source_label}' don't form "
                        f"a single face -- select all 3 vertices of at least one face",
                    )
                    return {'CANCELLED'}

        source_layers = None
        if layer_output == 'SEPARATE':
            source_layers = self._get_combined_source_layers(context, source_objs, offsets, source_vg_names)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer.execute(): source_layers="
                f"{[(name, len(w), md) for name, w, _m, md in source_layers]}",
            )

        for entry in target_entries:
            target = entry.object
            allowed_target_verts = None
            if entry.use_selected_verts:
                allowed_target_verts = set(np.flatnonzero(read_vertex_select(target.data)).tolist())
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
                merge_name=f"Transfer from {source_label}",
                allowed_source_verts=allowed_source_verts,
                allowed_target_verts=allowed_target_verts,
                weight_surface=weight_surface,
            )

            transfer_core.write_layers_to_target(context, target, insert_method, layer_payloads)

            target.data.update()

        context.view_layer.objects.active = source_objs[0]

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): done, {len(target_objs)} target(s) written",
        )
        self.report({'INFO'}, "Successfully applied accurate center-aligned linear weights!")
        return {'FINISHED'}

    def _compute_weight_map_vertex_id(self, source_obj, source_vg_names):
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
        matrix_world = obj.matrix_world
        return [matrix_world @ v.co for v in obj.data.vertices]

    def _source_geometry(self, context, source_obj):
        positions = self._world_positions(context, source_obj)

        mesh = source_obj.data
        mesh.calc_loop_triangles()
        triangles = [tuple(lt.vertices) for lt in mesh.loop_triangles]
        return positions, triangles

    def _get_combined_source_layers(self, context, source_objs, offsets, source_vg_names):
        if len(source_objs) == 1:
            return self._get_source_layers(context, source_objs[0], source_vg_names)

        merged = {}
        for obj, offset in zip(source_objs, offsets):
            layers = self._get_source_layers(context, obj, source_vg_names, fallback_name="Base")
            for name, weights, mask, mask_default in layers:
                merged_weights, merged_mask = merged.setdefault(name, ({}, {}))
                for v_idx, bone_weights in weights.items():
                    merged_weights[v_idx + offset] = bone_weights
                for v_idx in range(len(obj.data.vertices)):
                    merged_mask[v_idx + offset] = mask.get(v_idx, mask_default)

        return [(name, weights, mask, 0.0) for name, (weights, mask) in merged.items()]

    def _get_source_layers(self, context, source_obj, source_vg_names, fallback_name=None):
        fallback_name = fallback_name or source_obj.name
        if not _has_layer_system(source_obj):
            weight_dict = self._compute_weight_map_vertex_id(source_obj, source_vg_names)
            mask_dict = {v_idx: 1.0 for v_idx in weight_dict}
            return [(fallback_name, weight_dict, mask_dict, 0.0)]

        prev_active = context.view_layer.objects.active
        context.view_layer.objects.active = source_obj
        try:
            facade = CoreFacade(context)
            meta = facade.get_meta_list()
            prev_layer_idx = facade.get_active_layer_index()

            layers = []
            try:
                for m in meta:
                    idx = m.get("index", -1)
                    if idx < 0:
                        continue
                    name = m.get("name") or f"Layer {idx}"
                    mask_default = float(m.get("mask_default", 1.0))
                    facade.switch_to_layer(idx)
                    layers.append(
                        (name, facade.read_active_layer(), facade.get_active_mask_dict(), mask_default)
                    )
            finally:
                if prev_layer_idx is not None and prev_layer_idx >= 0:
                    facade.switch_to_layer(prev_layer_idx)
        finally:
            context.view_layer.objects.active = prev_active

        if layers:
            return layers

        weight_dict = self._compute_weight_map_vertex_id(source_obj, source_vg_names)
        mask_dict = {v_idx: 1.0 for v_idx in weight_dict}
        return [(fallback_name, weight_dict, mask_dict, 0.0)]


def register():
    bpy.utils.register_class(OBJECT_OT_mw_copy_skin_weight_transfer)


def unregister():
    bpy.utils.unregister_class(OBJECT_OT_mw_copy_skin_weight_transfer)
