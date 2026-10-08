
import array

import bmesh
from mathutils.bvhtree import BVHTree

from .temp_vg_bridge import PREFIX


def get_local_mapping(obj) -> tuple[dict[str, int], dict[int, str]]:
    bone_to_id = {}
    id_to_bone = {}
    for vg in obj.vertex_groups:
        if not vg.name.startswith(PREFIX):
            bone_to_id[vg.name] = vg.index
            id_to_bone[vg.index] = vg.name
    return bone_to_id, id_to_bone


def real_vg_count(obj) -> int:
    return sum(1 for vg in obj.vertex_groups if not vg.name.startswith(PREFIX))


def known_bone_names(id_to_bone: dict, real_count: int) -> set:
    return {name for idx, name in id_to_bone.items() if idx < real_count}


def get_unified_mapping(obj) -> tuple[dict[str, int], dict[int, str]]:
    bone_to_id, id_to_bone = get_local_mapping(obj)
    synthetic_id = len(id_to_bone)
    for item in getattr(obj, 'superskin_bones_collection', ()):
        if item.is_orphan and item.name not in bone_to_id:
            bone_to_id[item.name] = synthetic_id
            id_to_bone[synthetic_id] = item.name
            synthetic_id += 1
    return bone_to_id, id_to_bone


def build_mesh_neighbors(mesh) -> dict:
    neighbors = {}
    flat = array.array('i', bytes(4 * 2 * len(mesh.edges)))
    mesh.edges.foreach_get("vertices", flat)
    it = iter(flat.tolist())
    for v0, v1 in zip(it, it):
        neighbors.setdefault(v0, set()).add(v1)
        neighbors.setdefault(v1, set()).add(v0)
    return neighbors


def collect_mesh_weights(mesh, vert_indices: set) -> dict:
    result = {}
    for v_idx in vert_indices:
        vw = {}
        for g in mesh.vertices[v_idx].groups:
            vw[g.group] = g.weight
        if vw:
            result[v_idx] = vw
    return result


def get_vertex_coordinates(mesh) -> list:
    co = array.array('f', bytes(4 * 3 * len(mesh.vertices)))
    mesh.vertices.foreach_get("co", co)
    it = iter(co.tolist())
    return list(zip(it, it, it))


def build_bvh_tree(mesh):
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    return bvh
