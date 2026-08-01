"""Weight Brush geometry helpers -- raycast under the cursor and gather the
mesh vertices within a world-space radius of the hit point.

Reuses `weight_apply/logic.py`'s existing radius-bounded BFS
(`_bfs_within_radius`, originally built for Smooth Across Surface) instead
of reimplementing geodesic-distance vertex gathering -- same package, so
this is an ordinary same-package import, not a Zero-Cross-Imports
violation. No `core/` changes and no new Rust code -- this only walks
`CoreFacade.get_cached_mesh_neighbors()` with `CoreFacade.
get_vertex_coordinates()`, both already-exposed read-only Facade methods.

Raycasting goes through a `mathutils.bvhtree.BVHTree` built from the live
edit-mesh BMesh, NOT `obj.ray_cast()`. The latter looked like the obvious
choice (it's the standard object-space raycast) but does not reliably see
an object's live Edit Mode geometry -- confirmed while chasing the "brush
does nothing" bug: `obj.ray_cast()` returned `hit=False` on every single
tick of a real drag across the mesh surface, regardless of cursor
position. `BVHTree.FromBMesh()` built from the SAME `bmesh.from_edit_mesh()`
instance this feature already needs for face->vertex lookups fixes this.
"""

import bmesh
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ..logic import _bfs_within_radius

# Safety backstop on how many topological hops `gather_brush_vertices()`
# will walk outward -- `_bfs_within_radius()` already stops expanding a
# branch once its accumulated edge length passes `radius`, so this only
# matters for a very large radius on very dense topology (bounds worst-case
# BFS cost instead of leaving it fully open-ended).
_BRUSH_MAX_HOPS = 128


def build_bvh(obj):
    """Build a BVHTree from the object's LIVE edit-mesh BMesh -- see this
    module's docstring for why `obj.ray_cast()` doesn't work here instead.
    Returns `(bm, bvh)`; the caller holds both for the whole stroke (one
    build per stroke, not per dab -- this feature never changes vertex
    POSITIONS, only vertex-GROUP weights, so the geometry the BVH indexes
    never goes stale mid-stroke)."""
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bvh = BVHTree.FromBMesh(bm)
    return bm, bvh


def raycast_under_cursor(context, event, obj, bvh):
    """Cast a ray from the viewport camera through the cursor into *bvh*
    (see `build_bvh()`). Returns `(hit, face_index, world_location)` --
    `(False, None, None)` if the cursor isn't over the mesh, or there's no
    3D viewport region under it at all (e.g. the mouse strayed outside the
    viewport mid-drag). `face_index` is a BMesh face index into the same
    `bm` `build_bvh()` returned (BVHTree.FromBMesh preserves `bm.faces`
    order), consumed directly by `gather_brush_vertices()` below.
    """
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return False, None, None

    coord = (event.mouse_region_x, event.mouse_region_y)
    origin_world = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction_world = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    if origin_world is None or direction_world is None:
        return False, None, None

    mat_inv = obj.matrix_world.inverted()
    origin_local = mat_inv @ origin_world
    direction_local = (mat_inv.to_3x3() @ direction_world).normalized()

    location, _normal, face_index, _distance = bvh.ray_cast(origin_local, direction_local)
    if location is None:
        return False, None, None
    return True, face_index, obj.matrix_world @ location


def world_radius_to_screen_px(context, hit_location_world, world_radius):
    """World-space radius to the equivalent screen pixels at
    *hit_location_world*'s depth, purely for sizing `brush_draw.py`'s
    on-screen cursor circle -- `brush_radius` itself is always world-space
    mesh units regardless of `brush_projection` (Screen mode changes which
    vertices a dab can reach, not what the radius number means), so this
    is only ever a display-side conversion, never fed back into vertex
    gathering."""
    region = context.region
    rv3d = context.region_data
    center_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, hit_location_world)
    if center_2d is None:
        return world_radius
    right = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    offset_2d = view3d_utils.location_3d_to_region_2d(
        region, rv3d, hit_location_world + right * world_radius,
    )
    if offset_2d is None:
        return world_radius
    return (offset_2d - center_2d).length


def world_to_screen(context, world_co):
    """2D region coordinates for *world_co*, or `None` if it can't be
    projected (e.g. behind the view). Thin wrapper so callers (`brush_ops.py`,
    `brush_draw.py`) don't each need their own `bpy_extras.view3d_utils`
    import for this one call."""
    return view3d_utils.location_3d_to_region_2d(context.region, context.region_data, world_co)


