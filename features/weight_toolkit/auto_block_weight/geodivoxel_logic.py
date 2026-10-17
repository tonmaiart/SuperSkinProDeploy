
import array

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ....core.facade import CoreFacade
from .logic import apply

TARGET_TOTAL_VOXELS = 150_000
MAX_TOTAL_VOXELS = 4_000_000
RAY_DIR = Vector((1.0, 1.13, 1.7)).normalized()
RAY_EPS = 1e-5
SEED_SEARCH_RADIUS = 2


def _voxel_index(ix: int, iy: int, iz: int, nx: int, ny: int) -> int:
    return ix + nx * (iy + ny * iz)


def _world_to_cell(point, origin, cell_size, nx, ny, nz):
    ix = min(nx - 1, max(0, int((point.x - origin.x) / cell_size)))
    iy = min(ny - 1, max(0, int((point.y - origin.y) / cell_size)))
    iz = min(nz - 1, max(0, int((point.z - origin.z) / cell_size)))
    return ix, iy, iz


def _is_inside(bvh, point) -> bool:
    origin = point.copy()
    count = 0
    for _ in range(64):
        hit_loc, _normal, _idx, _dist = bvh.ray_cast(origin, RAY_DIR)
        if hit_loc is None:
            break
        count += 1
        origin = hit_loc + RAY_DIR * RAY_EPS
    return count % 2 == 1


def _nearest_inside_cell(inside, ix, iy, iz, nx, ny, nz):
    if inside[_voxel_index(ix, iy, iz, nx, ny)]:
        return ix, iy, iz
    for r in range(1, SEED_SEARCH_RADIUS + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    cx, cy, cz = ix + dx, iy + dy, iz + dz
                    if not (0 <= cx < nx and 0 <= cy < ny and 0 <= cz < nz):
                        continue
                    if inside[_voxel_index(cx, cy, cz, nx, ny)]:
                        return cx, cy, cz
    return None


def gather_geodivoxel_data(core_facade, bone_data) -> dict:
    obj = core_facade.get_obj()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(obj, depsgraph)

    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    min_corner = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    max_corner = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    extent = max_corner - min_corner
    volume = max(extent.x, 1e-6) * max(extent.y, 1e-6) * max(extent.z, 1e-6)

    cell_size = max((volume / TARGET_TOTAL_VOXELS) ** (1.0 / 3.0), 1e-6)
    nx = max(1, round(extent.x / cell_size) + 1)
    ny = max(1, round(extent.y / cell_size) + 1)
    nz = max(1, round(extent.z / cell_size) + 1)
    total = nx * ny * nz
    if total > MAX_TOTAL_VOXELS:
        scale = (total / MAX_TOTAL_VOXELS) ** (1.0 / 3.0)
        cell_size *= scale
        nx = max(1, round(extent.x / cell_size) + 1)
        ny = max(1, round(extent.y / cell_size) + 1)
        nz = max(1, round(extent.z / cell_size) + 1)

    inside = bytearray(nx * ny * nz)
    for iz in range(nz):
        z = min_corner.z + (iz + 0.5) * cell_size
        for iy in range(ny):
            y = min_corner.y + (iy + 0.5) * cell_size
            for ix in range(nx):
                x = min_corner.x + (ix + 0.5) * cell_size
                if _is_inside(bvh, Vector((x, y, z))):
                    inside[_voxel_index(ix, iy, iz, nx, ny)] = 1

    bone_seed_voxels = []
    for _name, head, tail in bone_data:
        head_v = Vector(head)
        tail_v = Vector(tail)
        length = (tail_v - head_v).length
        steps = max(1, int(length / cell_size) + 1)
        seeds = set()
        for s in range(steps + 1):
            t = s / steps
            sample = head_v.lerp(tail_v, t)
            ix, iy, iz = _world_to_cell(sample, min_corner, cell_size, nx, ny, nz)
            cell = _nearest_inside_cell(inside, ix, iy, iz, nx, ny, nz)
            if cell is not None:
                seeds.add(_voxel_index(*cell, nx, ny))
        bone_seed_voxels.append(sorted(seeds))

    return {
        "inside": inside,
        "nx": nx, "ny": ny, "nz": nz,
        "cell_size": cell_size,
        "origin": min_corner,
        "bone_seed_voxels": bone_seed_voxels,
    }


def apply_geodivoxel(core_facade, bone_data, voxel_data, selected_verts) -> dict:
    obj = core_facade.get_obj()
    mesh = core_facade.get_mesh()
    mat = obj.matrix_world
    mesh_verts = mesh.vertices

    origin = voxel_data["origin"]
    cell_size = voxel_data["cell_size"]
    nx, ny, nz = voxel_data["nx"], voxel_data["ny"], voxel_data["nz"]

    query_voxels = array.array("q")
    for v_idx in selected_verts:
        world = mat @ mesh_verts[v_idx].co
        ix, iy, iz = _world_to_cell(world, origin, cell_size, nx, ny, nz)
        query_voxels.append(_voxel_index(ix, iy, iz, nx, ny))

    rust = CoreFacade.get_rust_gateway("geodivoxel_logic")
    distances = rust.call(
        "rust_geodivoxel_logic",
        array.array("B", voxel_data["inside"]), nx, ny, nz, cell_size,
        voxel_data["bone_seed_voxels"], query_voxels,
    )

    result = {}
    unresolved = []
    for i, v_idx in enumerate(selected_verts):
        weights = {}
        total = 0.0
        for bone_idx, dist in enumerate(distances[i]):
            if dist == float("inf"):
                continue
            w = 1.0 / (dist * dist + 1e-6)
            weights[bone_data[bone_idx][0]] = w
            total += w
        if total > 1e-9:
            result[v_idx] = {name: w / total for name, w in weights.items()}
        else:
            unresolved.append(v_idx)

    if unresolved:
        bone_name_to_name = {name: name for name, _, _ in bone_data}
        world_coords_flat = array.array("d", [0.0]) * (len(unresolved) * 3)
        for out_i, v_idx in enumerate(unresolved):
            co = mat @ mesh_verts[v_idx].co
            base = out_i * 3
            world_coords_flat[base] = co.x
            world_coords_flat[base + 1] = co.y
            world_coords_flat[base + 2] = co.z
        fallback = apply(
            selected_verts=unresolved,
            selected_world_coords_flat=world_coords_flat,
            bone_data=bone_data,
            bone_name_to_name=bone_name_to_name,
        )
        for v_idx, name in fallback.items():
            result[v_idx] = {name: 1.0}

    return result
