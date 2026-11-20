
import numpy as np

from .proximity_analyzer import ProximityAnalyzer


class NeighborMap(dict):
    """``{v_idx: [neighbour, ...]}`` that also holds the whole mesh's topology as CSR int64 arrays
    ``offsets`` (one entry per vertex, plus one) and ``targets``."""

    __slots__ = ("offsets", "targets")


def _edge_ends(mesh):
    ends = np.empty(2 * len(mesh.edges), dtype=np.intc)
    attr = mesh.attributes.get(".edge_verts")
    if len(ends) and attr is not None and attr.data_type == 'INT32_2D' and attr.domain == 'EDGE':
        attr.data.foreach_get("value", ends)
    elif len(ends):
        mesh.edges.foreach_get("vertices", ends)
    return ends


class TopologyCacheManager:
    """Authority for high-performance zero-allocation Python <-> Rust FFI caching."""

    _mapping_cache: dict = {}
    _neighbor_cache: dict = {}

    @classmethod
    def get_local_mapping(cls, obj, storage) -> tuple[dict[str, int], dict[int, str]]:
        vg_count = len(obj.vertex_groups)
        cache_key = (id(obj.data), vg_count, storage.managed_vg_names(obj))
        cached = cls._mapping_cache.get(cache_key)
        if cached is not None:
            return cached
        result = storage.get_local_mapping(obj)
        cls._mapping_cache[cache_key] = result
        return result

    @classmethod
    def get_cached_mesh_neighbors(cls, mesh, storage) -> "NeighborMap":
        counts = (len(mesh.vertices), len(mesh.edges))
        uid = mesh.session_uid
        cached = cls._neighbor_cache.get(uid)
        if cached is not None and cached[1] == counts:
            if cached[0] is mesh:
                return cached[3]
            ends = _edge_ends(mesh)
            if np.array_equal(ends, cached[2]):
                cls._neighbor_cache[uid] = (mesh, counts, cached[2], cached[3])
                return cached[3]
        else:
            ends = _edge_ends(mesh)
        offsets, targets = storage.build_mesh_adjacency_csr()
        rows = np.flatnonzero(np.diff(offsets))
        flat = targets.tolist()
        spans = map(slice, offsets[rows].tolist(), offsets[rows + 1].tolist())
        neighbors = NeighborMap(zip(rows.tolist(), map(flat.__getitem__, spans)))
        neighbors.offsets, neighbors.targets = offsets, targets
        cls._neighbor_cache[uid] = (mesh, counts, ends, neighbors)
        return neighbors

    @classmethod
    def compute_bone_display_order(cls, arm_obj, deform_bone_names) -> list[str]:
        return ProximityAnalyzer.compute_bone_display_order(arm_obj, deform_bone_names)
