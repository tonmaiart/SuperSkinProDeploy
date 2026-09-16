"""TopologyCacheManager -- shared caches for VG-index mappings and mesh topology.

All methods are classmethods; the class is never instantiated.
Cache dicts are class-level so they persist across operator invocations and
are shared regardless of which code path populates them.

Cache eviction policy:
    Mapping  cache key: (id(mesh_data), vertex_group_count)
    Neighbor cache key: (id(mesh_data), edge_count)
Both keys become stale only when the mesh topology or VG roster actually
changes (rare during a paint session), so the hit rate is very high.
"""

from .proximity_analyzer import ProximityAnalyzer


class TopologyCacheManager:
    """Authority for high-performance zero-allocation Python <-> Rust FFI caching."""

    _mapping_cache: dict = {}
    _neighbor_cache: dict = {}

    @classmethod
    def get_local_mapping(cls, obj, storage) -> tuple[dict[str, int], dict[int, str]]:
        """Return (bone_to_id, id_to_bone) for real vertex groups, using cache.

        Args:
            obj: bpy.types.Object -- the active mesh object.
            storage: LayerStorageService instance for the object's mesh.

        Returns:
            Tuple (bone_name -> vg_index, vg_index -> bone_name).
        """
        vg_count = len(obj.vertex_groups)
        cache_key = (id(obj.data), vg_count)
        cached = cls._mapping_cache.get(cache_key)
        if cached is not None:
            return cached
        result = storage.get_local_mapping(obj)
        cls._mapping_cache[cache_key] = result
        return result

    @classmethod
    def get_cached_mesh_neighbors(cls, mesh, storage) -> dict[int, list[int]]:
        """Return vertex-neighbor topology map in FFI-compatible form, using cache.

        Raw neighbor sets from storage are converted to i32-compatible Python
        lists exactly once. Subsequent calls return the pre-cast result directly,
        eliminating per-call type coercion inside math loops.

        Args:
            mesh: bpy.types.Mesh -- the active mesh datablock.
            storage: LayerStorageService instance for the mesh.

        Returns:
            {vertex_index (int): [neighbor_index (int), ...]}
        """
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
        """Return bone names sorted by proximity for UI panel display.

        Delegates to ProximityAnalyzer.compute_bone_display_order().
        """
        return ProximityAnalyzer.compute_bone_display_order(arm_obj, deform_bone_names)
