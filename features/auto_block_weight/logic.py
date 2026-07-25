"""Auto-assign logic — BVH raycast precompute in Python, scoring in Rust.

⚡ NOTE: Auto-assign remains string-based (bone names). The assignment result
uses bone names directly since it feeds into the storage layer (String-keyed).
The int-ID pipeline is NOT used for auto — UIController handles string→storage
directly.
"""

from mathutils import Vector
from ...core.facade import CoreFacade


def gather_auto_bone_data(core_facade, arm_obj):
    """Prepare bone head/tail data and name→name map for auto().

    Replaces the UIController._gather_auto_bone_data escape hatch — parametrized
    over CoreFacade so that core/ui_controller/ carries no feature-specific logic.
    """
    obj = core_facade.get_obj()
    storage = obj.superskin_storage
    selected_pool = storage.selected_names

    if selected_pool == ",":
        active_idx = storage.last_clicked_index
        if 0 <= active_idx < len(obj.vertex_groups):
            selected_pool = f",{obj.vertex_groups[active_idx].name},"
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
        if f",{pb.name}," not in selected_pool:
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


def apply(selected_verts, vertex_world_coords, bone_data, bone_name_to_name, bvh_tree, neighbors):
    """Find the closest deform bone for each selected vertex returning String Name.

    `neighbors` (from `CoreFacade.get_cached_mesh_neighbors()`) is the plain
    1-ring vertex-adjacency map. Rust uses it to absorb small, topologically
    isolated same-label fragments into whichever larger same-pair region they
    border, so the winning bone stays consistent across an edge-loop ring
    instead of flipping vertex-by-vertex at contested boundaries.
    """
    # 🛡️ BVH raycast ต้องทำใน Python — ข้าม FFI ไม่ได้
    # Pre-compute hit_count_map: {v_idx: {bone_name: hit_count}}
    hit_count_map = {}
    for v_idx in selected_verts:
        v_w = Vector(vertex_world_coords[v_idx])
        hit_count_map[v_idx] = {}

        for name, head, tail in bone_data:
            head_v = Vector(head)
            tail_v = Vector(tail)
            bone_vec = tail_v - head_v
            bone_len_sq = bone_vec.dot(bone_vec)

            if bone_len_sq < 1e-12:
                closest_pt = head_v
                radial_dist = (v_w - head_v).length
                ray_dir = (
                    (head_v - v_w).normalized() if radial_dist > 1e-12
                    else Vector((0, 0, 1))
                )
            else:
                to_vert = v_w - head_v
                percent = to_vert.dot(bone_vec) / bone_len_sq
                percent_clamped = max(0.0, min(1.0, percent))
                closest_pt = head_v + bone_vec * percent_clamped
                radial_dist = (v_w - closest_pt).length
                ray_dir = (
                    (closest_pt - v_w).normalized() if radial_dist > 1e-12
                    else Vector((0, 0, 1))
                )

            hit_count = 0
            if radial_dist > 0.0001:
                current_origin = v_w + ray_dir * 0.0005
                remaining_dist = radial_dist - 0.001

                while remaining_dist > 0.0005 and hit_count < 4:
                    hit = bvh_tree.ray_cast(
                        current_origin, ray_dir, remaining_dist
                    )
                    if hit[0] is not None:
                        hit_count += 1
                        hit_pos = hit[1]
                        step = (hit_pos - current_origin).length + 0.0005
                        current_origin = hit_pos + ray_dir * 0.0005
                        remaining_dist -= step
                    else:
                        break

            hit_count_map[v_idx][name] = hit_count

    rust = CoreFacade.get_rust_gateway("auto_logic")
    assignment_rust, debug_summary = rust.call(
        "rust_auto_logic",
        [int(v) for v in selected_verts],
        [(float(c[0]), float(c[1]), float(c[2])) for c in vertex_world_coords],
        [(str(n), (float(h[0]), float(h[1]), float(h[2])),
                (float(t[0]), float(t[1]), float(t[2]))) for n, h, t in bone_data],
        {str(k): str(v) for k, v in bone_name_to_name.items()},
        {int(k): {str(bn): int(hc) for bn, hc in v.items()}
         for k, v in hit_count_map.items()},
        {int(k): [int(n) for n in v] for k, v in neighbors.items()},
    )
    CoreFacade.debug_log("adhoc:auto_assign", debug_summary)
    return {int(k): v for k, v in assignment_rust.items()}
