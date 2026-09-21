"""Dev-only button for the Phase 0 Rust skinning validation spike."""
import sys

import bpy

TOLERANCE_PASS = 1e-4
TOLERANCE_MARGINAL = 1e-3


def _find_module(suffix):
    for name, mod in list(sys.modules.items()):
        if mod is not None and name.endswith(suffix):
            return mod
    raise RuntimeError(f"Could not find a loaded module ending in {suffix!r}.")


def _resolve_mesh_and_armature(obj):
    if obj is None or obj.type != 'MESH':
        raise RuntimeError("Active object must be a mesh bound to an Armature modifier.")
    arm_obj = None
    for mod in obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object is not None:
            arm_obj = mod.object
            break
    if arm_obj is None:
        raise RuntimeError(f"'{obj.name}' has no Armature modifier with an assigned object.")
    return obj, arm_obj


def _mat_to_rows(mat):
    return tuple(tuple(mat[r][c] for c in range(4)) for r in range(4))


def _build_bone_matrices(mesh_obj, arm_obj, log):
    mesh_world = mesh_obj.matrix_world
    mesh_world_inv = mesh_world.inverted()
    arm_world = arm_obj.matrix_world
    arm_world_inv = arm_world.inverted()

    bone_matrices = {}
    skipped_bones = []
    for vg in mesh_obj.vertex_groups:
        pose_bone = arm_obj.pose.bones.get(vg.name)
        if pose_bone is None:
            continue
        bone = pose_bone.bone
        try:
            bind_inv = bone.matrix_local.inverted()
        except ValueError:
            skipped_bones.append(vg.name)
            continue
        skin_mat_armature_space = pose_bone.matrix @ bind_inv
        skin_mat_local = (
            mesh_world_inv @ arm_world @ skin_mat_armature_space @ arm_world_inv @ mesh_world
        )
        bone_matrices[vg.index] = _mat_to_rows(skin_mat_local)

    if skipped_bones:
        log(f"WARNING: {len(skipped_bones)} bone(s) had a non-invertible rest "
            f"matrix, skipped: {skipped_bones}")
    return bone_matrices


def _read_rest_coords_and_weights(mesh_obj):
    mesh = mesh_obj.data
    rest_coords = {v.index: (v.co.x, v.co.y, v.co.z) for v in mesh.vertices}
    weights = {}
    for v in mesh.vertices:
        vw = {g.group: g.weight for g in v.groups if g.weight > 0.0}
        if vw:
            weights[v.index] = vw
    return rest_coords, weights


def _extract_ground_truth(mesh_obj, num_verts):
    fab_mod = _find_module("core_subsystems.rust_weight_engine.flat_array_bridge")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = mesh_obj.evaluated_get(depsgraph)
    flat = fab_mod.extract_deformed_coords(obj_eval, num_verts)
    if flat is None:
        raise RuntimeError(
            "extract_deformed_coords() failed -- vertex count mismatch or eval error."
        )
    return {i: (flat[i * 3], flat[i * 3 + 1], flat[i * 3 + 2]) for i in range(num_verts)}


