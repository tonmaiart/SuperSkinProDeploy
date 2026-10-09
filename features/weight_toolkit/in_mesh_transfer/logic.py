
import numpy as np

from .. import mesh_arrays



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
        if self._mesh_name == mesh_name:
            self._mesh_name = None
            self._indices = None

    def get(self, mesh_name: str):
        if self._indices and self._mesh_name == mesh_name:
            return self._indices
        return None

    def count_for(self, mesh_name: str) -> int:
        indices = self.get(mesh_name)
        return len(indices) if indices else 0


_source_marker = _SourceMarker()



def _closest_surface_point_blend(core_facade, co_flat, target_verts, surface, layer_dict, mask_dict,
                                 mask_default):
    targets = sorted(target_verts)
    points = np.ascontiguousarray(co_flat.reshape(-1, 3)[targets]).reshape(-1)
    names, src_v, src_b, src_w = mesh_arrays.weights_coo(layer_dict)
    mask_v, mask_w = mesh_arrays.mask_arrays(mask_dict)
    found, masks, rows, bones, weights = surface.sample(
        points, src_v, src_b, src_w, mask_v, mask_w, mask_default,
    )
    per_row = mesh_arrays.coo_rows(rows, bones, weights, names)

    weight_map = {}
    mask_map = {}
    for row, v_idx in enumerate(targets):
        if not found[row]:
            continue
        bone_weights = per_row.get(row)
        if bone_weights:
            weight_map[v_idx] = bone_weights
        if masks[row] > 0.0:
            mask_map[v_idx] = masks[row]
    return weight_map, mask_map



def mark_source(core_facade):
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
    if not mesh_name:
        return 0
    return _source_marker.count_for(mesh_name)


def transfer(core_facade) -> int:
    mesh = core_facade.get_mesh()
    source_verts = _source_marker.get(mesh.name)
    if not source_verts:
        raise ValueError(
            "No Source marked on this mesh -- select source vertices and click Mark Source first"
        )

    target_verts = set(core_facade.get_selected_verts())
    if not target_verts:
        raise ValueError("No target vertices selected")

    co_flat = mesh_arrays.local_co(mesh)
    surface = mesh_arrays.surface_sampler(
        core_facade, co_flat, mesh_arrays.loop_triangles(mesh), source_verts,
    )
    if not len(surface):
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
            core_facade, co_flat, target_verts, surface, layer_data, mask_dict, mask_default,
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
