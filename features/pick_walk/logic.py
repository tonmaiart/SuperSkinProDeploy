"""Pure computational helpers for Pick Walk selection stepping.

No `bpy.context` / `bpy.ops` calls here beyond `bmesh.update_edit_mesh` (the
standard way to flush a live edit-mesh bmesh back to the mesh) -- everything
else is bmesh element math and 2D screen-space projection, so this is
parametrized away from a live context per the `core_subsystems/` convention,
even though this file lives under `features/pick_walk/`. Reloaded before
`ops.py` / `pick_walk_feature.py` per the Deep Matrix Reload Rule.
"""

import bmesh
from bpy_extras import view3d_utils
from mathutils import Vector


def snap_to_cardinal_direction(delta):
    """Snap a raw 2D screen-space drag vector to the nearest cardinal
    direction -- up, down, left, or right -- instead of using the drag's
    exact angle. Deliberately "dumb": whichever axis has the larger
    magnitude wins, and the other component is discarded entirely, so a
    slightly diagonal drag reads as a clean horizontal or vertical walk
    instead of nudging into an unintended in-between direction."""
    if abs(delta.x) >= abs(delta.y):
        return Vector((1.0, 0.0)) if delta.x >= 0 else Vector((-1.0, 0.0))
    return Vector((0.0, 1.0)) if delta.y >= 0 else Vector((0.0, -1.0))


def vert_neighbors(v):
    """Adjacent vertices via linked edges."""
    return [e.other_vert(v) for e in v.link_edges]


def edge_ring_neighbors(e):
    """Edge-ring neighbors of *e*: for each face touching it, the edge
    opposite *e* in that face (shares no vertex with *e*) -- the "next
    parallel edge" used to walk along an edge loop/ring. Falls back to any
    edge sharing a vertex with *e* when a touching face isn't a quad (no
    single opposite edge) or *e* has no linked face at all (boundary/wire
    edge)."""
    neighbors = []
    for f in e.link_faces:
        opposite = None
        for fe in f.edges:
            if fe is e:
                continue
            if fe.verts[0] not in e.verts and fe.verts[1] not in e.verts:
                opposite = fe
                break
        if opposite is not None:
            neighbors.append(opposite)
        else:
            neighbors.extend(fe for fe in f.edges if fe is not e)
    if not neighbors:
        for v in e.verts:
            neighbors.extend(le for le in v.link_edges if le is not e)
    return neighbors


def face_neighbors(f):
    """Adjacent faces sharing an edge with *f*."""
    neighbors = []
    for e in f.edges:
        neighbors.extend(lf for lf in e.link_faces if lf is not f)
    return neighbors


_NEIGHBOR_FUNCS = {
    'VERT': vert_neighbors,
    'EDGE': edge_ring_neighbors,
    'FACE': face_neighbors,
}


def select_mode_key(mesh_select_mode):
    """Map `tool_settings.mesh_select_mode` (vert, edge, face booleans) to a
    single active mode key, preferring face > edge > vert to match Blender's
    own priority when more than one bit happens to be set."""
    if mesh_select_mode[2]:
        return 'FACE'
    if mesh_select_mode[1]:
        return 'EDGE'
    return 'VERT'


def selected_elements(bm, mode_key):
    if mode_key == 'FACE':
        return [f for f in bm.faces if f.select]
    if mode_key == 'EDGE':
        return [e for e in bm.edges if e.select]
    return [v for v in bm.verts if v.select]


def element_center_local(el):
    """Local-space representative point of a BMVert/BMEdge/BMFace."""
    if isinstance(el, bmesh.types.BMVert):
        return el.co
    if isinstance(el, bmesh.types.BMEdge):
        return (el.verts[0].co + el.verts[1].co) * 0.5
    return el.calc_center_median()


def screen_position(region, rv3d, matrix_world, local_co):
    """Project a local-space point to 2D region coordinates, or None if it
    can't be resolved (e.g. behind the view)."""
    world_co = matrix_world @ local_co
    return view3d_utils.location_3d_to_region_2d(region, rv3d, world_co)


# Minimum cosine similarity a neighbor's screen-space direction must reach
# against the drag direction to count as "continuing the loop" (~72.5
# degrees of tolerance). Below this, the loop is treated as blocked/
# dead-ended in that direction rather than forcing a poor-alignment guess
# just because it happened to be the best of a bad set of candidates.
_MIN_ALIGNMENT = 0.3


def best_aligned_neighbor(region, rv3d, matrix_world, el, neighbors, direction_2d,
                           min_score=_MIN_ALIGNMENT):
    """Return whichever of *neighbors* has a screen-space direction from
    *el* that best matches *direction_2d* (a normalized 2D vector), or None
    if no candidate can be projected onto the screen, or if the best match
    still falls below *min_score* -- i.e. nothing actually continues in
    roughly the drag's direction, so the loop is dead-ended here."""
    if not neighbors:
        return None
    src_2d = screen_position(region, rv3d, matrix_world, element_center_local(el))
    if src_2d is None:
        return None

    best = None
    best_score = -1.0
    for n in neighbors:
        n_2d = screen_position(region, rv3d, matrix_world, element_center_local(n))
        if n_2d is None:
            continue
        vec = n_2d - src_2d
        if vec.length_squared < 1e-9:
            continue
        score = vec.normalized().dot(direction_2d)
        if score > best_score:
            best_score = score
            best = n

    if best_score < min_score:
        return None
    return best


def step_selection(context, obj, bm, mode_key, direction_2d):
    """Shift every currently-selected element to its best-aligned neighbor
    in *direction_2d* (2D, normalized, region space), replacing the whole
    selection at once -- "shift the loop sideways" rather than grow/shrink
    it, matching Maya's arrow-key pick walk on a multi-element selection.

    This is an all-or-nothing step, not a per-element best-effort one: if
    *any* currently-selected element has no neighbor that reasonably
    continues in *direction_2d* (mesh boundary, a pole, or just nothing
    aligned closely enough -- see `_MIN_ALIGNMENT`), the whole step is
    refused and nothing moves. Moving only the elements that *could*
    continue while leaving the rest behind would tear a clean loop
    selection into a partial, jagged shape -- refusing the whole hop keeps
    the loop intact instead, at the cost of the walk simply stopping dead
    the moment it hits a spot it can't cleanly continue through.

    Returns True if the selection actually changed.
    """
    region = context.region
    rv3d = context.region_data
    matrix_world = obj.matrix_world
    neighbor_fn = _NEIGHBOR_FUNCS[mode_key]

    selected = selected_elements(bm, mode_key)
    if not selected:
        return False

    new_selection = set()
    for el in selected:
        best = best_aligned_neighbor(
            region, rv3d, matrix_world, el, neighbor_fn(el), direction_2d,
        )
        if best is None:
            return False
        new_selection.add(best)

    if new_selection == set(selected):
        return False

    for el in selected:
        el.select = False
    for el in new_selection:
        el.select = True
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data)
    return True
