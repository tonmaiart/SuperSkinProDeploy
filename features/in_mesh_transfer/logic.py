"""In-Mesh Transfer logic — closest-surface-point weight/mask blend within a single mesh's
active Layer."""

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc


# ==============================================================================
# Source Marker — in-memory singleton, not persisted, not undo-tracked
# ==============================================================================

class _SourceMarker:
    """Remembers a "Mark Source" vertex selection between two separate operator invocations
    (Mark Source, then."""

    def __init__(self):
        self._mesh_name = None
        self._indices = None

    def mark(self, mesh_name: str, indices: set) -> None:
        self._mesh_name = mesh_name
        self._indices = set(indices)

    def clear(self, mesh_name: str) -> None:
        """Clear the marker if it belongs to *mesh_name* (no-op otherwise)."""
        if self._mesh_name == mesh_name:
            self._mesh_name = None
            self._indices = None

    def get(self, mesh_name: str):
        """Return the marked index set if it belongs to *mesh_name*, else None."""
        if self._indices and self._mesh_name == mesh_name:
            return self._indices
        return None

    def count_for(self, mesh_name: str) -> int:
        indices = self.get(mesh_name)
        return len(indices) if indices else 0


_source_marker = _SourceMarker()


# ==============================================================================
# Pure geometry engine (local-space; source and target share one object)
# ==============================================================================

def _build_restricted_surface(positions, triangles, allowed_verts):
    """BVH built from only the triangles whose all 3 corners are in *allowed_verts*."""
    restricted = [tri for tri in triangles if all(v in allowed_verts for v in tri)]
    if not restricted:
        return None
    bvh = BVHTree.FromPolygons(positions, restricted, all_triangles=True)
    return bvh, restricted, positions


def _closest_surface_point_blend(target_verts, surface, layer_dict, mask_dict, mask_default):
    """For each target vertex, blend weight+mask from the closest point on *surface* (a Source-
    restricted BVH)."""
    bvh, triangles, positions = surface

    weight_map = {}
    mask_map = {}
    for v_idx in target_verts:
        location, _normal, tri_idx, _dist = bvh.find_nearest(positions[v_idx])
        if tri_idx is None:
            continue

        tri = triangles[tri_idx]
        bary = poly_3d_calc([positions[i] for i in tri], location)

        bone_weights = {}
        mask_value = 0.0
        for w, src_idx in zip(bary, tri):
            mask_value += w * mask_dict.get(src_idx, mask_default)
            for bone, bw in layer_dict.get(src_idx, {}).items():
                bone_weights[bone] = bone_weights.get(bone, 0.0) + w * bw

        if mask_value <= 0.0 and not bone_weights:
            continue

        if bone_weights:
            weight_map[v_idx] = bone_weights
        if mask_value > 0.0:
            mask_map[v_idx] = mask_value

    return weight_map, mask_map


# ==============================================================================
# Public API (called from InMeshTransferFeature.execute())
# ==============================================================================

def mark_source(core_facade):
    """Toggle the Source marker for the active mesh."""
    mesh = core_facade.get_mesh()

    if _source_marker.get(mesh.name):
        _source_marker.clear(mesh.name)
        return None

    selected = set(core_facade.get_selected_verts())
    if not selected:
        raise ValueError("No source vertices selected -- select some before clicking Mark Source")

    _source_marker.mark(mesh.name, selected)
    return len(selected)


def marked_source_count_for_mesh(mesh_name) -> int:
    """UI helper: how many vertices are currently marked as Source for *mesh_name*."""
    if not mesh_name:
        return 0
    return _source_marker.count_for(mesh_name)


def transfer(core_facade) -> int:
    """Blend the marked Source region's weight+mask onto the currently selected Target region,
    staying on the SAME active Layer."""
    mesh = core_facade.get_mesh()
    source_verts = _source_marker.get(mesh.name)
    if not source_verts:
        raise ValueError(
            "No Source marked on this mesh -- select source vertices and click Mark Source first"
        )

    target_verts = set(core_facade.get_selected_verts())
    if not target_verts:
        raise ValueError("No target vertices selected")

    mesh.calc_loop_triangles()
    positions = [Vector(v.co) for v in mesh.vertices]
    triangles = [tuple(lt.vertices) for lt in mesh.loop_triangles]

    surface = _build_restricted_surface(positions, triangles, source_verts)
    if surface is None:
        raise ValueError(
            "The marked Source vertices don't form a single face -- "
            "select all 3 vertices of at least one face"
        )

    meta = core_facade.get_meta_list()
    active_idx = core_facade.get_active_layer_index()
    mask_default = 1.0
    for m in meta:
        if m.get("index", -1) == active_idx:
            mask_default = float(m.get("mask_default", 1.0))
            break

    mask_dict = core_facade.get_active_mask_dict()

    with core_facade.mutate_active_layer() as layer_data:
        weight_map, mask_map = _closest_surface_point_blend(
            target_verts, surface, layer_data, mask_dict, mask_default,
        )
        if not weight_map:
            raise ValueError("No weight data in the marked Source area -- nothing to transfer")

        for v_idx, bone_weights in weight_map.items():
            layer_data[v_idx] = bone_weights

    if mask_map:
        for v_idx, mv in mask_map.items():
            mask_dict[v_idx] = mv
        core_facade.write_mask_dict(mask_dict)
        core_facade.finish()

    return len(weight_map)
