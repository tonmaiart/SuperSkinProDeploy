"""Weight Brush geometry helpers -- raycast under the cursor and gather the
mesh vertices within a world-space radius of the hit point.

`_bfs_within_world_radius()` below is a self-contained, brush-only
radius-bounded BFS that walks in true WORLD-space distance -- see its own
docstring for why it does not reuse `weight_apply/logic.py`'s local-space
`_bfs_within_radius()` (that function no longer exists; an earlier
revision of this module converted *radius* to an approximate local-space
equivalent via a single scale scalar instead, which broke down on any
object with matrix_world scale != 1). No `core/` changes and no new Rust
code -- this only walks `CoreFacade.get_cached_mesh_neighbors()` (pure
topology, pose-independent) alongside this module's own
`get_posed_vertex_coordinates()` (see below).

Raycasting goes through a `mathutils.bvhtree.BVHTree`, NOT `obj.ray_cast()`.
The latter looked like the obvious choice (it's the standard object-space
raycast) but does not reliably see an object's live Edit Mode geometry --
confirmed while chasing the "brush does nothing" bug: `obj.ray_cast()`
returned `hit=False` on every single tick of a real drag across the mesh
surface, regardless of cursor position.

**Detects against the CURRENT POSE, not the bind/rest pose.** Edit Mode's
own BMesh (`bmesh.from_edit_mesh()`) always stores the rest/bind-pose
vertex positions -- never the Armature modifier's deformed output -- so a
BVH built straight from it (an earlier revision of this module did
exactly that, `BVHTree.FromBMesh(bm)`) only lines up with what's drawn on
screen when the armature happens to be sitting in its rest pose. Any
workflow that poses the character before weight-painting (an extremely
common one -- pose into a problem position, then paint-fix what's wrong)
made the brush's raycast/gather hit the invisible rest-pose surface
instead, offset from the actual on-screen cursor position by however far
the pose has moved from rest. `build_bvh()` now builds the BVH from
`get_posed_vertex_coordinates()`'s deformed positions instead, paired with
the SAME `bm.faces` topology (via `BVHTree.FromPolygons()`, not
`FromObject()` -- guarantees `face_index` still indexes directly into
`bm.faces`, no separate-ordering risk from a second, independently-built
evaluated mesh) -- so a raycast hits exactly the surface the user sees,
posed or not. The BFS distance walk (`_bfs_within_world_radius()`) and the
Screen-projection KDTree (`build_screen_kdtree()`) both take the same
posed coordinate array too, so radius/falloff distances and Screen mode's
on-screen vertex selection stay consistent with the posed hit point
instead of measuring against the (now-mismatched) rest pose.
"""

from collections import deque

import bmesh
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

# Safety backstop on how many topological hops `gather_brush_vertices()`
# will walk outward -- `_bfs_within_world_radius()` already stops expanding
# a branch once its accumulated WORLD-space edge length passes `radius`, so
# this only matters for a very large radius on very dense topology (bounds
# worst-case BFS cost instead of leaving it fully open-ended).
_BRUSH_MAX_HOPS = 128

# Per explicit request: Screen projection's brush size must stay a CONSTANT
# on-screen pixel size regardless of zoom/distance, while Surface
# projection's brush is drawn as real 3D geometry (`brush_draw.py`'s
# `show_surface()` -- a world-space disc, no pixel conversion involved at
# all, so its on-screen size naturally changes with zoom exactly like any
# other object in the scene). `SSPrefWeightBrush.brush_radius` stays a
# single 0.0-1.0 slider shared by both modes (see brush_ops.py) -- Screen
# mode just reinterprets that same 0.0-1.0 value as a fraction of this
# fixed pixel scale instead of a world-space length, via
# `screen_mode_radius_px()` below, so there's no separate "Screen Radius"
# property to keep in sync.
SCREEN_RADIUS_PX_SCALE = 500.0


def screen_mode_radius_px(brush_radius: float) -> float:
    """Screen projection's brush_radius -> on-screen pixels, independent of
    view zoom/distance/depth -- see `SCREEN_RADIUS_PX_SCALE` above."""
    return brush_radius * SCREEN_RADIUS_PX_SCALE


