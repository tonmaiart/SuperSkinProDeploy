
from . import refine_logic, soften_logic

DEFAULT_SMOOTH = 1.0
DEFAULT_REPEAT = 10


def bone_data_for(obj, bone_names):
    arm = next((m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)
    if arm is None:
        return []
    mat = arm.matrix_world
    data = []
    for name in sorted(bone_names):
        db = arm.data.bones.get(name)
        if db is not None and db.use_deform:
            data.append((name, (mat @ db.head_local).to_tuple(), (mat @ db.tail_local).to_tuple()))
    return data


def block_weights(core_facade, bone_names, verts, smooth=DEFAULT_SMOOTH, repeat=DEFAULT_REPEAT, mesh_graph=None):
    verts = sorted(verts)
    bone_data = bone_data_for(core_facade.get_obj(), bone_names)
    if not verts or not bone_data:
        return {}
    if mesh_graph is None:
        mesh_graph = refine_logic.build_mesh(core_facade)
    assignment = refine_logic.assign_bones(mesh_graph, bone_data, verts)
    if not assignment:
        return {}
    if smooth <= 0.0:
        return {v: {bone: 1.0} for v, bone in assignment.items()}
    return soften_logic.soften_assignment(
        core_facade, bone_data, assignment, min(smooth, soften_logic.MAX_SMOOTH),
        min(int(repeat), soften_logic.MAX_REPEAT), mesh_graph=mesh_graph,
    )
