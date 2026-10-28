
import bpy
import numpy as np

from ...core.facade import CoreFacade
from ...interface.utils.utils import _has_layer_system
from ..weight_apply.public_api import read_vertex_select
from . import transfer_core

OUTPUT_LAYER_NAME = "Transfer Output"


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
        if context.mode.startswith('EDIT'):
            return False
        state = getattr(context.scene, "superskin_weight_transfer_state", None)
        if state is None:
            return False
        return (any(e.object and e.object.type == 'MESH' and e.is_source for e in state.entries) and
                any(e.object and e.object.type == 'MESH' and not e.is_source for e in state.entries))

    def execute(self, context):
        with transfer_core.preserving_selection(context):
            return self._transfer(context)

    def _transfer(self, context):
        prefs = _get_prefs()
        force_init_layer = bool(prefs and prefs.force_init_layer)
        transfer_method = prefs.transfer_method if prefs else 'CLOSEST_DISTANCE'

        state = context.scene.superskin_weight_transfer_state
        source_entries = [e for e in state.entries if e.object and e.object.type == 'MESH' and e.is_source]

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): force_init_layer={force_init_layer} "
            f"transfer_method={transfer_method}",
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
            vg.name for obj in source_objs
            for managed in (CoreFacade.managed_vg_names(obj),)
            for vg in obj.vertex_groups if vg.name in managed
        ))

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): sources={[o.name for o in source_objs]} "
            f"source_vg_names={source_vg_names} targets={[t.name for t in target_objs]}",
        )

        if not source_vg_names:
            self.report({'ERROR'}, "Source mesh has no Vertex Groups for its Armature's deform bones")
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

            transfer_core.ensure_armature_modifier(target, source_armature)
            [payload] = transfer_core.compute_layer_payloads(
                'MERGE', transfer_method, target, source_surface,
                composite_weights, composite_mask, [],
                merge_name=OUTPUT_LAYER_NAME, allowed_source_verts=allowed_source_verts,
                allowed_target_verts=allowed_target_verts, weight_surface=weight_surface,
            )

            if not (force_init_layer or _has_layer_system(target)):
                transfer_core.clear_native_weights(target, source_vg_names, allowed_target_verts)
                transfer_core.ensure_native_vertex_groups(target, source_vg_names)
                transfer_core.write_native_weights(target, payload[1])
                target.data.update()
                continue

            transfer_core.ensure_native_vertex_groups(target, source_vg_names)
            context.view_layer.objects.active = target
            transfer_core.write_layers_to_target(context, target, [payload])
            target.data.update()

        CoreFacade.debug_log(
            "feature_domains",
            f"weight_transfer.execute(): done, {len(target_objs)} target(s) written",
        )
        self.report({'INFO'}, "Weight transfer complete")
        return {'FINISHED'}

    def _compute_weight_map_vertex_id(self, source_obj, source_vg_names):
        wanted = set(source_vg_names)
        src_index_to_name = {vg.index: vg.name for vg in source_obj.vertex_groups
                             if vg.name in wanted}

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


def register():
    bpy.utils.register_class(OBJECT_OT_mw_copy_skin_weight_transfer)


def unregister():
    bpy.utils.unregister_class(OBJECT_OT_mw_copy_skin_weight_transfer)