def _bfs_within_world_radius(v_idx, coords, adjacency, matrix_world, radius, max_hops):
    """Walk the 1-ring adjacency graph, accumulating TRUE world-space edge
    length (each visited vertex's local coordinate is transformed through
    `matrix_world` on demand and cached) as an approximate geodesic
    distance, collecting every vertex reachable within world-space *radius*.

    Earlier revision of this walk ran over raw LOCAL-space coordinates and
    converted *radius* to an equivalent local-space radius via a single
    `matrix_world.to_scale()`-derived scalar (geometric mean of the three
    axis scales). That approximation breaks down hard on any object whose
    matrix_world scale isn't ~1 -- e.g. an imported rig left at
    `scale=(0.01, 0.01, 0.01)` and never "Apply Scale"-d, which is common in
    production, not an edge case: the converted local radius comes out ~100x
    too large, and since `max_hops` (not the radius check) ends up being the
    only thing actually bounding the walk, a small on-screen brush radius
    paints far more of the mesh than its own drawn circle shows. Walking in
    true world space removes the whole class of scale-approximation error,
    at the cost of one `matrix_world @ Vector(...)` transform per VISITED
    vertex (cached, computed once each) -- cheap, since a radius-bounded
    walk only ever visits a footprint proportional to the brush, never the
    whole mesh, same order of cost this function already paid per vertex in
    its final returned-distance dict.

    Returns a plain `[neighbor_idx, ...]` list, same shape
    `gather_brush_vertices()` expects.
    """
    world_cache = {}

    def world_co(i):
        co = world_cache.get(i)
        if co is None:
            co = matrix_world @ Vector(coords[i])
            world_cache[i] = co
        return co

    start_world = world_co(v_idx)
    visited = {v_idx: 0.0}
    queue = deque([(v_idx, 0.0, 0)])
    result = []
    while queue:
        cur, dist, hops = queue.popleft()
        if cur != v_idx:
            result.append(cur)
        if hops >= max_hops:
            continue
        cur_world = world_co(cur)
        for nb in adjacency.get(cur, ()):
            edge_len = (world_co(nb) - cur_world).length
            new_dist = dist + edge_len
            if new_dist > radius:
                continue
            if nb in visited and visited[nb] <= new_dist:
                continue
            visited[nb] = new_dist
            queue.append((nb, new_dist, hops + 1))
    return result


def get_posed_vertex_coordinates(context, obj):
    """Per-vertex LOCAL-space coordinates reflecting the object's CURRENT
    pose -- the live depsgraph-evaluated (Armature-modifier-deformed)
    output -- instead of the rest/bind-pose geometry `obj.data.vertices`/
    the edit-mesh BMesh always stores. Same v_idx indexing as the bind-pose
    array, so a caller can swap one for the other with no other change.

    Evaluates via `context.evaluated_depsgraph_get()` +
    `obj.evaluated_get(depsgraph).to_mesh()` (freed immediately via
    `to_mesh_clear()` -- this is a real, if temporary, full mesh copy, not
    a view). Falls back to the bind-pose array
    (`[v.co for v in obj.data.vertices]`) if evaluation fails for any
    reason, or if the evaluated mesh's vertex count doesn't match --
    the same limitation Blender's own native Weight Paint mode already has
    under a modifier stack: index correspondence with the edit-mesh only
    holds when nothing GENERATIVE (Mirror, Subsurf, Array, Bevel, Boolean,
    Decimate, Skin, Remesh, ...) sits between the edit-mesh and whatever
    deforms it -- those change vertex count/order, at which point there is
    no correct per-vertex correspondence to fall back to; a pure DEFORM
    modifier (Armature, Lattice, Cast, Hook, Simple Deform, Shrinkwrap in
    a topology-preserving mode, Cloth, Corrective Smooth, ...) never
    changes vertex count/order, so this holds for exactly the "posed
    character" case this function exists for.
    """
    coords = None
    try:
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        eval_mesh = eval_obj.to_mesh()
        try:
            coords = [tuple(v.co) for v in eval_mesh.vertices]
        finally:
            eval_obj.to_mesh_clear()
    except Exception:
        coords = None

    if coords is None or len(coords) != len(obj.data.vertices):
        return [tuple(v.co) for v in obj.data.vertices]
    return coords


