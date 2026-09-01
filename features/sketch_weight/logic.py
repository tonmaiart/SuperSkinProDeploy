"""Sketch Weight Guide -- the inverse-LBS solve engine.

Pure Python + ``mathutils`` only (no ``numpy``/``scipy``), matching this
codebase's existing precedent for domain-local geometry math
(``weight_transfer/transfer_core.py``, ``in_mesh_transfer/logic.py``).

See ``docs/domains/sketch_weight.md`` for the full math writeup and the
deliberate scope decisions (restricted candidate bone set, screen-space
proximity, orphan-bone skip, etc.) -- this module implements exactly that
spec; read it first before changing anything here.
"""

from mathutils import Vector
from bpy_extras import view3d_utils

_MIN_WEIGHT = 1e-4
_REGULARIZATION = 1e-6
# Trust-region cap on the solved weights' actual resulting displacement,
# expressed as a multiple of the displacement that was actually asked for
# (the distance from the vertex's current position to its depth-anchored
# screen target). See "Solve Trust Region" in docs/domains/sketch_weight.md
# for why this replaced an earlier ridge-regression-toward-prior approach
# that proved impossible to tune well (either too weak to stop a runaway
# solve, or -- scaled to the candidate bones' own positional spread -- so
# strong it damped a legitimate small request down to ~11% of what was
# asked). 1.5 allows some natural slack for the LBS solve needing to move
# slightly more than a straight line to actually land on the target (the
# bones' basis points don't form a straight path), while still firmly
# capping the kind of 10x+ runaway that produced a spike-shaped protrusion.
_MAX_OVERSHOOT_RATIO = 1.5


# ==============================================================================
# Armature / skinning-matrix helpers
# ==============================================================================

def get_armature_object(mesh_obj):
    """Return the object of *mesh_obj*'s Armature modifier, or None."""
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object is not None:
            return mod.object
    return None


def _bone_skinning_matrix(mesh_to_arm, arm_to_mesh, armature_obj, bone_name):
    """4x4 matrix mapping a rest-pose, mesh-local vertex coordinate to where
    *bone_name* alone (at weight 1.0) would currently place it, given the
    armature's live pose. Returns None for an orphan bone name (no matching
    ``pose.bones``/``data.bones`` entry) -- see domain doc's "Scope
    Decisions" #4."""
    pose_bone = armature_obj.pose.bones.get(bone_name)
    bone = armature_obj.data.bones.get(bone_name)
    if pose_bone is None or bone is None:
        return None
    skin_in_armature_space = pose_bone.matrix @ bone.matrix_local.inverted()
    return arm_to_mesh @ skin_in_armature_space @ mesh_to_arm


# ==============================================================================
# Screen-space stroke <-> vertex correspondence
# ==============================================================================

