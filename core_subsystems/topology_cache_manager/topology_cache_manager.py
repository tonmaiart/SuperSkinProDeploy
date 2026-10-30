
from .proximity_analyzer import ProximityAnalyzer


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
    def get_cached_mesh_neighbors(cls, mesh, storage) -> dict[int, list[int]]:
        edge_count = len(mesh.edges)
        cache_key = (id(mesh), edge_count)
        cached = cls._neighbor_cache.get(cache_key)
        if cached is not None:
            return cached
        raw_neighbors = storage.build_mesh_neighbors()
        ffi_compatible_neighbors = {
            int(v_idx): [int(node) for node in nodes]
            for v_idx, nodes in raw_neighbors.items()
        }
        cls._neighbor_cache[cache_key] = ffi_compatible_neighbors
        return ffi_compatible_neighbors

    @classmethod
    def compute_bone_display_order(cls, arm_obj, deform_bone_names) -> list[str]:
        return ProximityAnalyzer.compute_bone_display_order(arm_obj, deform_bone_names)
