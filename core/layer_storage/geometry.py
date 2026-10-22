
import array

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from .temp_vg_bridge import PREFIX


def armatures_of(obj) -> list:
    arms = []
    for mod in obj.modifiers:
        arm = mod.object if mod.type == 'ARMATURE' else None
        if arm is not None and arm.type == 'ARMATURE' and arm not in arms:
            arms.append(arm)
    return arms


def mesh_users(mesh) -> list:
    return [obj for obj in bpy.data.objects if obj.type == 'MESH' and obj.data == mesh]


def deform_bone_names(obj) -> frozenset:
    names = set()
    for arm in armatures_of(obj):
        names.update(b.name for b in arm.data.bones if b.use_deform)
    return frozenset(names)


def managed_vg_names(obj) -> frozenset:
    deform = deform_bone_names(obj)
    if not deform:
        return frozenset()
    return frozenset(vg.name for vg in obj.vertex_groups
                     if vg.name in deform and not vg.name.startswith(PREFIX))


def real_vg_names(obj) -> frozenset:
    return frozenset(vg.name for vg in obj.vertex_groups if not vg.name.startswith(PREFIX))


def owned_vg_names(obj):
    if any(arm.data.is_editmode for arm in armatures_of(obj)):
        return None
    if not deform_bone_names(obj):
        return None
    return managed_vg_names(obj)


def mesh_owned_names(mesh):
    result = None
    for obj in mesh_users(mesh):
        names = owned_vg_names(obj)
        if names is None:
            return None
        result = names if result is None else result | names
    return result


def get_local_mapping(obj) -> tuple[dict[str, int], dict[int, str]]:
    managed = managed_vg_names(obj)
    bone_to_id = {}
    id_to_bone = {}
    for vg in obj.vertex_groups:
        if vg.name in managed:
            bone_to_id[vg.name] = vg.index
            id_to_bone[vg.index] = vg.name
    return bone_to_id, id_to_bone


def get_unified_mapping(obj) -> tuple[dict[str, int], dict[int, str]]:
    return get_local_mapping(obj)


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
