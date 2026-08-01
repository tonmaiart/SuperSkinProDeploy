"""Export/Import Weight JSON — merged in from the old `features/data_io/` domain.

Import is now just the same "closest point on surface" transfer as
`object.mw_copy_skin_weight_maya`, using the same `layer_output` /
`insert_method` / `transfer_method` prefs — the only difference is that the
"source" is reconstructed from a JSON file instead of a live mesh, via
`transfer_core`.

JSON schema (version 2 — v1 files from the old `data_io` domain are not
forward-compatible: they never stored vertex positions/triangles, which
`CLOSEST_DISTANCE` needs to rebuild a source surface):

    {
      "version": 2,
      "vertex_count": int,
      "positions": [[x, y, z], ...],           # world-space, index = vertex index
      "triangles": [[i, j, k], ...],            # triangulated faces
      "composite_weights": {"<v_idx>": {"<bone_name>": weight, ...}, ...},
      "armature_name": str | null,              # name of the source's bound Armature object, if any
      "active_layer_index": int,
      "layers": [
        {"index": int, "name": str, "mask_default": float,
         "weights": {"<v_idx>": {"<bone_name>": weight}},
         "mask": {"<v_idx>": mask_value}},
        ...
      ]
    }
"""

import bpy
import json
import os
import mathutils
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ...core.facade import CoreFacade
from ...interface.utils.utils import _has_layer_system
from . import transfer_core

_PRECISION = 5


def _get_prefs():
    wm = bpy.context.window_manager
    return getattr(wm, "superskin_weight_transfer_prefs", None)


class WM_OT_superskin_export_json(bpy.types.Operator, ExportHelper):
    """Export the active mesh's weights (flattened composite + full Layer stack) to JSON"""
    bl_idname = "superskin.export_weight_json"
    bl_label = "Export Weights to JSON"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH')

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        mesh.calc_loop_triangles()
        matrix_world = obj.matrix_world

        positions = [list(matrix_world @ v.co) for v in mesh.vertices]
        triangles = [list(lt.vertices) for lt in mesh.loop_triangles]

        idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
        composite_weights = {}
        for v in mesh.vertices:
            bone_weights = {
                idx_to_name[g.group]: round(g.weight, _PRECISION)
                for g in v.groups
                if g.group in idx_to_name and g.weight > 0.0
            }
            if bone_weights:
                composite_weights[str(v.index)] = bone_weights

        source_armature = next((m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)

        export_data = {
            "version": 2,
            "vertex_count": len(mesh.vertices),
            "positions": positions,
            "triangles": triangles,
            "composite_weights": composite_weights,
            "armature_name": source_armature.name if source_armature else None,
            "active_layer_index": -1,
            "layers": [],
        }

        if _has_layer_system(obj):
            facade = CoreFacade(context)
            meta = facade.get_meta_list()
            prev_idx = facade.get_active_layer_index()

            for m in meta:
                idx = m.get("index", -1)
                if idx < 0:
                    continue
                facade.switch_to_layer(idx, push_undo=False)
                weight_dict = facade.read_active_layer()
                mask_dict = facade.get_active_mask_dict()
                export_data["layers"].append({
                    "index": idx,
                    "name": m.get("name", f"Layer {idx}"),
                    "mask_default": float(m.get("mask_default", 1.0)),
                    "weights": {
                        str(v_idx): {b: round(w, _PRECISION) for b, w in bone_weights.items()}
                        for v_idx, bone_weights in weight_dict.items()
                    },
                    "mask": {str(v_idx): round(mv, _PRECISION) for v_idx, mv in mask_dict.items()},
                })

            if prev_idx is not None and prev_idx >= 0:
                facade.switch_to_layer(prev_idx, push_undo=False)
                export_data["active_layer_index"] = prev_idx

        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f)
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"Exported {len(export_data['layers'])} layer(s) + composite to {os.path.basename(self.filepath)}",
        )
        return {'FINISHED'}