def build_bvh(context, obj):
    """Build a BVHTree over the object's CURRENT POSE
    (`get_posed_vertex_coordinates()`) but the live edit-mesh's own face
    TOPOLOGY (`bmesh.from_edit_mesh()`) -- see this module's docstring for
    why raycasting against the posed surface (not the invisible bind-pose
    one) is the whole point, and for why `BVHTree.FromPolygons()` (not
    `FromObject()`) is what keeps `face_index` indexing directly into the
    SAME `bm.faces` this function returns.

    Returns `(bm, bvh, posed_coords)` -- the caller holds all three for the
    whole stroke (one build per stroke, not per dab -- this feature never
    changes vertex POSITIONS or re-poses the armature mid-stroke, only
    vertex-GROUP weights, so neither the topology nor the pose goes stale
    mid-stroke). `posed_coords` is returned alongside so callers
    (`gather_brush_vertices()`, `build_screen_kdtree()`) reuse the exact
    same evaluated array instead of re-running the depsgraph evaluation a
    second/third time for the same stroke."""
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    posed_coords = get_posed_vertex_coordinates(context, obj)
    polygons = [[v.index for v in f.verts] for f in bm.faces]
    bvh = BVHTree.FromPolygons(posed_coords, polygons)
    return bm, bvh, posed_coords


def raycast_under_cursor(context, event, obj, bvh):
    """Cast a ray from the viewport camera through the cursor into *bvh*
    (see `build_bvh()`). Returns `(hit, face_index, world_location,
    world_normal)` -- `(False, None, None, None)` if the cursor isn't over
    the mesh, or there's no 3D viewport region under it at all (e.g. the
    mouse strayed outside the viewport mid-drag). `face_index` is a BMesh
    face index into the same `bm` `build_bvh()` returned (the polygon list
    `BVHTree.FromPolygons()` was built from is `bm.faces` in order, so
    indices line up 1:1), consumed directly by `gather_brush_vertices()`
    below. `world_normal` is a normalized
    world-space surface normal at the hit point -- used by
    `brush_hover.py`/`brush_ops.py` to orient the Surface-projection cursor
    disc to the surface it's hovering (see `brush_draw.py::show_surface()`).
    """
    region = context.region
    rv3d = context.region_data
    if region is None or rv3d is None:
        return False, None, None, None

    coord = (event.mouse_region_x, event.mouse_region_y)
    origin_world = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction_world = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    if origin_world is None or direction_world is None:
        return False, None, None, None

    mat_inv = obj.matrix_world.inverted()
    origin_local = mat_inv @ origin_world
    direction_local = (mat_inv.to_3x3() @ direction_world).normalized()

    location, normal_local, face_index, _distance = bvh.ray_cast(origin_local, direction_local)
    if location is None:
        return False, None, None, None

    # Correct normal transform under non-uniform scale is the inverse-
    # transpose of the linear (3x3) part of matrix_world, not matrix_world
    # itself -- a plain `matrix_world @ normal_local` would tilt the normal
    # whenever the object's scale isn't uniform across axes.
    normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
    world_normal = (normal_matrix @ normal_local).normalized()

    return True, face_index, obj.matrix_world @ location, world_normal


def gather_brush_vertices(core_facade, bm, face_index, hit_location_world, matrix_world, radius, posed_coords):
    """Vertices within *radius* (true world-space geodesic distance) of
    whichever hit face's own vertex sits closest to *hit_location_world*.
    That nearest face-vertex is the BFS starting point for
    `_bfs_within_world_radius()` above -- see that function's docstring for
    the walk itself, and for why it walks in world space directly instead
    of converting *radius* to an approximate local-space equivalent. The
    starting vertex is added explicitly since `_bfs_within_world_radius()`
    only returns vertices strictly reached by the walk, not its own start
    point.

    *bm* is the SAME BMesh `build_bvh()` returned -- *not* re-fetched here
    via a second `bmesh.from_edit_mesh()` call, to guarantee `face_index`
    (from the BVH built against this exact `bm`) stays valid. *posed_coords*
    is likewise `build_bvh()`'s own returned array -- reused here rather
    than re-evaluated, and used in place of a bind-pose vertex-coordinate
    read so this walk's distances stay consistent with *hit_location_world*
    (posed-space, from a BVH built over the same *posed_coords* -- see this
    module's docstring) instead of measuring against the mismatched rest
    pose.

    Returns `{v_idx: distance}` -- *distance* is straight-line world-space
    distance to *hit_location_world* (0.0 at the start vertex), used by
    `brush_ops.py::_dab()` to bucket vertices into falloff bands. Reachability
    itself is still geodesic (`_bfs_within_world_radius()`'s hop-by-hop
    walk); only the returned per-vertex distance for banding is
    straight-line -- cheap, and close enough at brush-radius scale.
    """
    if face_index is None or face_index < 0 or face_index >= len(bm.faces):
        return {}

    face_verts = bm.faces[face_index].verts
    start_v = min(
        face_verts,
        key=lambda v: (matrix_world @ Vector(posed_coords[v.index]) - hit_location_world).length_squared,
    ).index

    adjacency = core_facade.get_cached_mesh_neighbors()
    reached = _bfs_within_world_radius(
        start_v, posed_coords, adjacency, matrix_world, radius, _BRUSH_MAX_HOPS,
    )
    verts = set(reached)
    verts.add(start_v)

    return {
        v_idx: (matrix_world @ Vector(posed_coords[v_idx]) - hit_location_world).length
        for v_idx in verts
    }


