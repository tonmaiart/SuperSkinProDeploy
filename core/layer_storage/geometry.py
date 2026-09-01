"""Mesh-geometry harvesting helpers.

Pure geometry operations — no custom-property I/O, no data encoding.
All functions accept an explicit mesh/obj parameter (no ``self.mesh``).
"""

import bmesh
from mathutils.bvhtree import BVHTree


def get_local_mapping(obj) -> tuple[dict[str, int], dict[int, str]]:
    """Generates two-way fast-lookup tables for runtime mapping (O(1)).

    Returns:
        (bone_to_id, id_to_bone) where:
        - bone_to_id: ``{vg_name: vg_index}``
        - id_to_bone: ``{vg_index: vg_name}``
    """
    bone_to_id = {vg.name: vg.index for vg in obj.vertex_groups
                  if not vg.name.startswith("__ssp_")}
    id_to_bone = {vg.index: vg.name for vg in obj.vertex_groups
                  if not vg.name.startswith("__ssp_")}
    return bone_to_id, id_to_bone


def real_vg_count(obj) -> int:
    """Count of real (non-``__ssp_*``) vertex groups on *obj*.

    The single authoritative boundary between a real vertex-group index and
    a synthetic orphan ID: every synthetic ID assigned by
    ``get_unified_mapping()`` starts here, and any downstream code that
    needs to classify an int-keyed bone ID as "real" vs. "orphan" must
    compare against this same value.

    MUST NOT be derived from ``len(obj.vertex_groups)`` directly -- while
    Edit Mode temp VGs are loaded, ``obj.vertex_groups`` also holds one
    ``__ssp_N`` per bone (real and orphan) plus ``__ssp_m``/``__ssp_meta``,
    roughly doubling the count. Three independent call sites each derived
    this value inline and got bitten by that inflation in different ways
    before it was consolidated here -- see docs/bug-history/0026.
    """
    return sum(1 for vg in obj.vertex_groups if not vg.name.startswith("__ssp_"))


def known_bone_names(id_to_bone: dict, real_count: int) -> set:
    """Names in *id_to_bone* backed by a real vertex group (index < *real_count*),
    i.e. excluding synthetic orphan IDs. Pass ``real_vg_count(obj)`` as *real_count*.
    """
    return {name for idx, name in id_to_bone.items() if idx < real_count}


def get_unified_mapping(obj) -> tuple[dict[str, int], dict[int, str]]:
    """Like get_local_mapping but also assigns synthetic int IDs to orphaned
    bones (in superskin_bones_collection but not in vertex_groups), so
    downstream code treats real and orphaned bones identically.

    The synthetic ID sequence starts at real_vg_count(obj), not
    len(obj.vertex_groups) -- see that function's docstring for why an
    inflated starting point desyncs an orphan's assigned ID from the
    __ssp_N VG name it was already given earlier in the same session.
    """
    bone_to_id = {vg.name: vg.index for vg in obj.vertex_groups
                  if not vg.name.startswith("__ssp_")}
    id_to_bone = {vg.index: vg.name for vg in obj.vertex_groups
                  if not vg.name.startswith("__ssp_")}
    synthetic_id = real_vg_count(obj)
    for item in getattr(obj, 'superskin_bones_collection', ()):
        if item.is_orphan and item.name not in bone_to_id:
            bone_to_id[item.name] = synthetic_id
            id_to_bone[synthetic_id] = item.name
            synthetic_id += 1
    return bone_to_id, id_to_bone


def build_mesh_neighbors(mesh) -> dict:
    """Build raw mesh topology map.

    Keeps pure Python types {int: set(int)} for internal decoupling.
    """
    neighbors = {}
    for edge in mesh.edges:
        v0, v1 = edge.vertices
        neighbors.setdefault(v0, set()).add(v1)
        neighbors.setdefault(v1, set()).add(v0)
    return neighbors


def collect_mesh_weights(mesh, vert_indices: set) -> dict:
    """Collect mesh-level weights for *vert_indices*.

    Returns ``{v_idx: {vg_idx: weight}}``.
    """
    result = {}
    for v_idx in vert_indices:
        vw = {}
        for g in mesh.vertices[v_idx].groups:
            vw[g.group] = g.weight
        if vw:
            result[v_idx] = vw
    return result


def get_vertex_coordinates(mesh) -> list:
    """Return ``[(x, y, z), ...]`` for every mesh vertex (local space)."""
    return [(v.co.x, v.co.y, v.co.z) for v in mesh.vertices]


def build_bvh_tree(mesh):
    """Triangulate the mesh and return a BVHTree from its bmesh."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    return bvh