class WM_OT_superskin_import_json(bpy.types.Operator, ImportHelper):
    """Import weights from JSON as a closest-point-on-surface transfer onto the active mesh"""
    bl_idname = "superskin.import_weight_json"
    bl_label = "Import Weights from JSON"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".json"
    filter_glob: bpy.props.StringProperty(default="*.json", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == 'MESH')

    def execute(self, context):
        prefs = _get_prefs()
        layer_output = prefs.layer_output if prefs else 'SEPARATE'
        insert_method = prefs.insert_method if prefs else 'REPLACE'
        transfer_method = prefs.transfer_method if prefs else 'CLOSEST_DISTANCE'
        auto_assign_armature = prefs.auto_assign_armature if prefs else True

        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read JSON: {e}")
            return {'CANCELLED'}

        if "positions" not in data or "layers" not in data:
            self.report(
                {'ERROR'},
                "Invalid or outdated file format (missing 'positions'/'layers') — "
                "re-export with the current Weight Transfer exporter",
            )
            return {'CANCELLED'}

        target = context.active_object
        vertex_count = data.get("vertex_count", -1)

        if transfer_method == 'VERTEX_ID' and len(target.data.vertices) != vertex_count:
            self.report(
                {'ERROR'},
                f"Transfer Method 'Vertex ID' ต้องการจำนวน Vertex เท่ากัน "
                f"(ไฟล์มี {vertex_count}, target มี {len(target.data.vertices)})",
            )
            return {'CANCELLED'}

        composite_weights = {int(k): v for k, v in data.get("composite_weights", {}).items()}
        composite_mask = {v_idx: 1.0 for v_idx in composite_weights}

        layers = [
            (
                layer.get("name", f"Layer {layer.get('index', 0)}"),
                {int(k): v for k, v in layer.get("weights", {}).items()},
                {int(k): v for k, v in layer.get("mask", {}).items()},
                float(layer.get("mask_default", 1.0)),
            )
            for layer in data.get("layers", [])
        ]
        if not layers:
            layers = [(os.path.basename(self.filepath), composite_weights, composite_mask, 0.0)]

        bone_names = {bone for bw in composite_weights.values() for bone in bw}
        for _name, weight_dict, _mask_dict, _mask_default in layers:
            for bone_weights in weight_dict.values():
                bone_names.update(bone_weights.keys())

        if insert_method == 'REPLACE':
            target.vertex_groups.clear()

        if auto_assign_armature:
            armature_name = data.get("armature_name")
            if armature_name:
                armature_obj = bpy.data.objects.get(armature_name)
                if armature_obj is None or armature_obj.type != 'ARMATURE':
                    armature_data = bpy.data.armatures.new(armature_name)
                    armature_obj = bpy.data.objects.new(armature_name, armature_data)
                    context.collection.objects.link(armature_obj)
                transfer_core.ensure_armature_modifier(target, armature_obj)

        transfer_core.ensure_native_vertex_groups(target, bone_names)

        context.view_layer.objects.active = target

        source_surface = None
        if transfer_method == 'CLOSEST_DISTANCE':
            positions = [mathutils.Vector(p) for p in data.get("positions", [])]
            triangles = [tuple(t) for t in data.get("triangles", [])]
            source_surface = transfer_core.build_surface(positions, triangles)

        layer_payloads = transfer_core.compute_layer_payloads(
            layer_output, transfer_method, target, source_surface,
            composite_weights, composite_mask, layers,
            merge_name=f"Transfer from {os.path.basename(self.filepath)}",
        )

        transfer_core.write_layers_to_target(context, target, insert_method, layer_payloads)
        target.data.update()

        self.report({'INFO'}, f"Imported weight data from {os.path.basename(self.filepath)}")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(WM_OT_superskin_export_json)
    bpy.utils.register_class(WM_OT_superskin_import_json)


def unregister():
    bpy.utils.unregister_class(WM_OT_superskin_import_json)
    bpy.utils.unregister_class(WM_OT_superskin_export_json)