def _closest_point_on_segment(p, a, b):
    """Closest point on segment a-b to point p, all 2D tuples.
    Returns (distance, t) with t in [0, 1]."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-9:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / len_sq
        t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    dist = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
    return dist, t


def _closest_point_on_stroke(screen_pos, stroke_samples):
    """Closest point on the full stroke polyline to *screen_pos*, in screen
    space only -- the solve now matches the view-plane projection, not a
    3D world target (see `solve_stroke`'s "Screen Projection Constraint"
    changes), so no 3D interpolation is computed here anymore.

    Returns (min_distance_px, screen_target, end_capped). `end_capped` is
    True when the closest point landed exactly on one of the polyline's own
    two endpoints (t clamped to 0 on the very first segment, or 1 on the
    very last) rather than strictly between them -- i.e. *screen_pos*
    projects past where the stroke actually starts/ends, not genuinely
    alongside it. `solve_stroke` rejects an `end_capped` match outright,
    regardless of `radius_px`: the affected area is meant to be a flat-
    ended ribbon that hugs the drawn stroke exactly (perpendicular
    tolerance only, via `radius_px`), not a stroke dilated in every
    direction including past its own two ends -- the latter reads as a
    circular/spherical blob for a short stroke with a generous radius,
    wildly disproportionate to what was actually drawn. See "Stroke
    proximity" in docs/domains/sketch_weight.md.
    Returns (None, None, False) if the stroke has fewer than 2 samples."""
    best_dist = None
    best_screen = None
    end_capped = False
    last_segment = len(stroke_samples) - 2
    for seg_idx, (a, b) in enumerate(zip(stroke_samples, stroke_samples[1:])):
        dist, t = _closest_point_on_segment(screen_pos, a["screen"], b["screen"])
        if best_dist is None or dist < best_dist:
            best_dist = dist
            ax, ay = a["screen"]
            bx, by = b["screen"]
            best_screen = (ax + t * (bx - ax), ay + t * (by - ay))
            end_capped = (seg_idx == 0 and t <= 0.0) or (seg_idx == last_segment and t >= 1.0)
    return best_dist, best_screen, end_capped


def _is_visible_from_camera(bvh, region, rv3d, screen_pos, world_pos):
    """True if *world_pos* is the nearest surface point along the camera
    ray through *screen_pos* -- i.e. it's actually on the silhouette facing
    the camera, not occluded by nearer geometry (the far side of a limb,
    another body part overlapping it in screen space, etc.).

    Recomputes the ray's origin per call via ``region_2d_to_origin_3d``
    rather than reusing one shared origin, so this is correct for both
    perspective (origin is effectively constant, direction varies) and
    orthographic (origin varies per screen pixel, direction is constant)
    viewports -- the same utility ``ops.py`` already uses for the stroke's
    own raycasts."""
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, screen_pos)
    to_vertex = world_pos - origin
    dist = to_vertex.length
    if dist < 1e-9:
        return True
    direction = to_vertex / dist
    # Small relative back-off so the vertex's own triangle (which sits
    # exactly at `dist`) is never mistaken for an occluder in front of it.
    epsilon = max(1e-4, dist * 1e-3)
    hit, _normal, _index, _hit_dist = bvh.ray_cast(origin, direction, dist - epsilon)
    return hit is None


# Facing ratio above this is treated as squarely camera-facing surface, not
# a silhouette edge -- see `_is_on_silhouette_edge()`. 0.0 == normal exactly
# perpendicular to the view ray (a true grazing edge), 1.0 == normal exactly
# parallel to the view ray (facing straight at or away from the camera).
_SILHOUETTE_FACING_LIMIT = 0.5


def _is_on_silhouette_edge(region, rv3d, screen_pos, world_pos, world_normal):
    """True if *world_pos* sits near the camera-facing silhouette outline --
    i.e. its surface normal is close to perpendicular to the view direction
    -- rather than on a flat, squarely front-facing patch that merely
    happens to project close to the stroke on-screen. This is a strict
    grazing-angle filter, separate from and in addition to
    `_is_visible_from_camera()`'s occlusion test: a vertex can be the
    nearest surface along its own camera ray (unoccluded) and still fail
    this check because it's facing the camera head-on rather than sitting
    on the outline. See docs/domains/sketch_weight.md Scope Decision #2 --
    adding this filter is a deliberate behavior change from the domain's
    original "any visible vertex" design and narrows what the stroke can
    affect to true outline geometry."""
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, screen_pos)
    to_vertex = world_pos - origin
    dist = to_vertex.length
    if dist < 1e-9:
        return False
    view_dir = to_vertex / dist
    facing_ratio = abs(world_normal.dot(view_dir))
    return facing_ratio <= _SILHOUETTE_FACING_LIMIT


# ==============================================================================
# Small linear algebra -- pure Python, small n (typically 2-6 unknowns)
# ==============================================================================

def _gaussian_solve(matrix, rhs):
    """Solve matrix @ x = rhs via Gaussian elimination with partial
    pivoting. Returns None if the system is singular."""
    n = len(rhs)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-10:
            return None
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        aug[col] = [x / pivot for x in aug[col]]
        for r in range(n):
            if r != col:
                factor = aug[r][col]
                aug[r] = [rv - factor * cv for rv, cv in zip(aug[r], aug[col])]
    return [aug[i][n] for i in range(n)]


def _project_to_simplex(values, budget):
    """Euclidean projection of *values* onto {w : w >= 0, sum(w) == budget}.

    Standard O(n log n) sort-based algorithm (Duchi et al., 2008,
    "Efficient Projections onto the l1-Ball for Learning in High
    Dimensions")."""
    n = len(values)
    if budget <= 0.0:
        return [0.0] * n
    sorted_vals = sorted(values, reverse=True)
    running_sum = 0.0
    rho = 0
    theta = 0.0
    for i in range(n):
        running_sum += sorted_vals[i]
        candidate_theta = (running_sum - budget) / (i + 1)
        if sorted_vals[i] - candidate_theta > 0.0:
            rho = i
            theta = candidate_theta
    return [max(0.0, v - theta) for v in values]


def _solve_simplex_lstsq(basis_points, rhs, budget):
    """Solve for weights w (len == len(basis_points)) minimizing
    ``||sum(w_j * basis_points[j]) - rhs||^2`` subject to w >= 0,
    sum(w) == budget. Approximate: unconstrained normal-equations least
    squares (only the tiny fixed `_REGULARIZATION`, enough to avoid an
    exactly-singular matrix, nothing more), projected onto the simplex --
    see domain doc step 5.

    This used to also regularize toward the vertex's current weights
    (a ridge/ridge-to-prior term), added to stop an ill-conditioned system
    from snapping to a single bone's 100%-weight extreme. That approach
    could not be tuned to work in both directions at once: scaled to a
    fixed constant it was too weak to stop the snap; scaled to the
    candidate bones' own positional spread (the fix after that) it also
    correctly stopped the snap, but the SAME strength that does so also
    damps a small, legitimate request nearly to nothing whenever a
    candidate bone's reachable span happens to be naturally large (e.g. a
    stretch-style "growth" bone) -- observed damping a full-strength
    request down to a few percent of what was asked. See "Solve Trust
    Region" in docs/domains/sketch_weight.md -- the actual fix now lives in
    `solve_stroke()`, as a post-hoc cap on how far the RESULT is allowed to
    move relative to what was actually asked for, not as a bias baked into
    this function's own objective."""
    n = len(basis_points)
    if n == 0:
        return []
    if n == 1:
        return [budget]

    ata = [[basis_points[i].dot(basis_points[j]) for j in range(n)] for i in range(n)]
    atb = [basis_points[i].dot(rhs) for i in range(n)]
    for i in range(n):
        ata[i][i] += _REGULARIZATION

    solved = _gaussian_solve(ata, atb)
    if solved is None:
        solved = [budget / n] * n
    return _project_to_simplex(solved, budget)


# ==============================================================================
# Main entry point
# ==============================================================================

def solve_stroke(core_facade, context, stroke_samples, region, rv3d, bvh,
                  radius_px):
    """Solve and write multi-bone weights for every vertex near
    *stroke_samples* (each ``{"screen": (x, y), "world": Vector}``) that is
    actually part of the camera-facing silhouette outline at its own screen
    position -- *bvh* (the same world-space BVH ``ops.py`` built for the
    stroke's own raycasts) is used to reject a candidate vertex that's
    occluded by nearer geometry (see ``_is_visible_from_camera``), and its
    surface normal is used to reject one that's squarely camera-facing
    rather than on a grazing outline edge (see ``_is_on_silhouette_edge``).
    The per-vertex solve targets a 3D point unprojected from the stroke's
    on-screen position at the VERTEX'S OWN current depth, not the stroke's
    own (heuristically guessed) depth -- see the "Depth-Anchored Screen
    Target" comment below for why neither a raw 3D target nor a pure 2D
    target work as well as this.

    Returns the number of vertices actually modified. Raises ValueError for
    conditions the operator should report and abort on (no Armature
    modifier, evaluated/rest vertex-count mismatch) -- never silently
    guesses, matching ``in_mesh_transfer``'s abort-cleanly convention.
    """
    if len(stroke_samples) < 2:
        raise ValueError("Guide stroke too short.")

    obj = core_facade.get_obj()
    is_mask_mode = core_facade.is_mask_context()
    armature_obj = get_armature_object(obj)
    if not is_mask_mode and armature_obj is None:
        raise ValueError("Active mesh has no Armature modifier to solve weights against.")

    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()
    try:
        world_positions = [obj.matrix_world @ v.co for v in mesh_eval.vertices]
        normal_matrix = obj.matrix_world.inverted().transposed().to_3x3()
        world_normals = [(normal_matrix @ v.normal).normalized() for v in mesh_eval.vertices]
    finally:
        obj_eval.to_mesh_clear()

    num_verts = core_facade.get_num_verts()
    if len(world_positions) != num_verts:
        raise ValueError(
            "Evaluated mesh vertex count does not match the active layer topology."
        )

    # Auto-scope: an existing selection narrows the candidate set to just
    # those vertices (the common "I selected the area I mean to reshape"
    # case); with nothing selected, every vertex is a candidate instead --
    # guide_radius (below) is what keeps that whole-mesh case from touching
    # unrelated geometry. There used to be a manual "Selected Vertices
    # Only" checkbox controlling this; it was removed because the
    # selection state itself already says which behavior is wanted, making
    # a separate toggle redundant (and a source of "I forgot to check/
    # uncheck it" mistakes). Shared by both the weight-solve path below and
    # the mask-paint path (see is_mask_mode branch).
    selected_verts = core_facade.get_selected_verts()
    candidate_verts = selected_verts if selected_verts else range(num_verts)

    # guide_radius is enforced unconditionally, regardless of whether the
    # candidate set above came from a selection or the whole mesh -- see
    # "Stroke proximity" in docs/domains/sketch_weight.md for why an
    # earlier version that bypassed this for a selection was a bug: a short
    # stroke drawn near one end of a wide selection (e.g. an ear tip) needs
    # a proximity gate just as much as the unrestricted case does, or
    # selected-but-unrelated vertices elsewhere in the selection get
    # dragged toward the stroke instead of being left alone. Shared by both
    # paths below.
    xs = [s["screen"][0] for s in stroke_samples]
    ys = [s["screen"][1] for s in stroke_samples]
    min_x, max_x = min(xs) - radius_px, max(xs) + radius_px
    min_y, max_y = min(ys) - radius_px, max(ys) + radius_px

    if is_mask_mode:
        # Mask is a per-vertex coverage scalar, not a position -- there is
        # nothing for an inverse-LBS solve to target. Instead, every
        # candidate vertex that passes the same stroke-proximity/
        # visibility/silhouette filters the weight path uses below is
        # painted to full coverage (1.0), replacing whatever mask value it
        # already had. See "Mask Mode" in docs/domains/sketch_weight.md.
        return _paint_mask_stroke(
            core_facade, stroke_samples, region, rv3d, bvh, radius_px,
            world_positions, world_normals, candidate_verts,
            min_x, max_x, min_y, max_y,
        )

    rest_coords = core_facade.get_vertex_coordinates()
    locks = core_facade.get_bone_locks()
    mat_world_inv = obj.matrix_world.inverted()
    mesh_to_arm = armature_obj.matrix_world.inverted() @ obj.matrix_world
    arm_to_mesh = mesh_to_arm.inverted()

    bone_matrix_cache = {}

    def get_bone_matrix(bone_name):
        if bone_name not in bone_matrix_cache:
            bone_matrix_cache[bone_name] = _bone_skinning_matrix(
                mesh_to_arm, arm_to_mesh, armature_obj, bone_name
            )
        return bone_matrix_cache[bone_name]

    touched = 0
    with core_facade.mutate_active_layer() as layer_data:
        for v_idx in candidate_verts:
            entry = layer_data.get(v_idx)
            if not entry:
                continue

            world_pos = world_positions[v_idx]
            # Stroke-proximity ribbon + camera-occlusion + silhouette-edge
            # gate -- same filter the mask-paint path uses, see
            # _passes_stroke_filters()'s docstring and "Stroke proximity" /
            # Scope Decision #2 in docs/domains/sketch_weight.md.
            target_screen = _passes_stroke_filters(
                v_idx, world_positions, world_normals, region, rv3d, bvh,
                stroke_samples, radius_px, min_x, max_x, min_y, max_y,
            )
            if target_screen is None:
                continue

            # Orphan-bone guard -- skip this vertex entirely rather than
            # guessing a fallback matrix (Scope Decisions #4).
            if any(get_bone_matrix(bn) is None for bn in entry):
                continue

            unlocked_bones = [bn for bn in entry if not locks.get(bn, False)]
            if not unlocked_bones:
                continue

            locked_weight_sum = sum(w for bn, w in entry.items() if locks.get(bn, False))
            budget = max(0.0, 1.0 - locked_weight_sum)
            if budget <= 1e-6:
                continue

            # Depth-Anchored Screen Target -- the 2D stroke target is
            # unprojected back into 3D at the VERTEX'S OWN current depth
            # (`region_2d_to_location_3d()`'s `depth_location` argument),
            # not the stroke's own guessed depth. This is deliberately
            # different from both of this domain's earlier solve designs:
            # targeting the stroke's raw 3D position (the original design)
            # forced the solve to chase a heuristic depth guess that had
            # nothing to do with this particular vertex, reading as
            # unrelated bulging; targeting the 2D screen position alone
            # (this session's first revision) removed depth from the
            # objective entirely, letting a long-lever-arm bone (e.g. a
            # jaw/beak hinge far from the tip) swing the vertex an enormous
            # 3D distance while still projecting correctly on-screen,
            # reading as a runaway spike. Anchoring the target's depth to
            # the vertex's own current position keeps the solve a normal
            # bounded 3D least-squares (still a convex combination of the
            # candidate bones' full-weight 3D positions, so it can never
            # exceed what 100% weight on the single most extreme bone would
            # already produce) while still driving the vertex toward the
            # stroke's on-screen position rather than an arbitrary guessed
            # depth. See "Screen Projection Constraint" in
            # docs/domains/sketch_weight.md for the full writeup.
            target_world = view3d_utils.region_2d_to_location_3d(
                region, rv3d, target_screen, world_pos
            )

            v_rest_local = Vector(rest_coords[v_idx])
            target_local = mat_world_inv @ target_world

            locked_offset = Vector((0.0, 0.0, 0.0))
            for bn, w in entry.items():
                if locks.get(bn, False):
                    locked_offset += w * (get_bone_matrix(bn) @ v_rest_local.to_4d()).to_3d()

            rhs = target_local - locked_offset
            basis_points = [
                (get_bone_matrix(bn) @ v_rest_local.to_4d()).to_3d() for bn in unlocked_bones
            ]
            prior_weights = [entry.get(bn, 0.0) for bn in unlocked_bones]

            raw_weights = _solve_simplex_lstsq(basis_points, rhs, budget)

            # Solve Trust Region -- cap how far the RAW solve is allowed to
            # move the vertex relative to what was actually asked for
            # (`asked_dist`, current-to-target distance), rather than
            # biasing the least-squares objective itself toward the prior
            # weights (see `_solve_simplex_lstsq`'s docstring for why that
            # approach was replaced). Since position is an affine function
            # of the weights for a fixed candidate bone set, linearly
            # blending `raw_weights` toward `prior_weights` by `t` linearly
            # blends the resulting position by the same `t` -- so solving
            # for the `t` that brings `actual_dist` down to exactly
            # `_MAX_OVERSHOOT_RATIO * asked_dist` is a direct, closed-form
            # scalar computation, not another optimization. See "Solve
            # Trust Region" in docs/domains/sketch_weight.md.
            v_current_local = mat_world_inv @ world_pos
            asked_dist = (target_local - v_current_local).length
            raw_local = locked_offset + sum(
                (w * bp for w, bp in zip(raw_weights, basis_points)), Vector((0.0, 0.0, 0.0))
            )
            raw_actual_dist = (raw_local - v_current_local).length
            max_allowed_dist = _MAX_OVERSHOOT_RATIO * asked_dist
            if raw_actual_dist > max_allowed_dist > 0.0:
                t = max_allowed_dist / raw_actual_dist
                new_weights = [
                    p + t * (w - p) for w, p in zip(raw_weights, prior_weights)
                ]
            else:
                t = 1.0
                new_weights = raw_weights

            new_entry = {bn: w for bn, w in entry.items() if locks.get(bn, False)}
            for bn, w in zip(unlocked_bones, new_weights):
                if w > _MIN_WEIGHT:
                    new_entry[bn] = w
            layer_data[v_idx] = new_entry
            touched += 1

    return touched


# ==============================================================================
# Mask mode
# ==============================================================================

def _passes_stroke_filters(v_idx, world_positions, world_normals, region, rv3d,
                            bvh, stroke_samples, radius_px, min_x, max_x, min_y, max_y):
    """Shared candidate-vertex gate for both the weight-solve path and the
    mask-paint path: stroke-proximity ribbon (guide_radius + hard end-cap
    reject), camera-occlusion, and silhouette-edge facing-ratio checks --
    see "Stroke proximity" and Scope Decision #2 in
    docs/domains/sketch_weight.md. Returns the stroke's closest on-screen
    point (needed by the weight path to build its depth-anchored target) if
    *v_idx* passes every filter, or None if it's rejected by any of them."""
    world_pos = world_positions[v_idx]
    screen = view3d_utils.location_3d_to_region_2d(region, rv3d, world_pos)
    if screen is None:
        return None
    sx, sy = screen
    if sx < min_x or sx > max_x or sy < min_y or sy > max_y:
        return None

    dist, target_screen, end_capped = _closest_point_on_stroke((sx, sy), stroke_samples)
    if dist is None or dist > radius_px or end_capped:
        return None

    if not _is_visible_from_camera(bvh, region, rv3d, (sx, sy), world_pos):
        return None
    if not _is_on_silhouette_edge(region, rv3d, (sx, sy), world_pos, world_normals[v_idx]):
        return None

    return target_screen


def _paint_mask_stroke(core_facade, stroke_samples, region, rv3d, bvh, radius_px,
                        world_positions, world_normals, candidate_verts,
                        min_x, max_x, min_y, max_y):
    """Mask-mode counterpart to the weight-solve loop in `solve_stroke()`.
    A mask value is a coverage scalar, not a position -- there is nothing
    for the inverse-LBS solve to target -- so every candidate vertex that
    passes `_passes_stroke_filters()` is simply painted to full coverage
    (1.0), replacing its previous mask value outright (no falloff, no
    additive blend). See "Mask Mode" in docs/domains/sketch_weight.md.

    Returns the number of vertices painted."""
    mask_dict = core_facade.get_active_mask_dict()
    touched = 0
    for v_idx in candidate_verts:
        target_screen = _passes_stroke_filters(
            v_idx, world_positions, world_normals, region, rv3d, bvh,
            stroke_samples, radius_px, min_x, max_x, min_y, max_y,
        )
        if target_screen is None:
            continue
        mask_dict[v_idx] = 1.0
        touched += 1

    if touched:
        core_facade.write_mask_dict(mask_dict)
        # write_mask_dict() does not call finish() itself -- explicit
        # follow-up required, mirroring the mask-only path in
        # features/mirror/logic.py::_mirror_active_layer(). color_only is
        # correct here since a mask paint never touches topology or weight
        # data.
        core_facade.finish_color_only()

    return touched