def build_screen_kdtree(matrix_world, region, rv3d, posed_coords):
    """Build a `mathutils.kdtree.KDTree` of every vertex's 2D SCREEN-space
    projection (region-relative pixels, Z padded to 0.0), for
    `gather_brush_vertices_screen()`'s literal screen-space range query.

    This is Screen projection's actual, literal meaning (an earlier
    revision built a WORLD-space sphere instead -- see this function's own
    "not a sphere" correction below): project straight through the mesh
    along the current view, like an infinite cylinder extruded from the
    on-screen brush circle -- membership depends ONLY on a vertex's 2D
    screen position relative to the circle, never on its depth/distance
    from the hit point along the view axis. ngSkinTools' own "Screen"
    projection works the same way.

    A vertex that doesn't project at all (behind the camera --
    `view3d_utils.location_3d_to_region_2d()` returns `None`) is skipped
    entirely; it has no screen position to test against the circle.

    *posed_coords* is `build_bvh()`'s own returned array (same POSED,
    not bind-pose, positions as Surface projection's BVH -- see this
    module's docstring) so Screen mode's on-screen vertex selection
    matches where vertices actually appear when the character is posed,
    not their rest-pose screen position.

    Built once per stroke, lazily (see `brush_ops.py::_get_kdtree()`) --
    same reasoning as the old world-space KDTree this replaces: cheap
    enough to build once and query every dab, assuming the view doesn't
    change mid-stroke (LMB is held by the brush stroke itself, so an
    orbit/pan/zoom mid-drag is not the normal case -- if it does happen,
    the circle only goes stale until the next stroke, same tradeoff the
    Surface-mode BVH already makes for vertex positions)."""
    kd = KDTree(len(posed_coords))
    for i, co in enumerate(posed_coords):
        world_co = matrix_world @ Vector(co)
        co_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, world_co)
        if co_2d is None:
            continue
        kd.insert((co_2d.x, co_2d.y, 0.0), i)
    kd.balance()
    return kd


def gather_brush_vertices_screen(kdtree_2d, center_2d, radius_px, world_radius):
    """Screen-space "pass through" vertex gathering -- ngSkinTools' "Screen"
    projection convention: every vertex whose 2D screen projection falls
    within *radius_px* pixels of the brush's on-screen center is included,
    regardless of depth, occlusion, facing direction, or surface
    connectivity -- a straight/linear projection (an infinite cylinder
    along the view ray from the on-screen circle), NOT a 3D sphere around
    the hit point (an earlier revision of this function built exactly that
    sphere, which is why a small `brush_radius` still painted the whole
    mesh: a sphere large enough to contain the object at all reaches
    through it regardless of the on-screen circle size, but a screen-space
    circle's on-screen SIZE is exactly what the drawn cursor already shows,
    so what you see is what gets painted). Distinct from
    `gather_brush_vertices()`'s geodesic-BFS-from-hit-point gathering
    (Surface projection), which only reaches vertices connected along the
    mesh surface.

    *kdtree_2d* is `build_screen_kdtree()`'s return -- 2D screen-space
    points (Z=0). *center_2d* is the brush's on-screen center (a 2-tuple
    or `Vector`, X/Y only -- padded to Z=0 for the query). *radius_px* is
    the SAME on-screen pixel radius `brush_draw.py`'s circle is drawn at
    (`screen_mode_radius_px()`), so the query boundary is identical to what
    the user sees drawn, by construction.

    Returns `{v_idx: distance}` -- rescaled from the raw pixel distance
    back into *world_radius*-equivalent units
    (`(pixel_dist / radius_px) * world_radius`) so `brush_ops.py::_dab()`'s
    `dist / brush_radius` falloff-banding math needs no per-mode branching,
    identical shape/unit contract to `gather_brush_vertices()`'s own
    return.
    """
    if radius_px <= 1e-9:
        return {}
    cx, cy = center_2d[0], center_2d[1]
    return {
        v_idx: (dist_px / radius_px) * world_radius
        for _co, v_idx, dist_px in kdtree_2d.find_range((cx, cy, 0.0), radius_px)
    }
