"""In-Mesh Transfer logic — closest-surface-point weight/mask blend within a
single mesh's active Layer.

Unlike `features/weight_transfer/` (source = a DIFFERENT mesh object), this
domain transfers FROM one selected region TO another selected region of the
SAME mesh, staying on the SAME active Layer — no new Layer is ever created.
Since source and target share one object/frame, all geometry stays in local
space; no `matrix_world` transform is needed anywhere in this module.

The "closest point on surface + barycentric blend" engine below is a
deliberate, minimal reimplementation of the same algorithm used by
`features/weight_transfer/transfer_core.py` (and independently reimplemented
again by `features/mirror/logic.py`'s own gap-fill fallback, see its module
docstring). It is NOT imported from either sibling package: the project's
"Zero Cross-Imports" rule forbids importing from a sibling `features/*`
package, and `core_subsystems/README.md`'s Import Invariant #3 forbids
feature code from importing a shared `core_subsystems/` module directly
either (only `CoreFacade` is a sanctioned shared entry point, and it cannot
be extended from feature-domain work since `core/` is read-only). Given
those two constraints, duplicating this small, well-understood block per
domain is the accepted, established pattern in this codebase rather than a
shortcut invented here.

Compared to `weight_transfer`'s engine, this one is intentionally simpler:
- Local space only (one object, one frame — no cross-object matrix_world).
- A single restricted BVH serves BOTH weight and mask, unlike
  `weight_transfer`'s deliberate dual-surface split (mask always from the
  full, unrestricted source; weight from a selection-restricted source).
  That split existed there because a separate proxy object is expected to
  be fully painted white — mask should stay "fully covered" regardless of
  which subset of its vertices fed the weight blend. Here there is no
  separate "fully white" source to fall back to: the marked Source region
  IS the layer's own real data, gaps and all, so both channels should
  faithfully reflect only what that specific region actually has painted.
- No `VERTEX_ID` / `MERGE`/`SEPARATE` / insert-method equivalents — this
  domain always does one thing: blend the marked Source into the currently
  selected Target on the currently active Layer.
"""

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc


# ==============================================================================
# Source Marker — in-memory singleton, not persisted, not undo-tracked
# ==============================================================================

class _SourceMarker:
    """Remembers a "Mark Source" vertex selection between two separate
    operator invocations (Mark Source, then Transfer), the same "remember
    something between two operator calls" pattern as
    `features/clipboard/logic.py`'s `ClipboardManager`. Reset on F3 Reload
    Scripts, like any other module-level Python state in this addon.
    """

    def __init__(self):
        self._mesh_name = None
        self._indices = None

    def mark(self, mesh_name: str, indices: set) -> None:
        self._mesh_name = mesh_name
        self._indices = set(indices)

    def get(self, mesh_name: str):
        """Return the marked index set if it belongs to *mesh_name*, else None.

        Scoped by mesh name so switching to a different mesh object doesn't
        silently reuse a stale marker from an unrelated mesh.
        """
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
    """BVH built from only the triangles whose all 3 corners are in *allowed_verts*.

    A genuine sub-surface patch (not a post-hoc per-vertex filter against the
    whole mesh) — see `features/weight_transfer/README.md` rule 18 for why a
    post-hoc filter against an unrestricted BVH produces scattered zero-weight
    holes near the selection boundary instead of a coherent blend.

    Returns `None` if no triangle has all 3 corners selected (e.g. a sparse,
    non-contiguous vertex pick) — the caller must handle this explicitly
    rather than silently falling back to the whole mesh.
    """
    restricted = [tri for tri in triangles if all(v in allowed_verts for v in tri)]
    if not restricted:
        return None
    bvh = BVHTree.FromPolygons(positions, restricted, all_triangles=True)
    return bvh, restricted, positions


def _closest_surface_point_blend(target_verts, surface, layer_dict, mask_dict, mask_default):
    """For each target vertex, blend weight+mask from the closest point on
    *surface* (a Source-restricted BVH), using the active Layer's own
    weight/mask data for that same set of source-side vertices.

    Both channels are read from the identical restricted surface — see this
    module's docstring for why `weight_transfer`'s dual-surface split isn't
    needed here.
    """
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

def mark_source(core_facade) -> int:
    """Snapshot the current selection as the Source region. Returns the count marked."""
    selected = set(core_facade.get_selected_verts())
    if not selected:
        raise ValueError("ยังไม่ได้เลือก Vertex ต้นทาง (Source) กรุณาเลือกก่อนกด Mark Source")

    mesh = core_facade.get_mesh()
    _source_marker.mark(mesh.name, selected)
    return len(selected)


def marked_source_count_for_mesh(mesh_name) -> int:
    """UI helper: how many vertices are currently marked as Source for *mesh_name*."""
    if not mesh_name:
        return 0
    return _source_marker.count_for(mesh_name)


def transfer(core_facade) -> int:
    """Blend the marked Source region's weight+mask onto the currently
    selected Target region, staying on the SAME active Layer. Returns the
    count of target vertices actually written.
    """
    mesh = core_facade.get_mesh()
    source_verts = _source_marker.get(mesh.name)
    if not source_verts:
        raise ValueError(
            "ยังไม่ได้ Mark Source บน mesh นี้ — เลือก Vertex ต้นทางแล้วกด Mark Source ก่อน"
        )

    target_verts = set(core_facade.get_selected_verts())
    if not target_verts:
        raise ValueError("ยังไม่ได้เลือก Vertex ปลายทาง (Target)")

    mesh.calc_loop_triangles()
    positions = [Vector(v.co) for v in mesh.vertices]
    triangles = [tuple(lt.vertices) for lt in mesh.loop_triangles]

    surface = _build_restricted_surface(positions, triangles, source_verts)
    if surface is None:
        raise ValueError(
            "Vertex ที่ Mark Source ไว้ไม่ประกอบกันเป็นหน้า (Face) เดียวเลย "
            "ต้องเลือก Vertex ให้ครบทั้ง 3 จุดของอย่างน้อย 1 หน้า"
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
            raise ValueError("ไม่มีข้อมูล Weight ในบริเวณ Source ที่ Mark ไว้ ไม่มีอะไรให้ Transfer")

        for v_idx, bone_weights in weight_map.items():
            layer_data[v_idx] = bone_weights

    if mask_map:
        for v_idx, mv in mask_map.items():
            mask_dict[v_idx] = mv
        core_facade.write_mask_dict(mask_dict)
        core_facade.finish()

    return len(weight_map)