def gather_brush_vertices(core_facade, bm, face_index, hit_location_world, matrix_world, radius):
    """Vertices within *radius* (world-space, approximate geodesic
    distance) of whichever hit face's own vertex sits closest to
    *hit_location_world*. That nearest face-vertex is the BFS starting
    point for `_bfs_within_radius()` (`weight_apply/logic.py`, the same
    geodesic-radius walk Smooth Across Surface already uses) -- see that
    function's docstring for the walk itself. The starting vertex is added
    explicitly since `_bfs_within_radius()` only returns vertices strictly
    reached by the walk, not its own start point.

    *bm* is the SAME BMesh `build_bvh()` returned -- *not* re-fetched here
    via a second `bmesh.from_edit_mesh()` call, to guarantee `face_index`
    (from the BVH built against this exact `bm`) stays valid.

    Returns `{v_idx: distance}` -- *distance* is straight-line world-space
    distance to *hit_location_world* (0.0 at the start vertex), used by
    `brush_ops.py::_dab()` to bucket vertices into falloff bands. Reachability
    itself is still geodesic (`_bfs_within_radius()`'s hop-by-hop walk); only
    the returned per-vertex distance for banding is straight-line -- cheap,
    and close enough at brush-radius scale.

    *radius* is in world units (matching `brush_radius`'s own meaning), but
    `_bfs_within_radius()` accumulates distance over `core_facade.
    get_vertex_coordinates()`'s LOCAL-space coordinates -- so *radius* is
    converted to an equivalent local-space radius first, via *matrix_world*'s
    scale, or the BFS would reach further or less far than intended whenever
    the object has any scale != 1 (a very common case for rigs that were
    never "Apply Scale"-d), silently desyncing the actual painted footprint
    from the cursor circle drawn from the unconverted world radius. Assumes
    a roughly uniform scale (geometric mean of the three axes) -- BFS
    distances aren't axis-aligned to begin with, so a single scalar
    conversion is the right level of precision here even for mild
    non-uniform scale.
    """
    if face_index is None or face_index < 0 or face_index >= len(bm.faces):
        return {}

    face_verts = bm.faces[face_index].verts
    start_v = min(
        face_verts, key=lambda v: (matrix_world @ v.co - hit_location_world).length_squared,
    ).index

    scale = matrix_world.to_scale()
    avg_scale = (abs(scale.x) * abs(scale.y) * abs(scale.z)) ** (1.0 / 3.0)
    local_radius = radius / avg_scale if avg_scale > 1e-9 else radius

    coords = core_facade.get_vertex_coordinates()
    adjacency = core_facade.get_cached_mesh_neighbors()
    reached = _bfs_within_radius(start_v, coords, adjacency, local_radius, _BRUSH_MAX_HOPS)
    verts = set(reached)
    verts.add(start_v)

    return {
        v_idx: (matrix_world @ Vector(coords[v_idx]) - hit_location_world).length
        for v_idx in verts
    }


def build_vertex_kdtree(core_facade, matrix_world):
    """Build a `mathutils.kdtree.KDTree` of every vertex's WORLD-space
    coordinate, for `gather_brush_vertices_screen()`'s spherical range
    query. World-space (not local) so the query radius is directly
    `brush_radius` with no local/world scale conversion, even if the
    object has non-uniform scale in *matrix_world*. Built once per stroke
    (like `build_bvh()`'s BVHTree), lazily -- only when Screen projection
    is actually used during that stroke (see `brush_ops.py::_get_kdtree()`)
    -- since this feature never changes vertex positions mid-stroke, and
    Surface-projection-only strokes never need it at all."""
    coords = core_facade.get_vertex_coordinates()
    kd = KDTree(len(coords))
    for i, co in enumerate(coords):
        kd.insert(matrix_world @ Vector(co), i)
    kd.balance()
    return kd


def gather_brush_vertices_screen(kdtree, hit_location_world, radius):
    """World-space spherical "pass through" vertex gathering --
    ngSkinTools' "Screen" projection convention: every vertex within
    *radius* world units of *hit_location_world* is included, regardless
    of occlusion, facing direction, or surface connectivity. Distinct from
    `gather_brush_vertices()`'s geodesic-BFS-from-hit-point gathering
    (Surface projection), which only reaches vertices connected along the
    mesh surface and therefore never reaches through to the far side of
    the mesh at all. *radius* is the SAME `brush_radius` value/unit
    Surface projection uses -- Screen mode only changes which vertices
    within that radius are reachable, not what the radius means (unlike an
    earlier revision of this function, which mistakenly treated it as a
    separate screen-pixel size).

    Returns `{v_idx: distance}` -- world-space distance to
    *hit_location_world*, straight from `KDTree.find_range()`, same
    shape/unit as `gather_brush_vertices()`'s own return, so falloff
    banding in `brush_ops.py::_dab()` needs no per-mode branching.
    """
    return {
        v_idx: dist
        for _co, v_idx, dist in kdtree.find_range(hit_location_world, radius)
    }
