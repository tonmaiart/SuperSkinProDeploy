
import array

from ....core.facade import CoreFacade
from .logic import apply

HEAT_MAP_ITERATIONS = 25


def apply_heat_map(core_facade, bone_data, selected_verts) -> dict:
    obj = core_facade.get_obj()
    mesh = core_facade.get_mesh()
    mat = obj.matrix_world
    num_verts = core_facade.get_num_verts()
    mesh_verts = mesh.vertices

    all_verts = list(range(num_verts))
    world_coords_flat = array.array("d", [0.0]) * (num_verts * 3)
    for v_idx in all_verts:
        co = mat @ mesh_verts[v_idx].co
        base = v_idx * 3
        world_coords_flat[base] = co.x
        world_coords_flat[base + 1] = co.y
        world_coords_flat[base + 2] = co.z

    bone_name_to_name = {name: name for name, _, _ in bone_data}
    seed = apply(
        selected_verts=all_verts,
        selected_world_coords_flat=world_coords_flat,
        bone_data=bone_data,
        bone_name_to_name=bone_name_to_name,
    )

    bone_to_id = {name: i for i, (name, _, _) in enumerate(bone_data)}
    id_to_bone = {i: name for name, i in bone_to_id.items()}
    seed_int_layer = {v: {bone_to_id[name]: 1.0} for v, name in seed.items()}

    vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(seed_int_layer)

    local_coords = core_facade.get_vertex_coordinates()
    coords = dict(enumerate(local_coords))
    neighbors = core_facade.get_cached_mesh_neighbors()

    rust = CoreFacade.get_rust_gateway("smooth_logic")
    out_v, out_b, out_w, _res_mask = rust.call(
        "rust_smooth_logic",
        vert_ids, bone_ids, weights, {}, all_verts, coords, neighbors,
        [1.0] * HEAT_MAP_ITERATIONS, {}, False, False, {}, 0.0,
    )
    result_int = CoreFacade.coo_to_layer(out_v, out_b, out_w)

    selected_set = set(selected_verts)
    return {
        v: {id_to_bone[b]: w for b, w in bone_weights.items()}
        for v, bone_weights in result_int.items() if v in selected_set
    }
