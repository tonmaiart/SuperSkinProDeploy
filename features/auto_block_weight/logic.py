"""Auto-assign logic — Python gathers bone/vertex data, Rust picks the closest bone per vertex
by straight-line distance to its segment."""

import array

from ...core.facade import CoreFacade


def gather_auto_bone_data(core_facade, arm_obj):
    """Prepare bone head/tail data and name→name map for auto()."""
    obj = core_facade.get_obj()
    storage = obj.superskin_storage
    selected_pool = core_facade.get_selected_bones_pool()

    if not selected_pool:
        active_idx = storage.last_clicked_index
        if 0 <= active_idx < len(obj.vertex_groups):
            selected_pool = {obj.vertex_groups[active_idx].name}
        else:
            raise ValueError("No bone selected")

    bone_data = []
    bone_name_to_name = {}
    arm_mat = arm_obj.matrix_world
    locks = core_facade.get_bone_locks()

    for pb in arm_obj.pose.bones:
        db = arm_obj.data.bones.get(pb.name)
        if not db or not db.use_deform:
            continue
        if locks.get(pb.name, False):
            continue
        if pb.name not in selected_pool:
            continue
        vg = obj.vertex_groups.get(pb.name)
        if not vg:
            vg = obj.vertex_groups.new(name=pb.name)
        bone_name_to_name[pb.name] = pb.name
        bone_data.append((
            pb.name,
            (arm_mat @ db.head_local).to_tuple(),
            (arm_mat @ db.tail_local).to_tuple(),
        ))

    if not bone_data:
        raise ValueError("No valid bones in selection pool")
    return bone_data, bone_name_to_name


def apply(selected_verts, selected_world_coords_flat, bone_data, bone_name_to_name):
    """Find the closest deform bone for each selected vertex returning String Name."""
    rust = CoreFacade.get_rust_gateway("auto_logic")
    assignment_rust = rust.call(
        "rust_auto_logic",
        array.array("q", (int(v) for v in selected_verts)),
        selected_world_coords_flat,
        [(str(n), (float(h[0]), float(h[1]), float(h[2])),
                (float(t[0]), float(t[1]), float(t[2]))) for n, h, t in bone_data],
        {str(k): str(v) for k, v in bone_name_to_name.items()},
    )
    return {int(k): v for k, v in assignment_rust.items()}
