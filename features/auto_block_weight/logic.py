"""Auto-assign logic — Python gathers bone/vertex data, Rust picks the
closest bone per vertex by straight-line distance to its segment.

⚡ NOTE: Auto-assign remains string-based (bone names). The assignment result
uses bone names directly since it feeds into the storage layer (String-keyed).
The int-ID pipeline is NOT used for auto — UIController handles string→storage
directly.
"""

import array

from ...core.facade import CoreFacade


def gather_auto_bone_data(core_facade, arm_obj):
    """Prepare bone head/tail data and name→name map for auto().

    Replaces the UIController._gather_auto_bone_data escape hatch — parametrized
    over CoreFacade so that core/ui_controller/ carries no feature-specific logic.
    """
    obj = core_facade.get_obj()
    storage = obj.superskin_storage
    # Mode-aware: in Edit Mode this reads the undo-safe __ssp_pool BMesh
    # state instead of storage.selected_names directly, which is not
    # reliably undo-tracked while in Edit Mode.
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
    """Find the closest deform bone for each selected vertex returning String Name.

    Straight-line distance from each vertex to the closest point on each
    bone's segment (head→tail, clamped), argmin — computed entirely in Rust,
    parallelized across vertices via Rayon (see `rust_logic/src/auto_logic.rs`).

    ⚡ `selected_world_coords_flat` must be a flat `array.array('d', ...)` of
    length `3 * len(selected_verts)`, positionally paired with
    `selected_verts` (`selected_world_coords_flat[3*i:3*i+3]` is vertex
    `selected_verts[i]`'s world coordinate) — NOT a list of (x, y, z) tuples
    and NOT indexed by raw vertex id across the whole mesh. A flat numeric
    buffer lets PyO3 extract the payload as one contiguous copy instead of
    visiting one Python tuple object per vertex, and lets the caller
    transform only the selected subset instead of every vertex in the mesh;
    see `auto_block_feature.py`'s `execute()`, which builds this buffer
    directly during the world-space transform instead of materializing an
    intermediate list of tuples first.
    """
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