class SUPERSKIN_OT_dev_skin_spike(bpy.types.Operator):
    """Compare the new Rust skinning math against Blender's real Armature modifier result on
    the active mesh, and report max/mean/RMS delta."""
    bl_idname = "superskin.dev_skin_spike"
    bl_label = "Run Skin Spike (dev)"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        log_lines = []

        def log(msg):
            line = f"[skin_spike] {msg}"
            print(line)
            log_lines.append(line)

        try:
            mesh_obj, arm_obj = _resolve_mesh_and_armature(context.active_object)
            mesh = mesh_obj.data
            num_verts = len(mesh.vertices)
            log(f"mesh={mesh_obj.name!r} verts={num_verts} armature={arm_obj.name!r}")

            if mesh_obj.matrix_world == arm_obj.matrix_world:
                log("mesh/armature share a world transform -- skin matrices reduce to "
                    "pose_bone.matrix @ bone.matrix_local.inverted().")
            else:
                log("mesh/armature world transforms differ -- exercising the full "
                    "object-to-armature-space composition path.")

            bone_matrices = _build_bone_matrices(mesh_obj, arm_obj, log)
            if not bone_matrices:
                raise RuntimeError("No matching pose bones found for this mesh's vertex groups.")
            log(f"built {len(bone_matrices)} bone skin matrices")

            rest_coords, weights = _read_rest_coords_and_weights(mesh_obj)
            multi_bone_verts = sum(1 for vw in weights.values() if len(vw) >= 2)
            log(f"{len(weights)} verts have weight, {multi_bone_verts} span >=2 bones")
            if multi_bone_verts == 0:
                log("WARNING: no multi-bone vertex found -- this test can't catch a "
                    "weighted-blend bug.")

            rwe_mod = _find_module("core_subsystems.rust_weight_engine.rust_weight_engine")
            prefs_mod = _find_module("core_subsystems.preferences.preferences_service")
            rust_module = rwe_mod._internal_load_binary()
            if rust_module is None:
                raise RuntimeError(
                    "rust_logic binary not found -- run rust_logic/build.py and restart Blender."
                )
            if not hasattr(rust_module, "rust_skin_positions"):
                raise RuntimeError(
                    "This rust_logic binary predates rust_skin_positions -- rebuild "
                    "(python rust_logic/build.py) and fully restart Blender."
                )
            license_key = prefs_mod.PreferencesService.get_license_key()
            activation_token = prefs_mod.PreferencesService.get_activation_token()

            rust_positions = rust_module.rust_skin_positions(
                rest_coords, weights, bone_matrices, license_key, activation_token,
            )

            ground_truth = _extract_ground_truth(mesh_obj, num_verts)

            max_delta = 0.0
            sum_delta = 0.0
            sum_sq_delta = 0.0
            worst_v = None
            compared = 0
            for v_idx, truth_co in ground_truth.items():
                rust_co = rust_positions.get(v_idx, rest_coords[v_idx])
                dx = rust_co[0] - truth_co[0]
                dy = rust_co[1] - truth_co[1]
                dz = rust_co[2] - truth_co[2]
                delta = max(abs(dx), abs(dy), abs(dz))
                sum_delta += delta
                sum_sq_delta += delta * delta
                compared += 1
                if delta > max_delta:
                    max_delta = delta
                    worst_v = v_idx

            mean_delta = sum_delta / compared if compared else 0.0
            rms_delta = (sum_sq_delta / compared) ** 0.5 if compared else 0.0

            log(f"compared {compared} vertices")
            log(f"max_delta={max_delta:.6f}  mean_delta={mean_delta:.6f}  "
                f"rms_delta={rms_delta:.6f}  worst_vertex={worst_v}")

            if max_delta < TOLERANCE_PASS:
                verdict = f"PASS -- within {TOLERANCE_PASS} tolerance."
                report_type = {'INFO'}
            elif max_delta < TOLERANCE_MARGINAL:
                verdict = (f"MARGINAL -- under {TOLERANCE_MARGINAL} but not {TOLERANCE_PASS}. "
                           f"Likely float precision noise -- investigate.")
                report_type = {'WARNING'}
            else:
                verdict = (f"FAIL -- delta too large to be float noise "
                           f"(worst vertex {worst_v}). Likely a transform/space bug.")
                report_type = {'ERROR'}
            log(verdict)

            self.report(report_type, f"Skin spike: max_delta={max_delta:.6f} -- {verdict}")
        except Exception as exc:
            print(f"[skin_spike] ERROR: {exc}")
            self.report({'ERROR'}, f"Skin spike failed: {exc}")
            return {'CANCELLED'}

        return {'FINISHED'}


def draw_button(layout):
    layout.operator(SUPERSKIN_OT_dev_skin_spike.bl_idname, icon='EXPERIMENTAL')
    layout.label(text="Full report prints to the System Console.")


def register():
    bpy.utils.register_class(SUPERSKIN_OT_dev_skin_spike)


def unregister():
    bpy.utils.unregister_class(SUPERSKIN_OT_dev_skin_spike)
