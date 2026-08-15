"""Mirror-weight logic — Rust Accelerated Multi-OS Portal.

Combines name-pair generation with the layer-aware mirror operation.

⚡ LOCAL ID MAPPING:
- ``generate_pairs`` remains string-based (pattern matching needs bone names)
- ``apply`` and ``apply_mask`` use Integer Bone IDs for inner layer_dict keys

⚡ WEIGHT vs. MASK CHANNEL — DIFFERENT SIDE-CLASSIFICATION STRATEGIES:
- **Weight channel (``apply``)** uses ``target_side_mask`` — a per-vertex
  bool list from ``classify_sides_by_topology()``, NOT a raw coordinate-sign
  test. When the mesh visually intersects across the mirror plane (e.g. an
  oversized pant leg poking into the other leg), a vertex that's
  topologically part of one side can have a coordinate that dips onto the
  other — confirmed by a user report where the mirrored weight data itself
  (not just its rendering) was wrong right in the overlap region. It also
  gets a gap-fill fallback (``apply`` returns ``(result, gap_v_indices)``;
  ``_fill_weight_gaps`` covers the target-side vertices the Rust
  nearest-vertex pass couldn't resolve within tolerance, via the same
  "closest point on surface + barycentric blend" approach as
  ``features/weight_transfer/transfer_core.py``). Separately,
  ``_symmetrize_center_vertices`` forces a true 50/50 split on vertices
  sitting exactly on the mirror seam (see its own docstring) — a small,
  fixed-epsilon check kept deliberately independent of
  ``classify_sides_by_topology()``'s wide margin, so it can never affect
  self-intersection handling.
- **Mask channel (``apply_mask_flat``)** deliberately stays on the ORIGINAL
  raw coordinate-sign test, with no gap-fill — the topology classification
  was confirmed to badly misclassify most of a complex character mesh's
  mask coverage (only a small, simple region came out right; gap-fill was
  silently papering over the rest of the mesh). Reverted per explicit user
  request after that regression; the weight channel's fix stays since it
  was independently confirmed to work well.
"""

import heapq

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from ...core.facade import CoreFacade


def get_bone_centers(core_facade):
    """Compute world-space bone centre positions keyed by vertex-group name.

    Replaces the UIController._bone_centers escape hatch — parametrized over
    CoreFacade so that core/ui_controller/ carries no feature-specific logic.
    """
    obj = core_facade.get_obj()
    arm_obj = next(
        (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None
    )
    centers = {}
    arm_mat = arm_obj.matrix_world if arm_obj else None
    for vg in core_facade.get_vertex_groups():
        if arm_obj and arm_mat:
            bone = arm_obj.data.bones.get(vg.name)
            if bone:
                centers[vg.name] = (
                    arm_mat @ ((bone.head_local + bone.tail_local) * 0.5)
                ).to_tuple()
        # Vertex groups without a matching bone are omitted rather than set
        # to None — the Rust FFI signature requires (f64, f64, f64) tuples
        # for every value, and a missing key is already handled on that side.
    return centers


def classify_sides_by_topology(core_facade, axis_idx, direction, margin_frac=0.15):
    """Classify every vertex as source-side or target-side using geodesic
    (edge-length-weighted) distance along the mesh surface, not a raw
    coordinate-sign test.

    A coordinate-sign split misclassifies vertices whenever the mesh
    visually folds/intersects across the mirror plane — e.g. a vertex
    that's topologically part of the target side (say, the right leg) can
    have a coordinate that dips into source territory because the geometry
    pokes through the other leg there. Confirmed by a user report where the
    mirrored *weight data itself* (not just its rendering) was wrong right
    in the overlap region, using a pure coordinate-sign split.

    Two prior approaches were tried and both regressed on real models:
      - **Fixed-margin multi-seed BFS (hop-count):** seeded every vertex
        beyond a small coordinate margin, flood-filled by edge-hop count.
        A vertex poking deep enough past the plane exceeded that small
        margin and self-seeded on the wrong side directly, poisoning the
        flood-fill — confirmed: light overlap fixed, heavy overlap still
        misclassified.
      - **Single global extreme-vertex anchor per side (Dijkstra):** used
        exactly the single most-extreme vertex on each side as the only
        seed, reasoning an intersection can't push a vertex further out
        than the model's own true extremity. This backfired badly: if
        *any other, unrelated part* of the mesh (e.g. a trunk or ear) has a
        more extreme coordinate than the leg region itself, the anchor
        lands there instead — geodesic distance from a far-away anchor is
        then dominated by "distance to travel there" (similar for both
        legs), destroying local discrimination. Confirmed: previously-fixed
        areas broke again after this change.
      - Nearest-*bone*-position was also considered (bones don't self-
        intersect the way skin does) but is mathematically a dead end for
        symmetric rigs: distance-to-nearest-of-two-mirror-symmetric-points
        reduces algebraically to comparing the vertex's own raw coordinate
        sign — identical to the naive test that started this investigation,
        with zero added robustness.

    Current approach: multi-seed (not a single global anchor — keeps
    classification local to each body part) with a WIDE margin (15% of the
    mesh's own extent along the axis, not a tiny epsilon) so a *localized*
    bulge — a small fraction of a limb's own length/depth — can't clear the
    margin and self-seed wrong, while genuinely far-out vertices (the bulk
    of any limb) still seed correctly nearby. Propagation is Dijkstra over
    actual edge lengths (geodesic surface distance), not raw hop-count, for
    better accuracy on irregular mesh density. A vertex whose connected
    component has no reachable seed at all (rare — a fully disconnected
    island entirely within the margin band) falls back to its own raw
    coordinate sign as a last resort.

    Known remaining limitation: an intersection so severe that it consumes
    a large fraction of the limb's own length (wide) or pokes deeper than
    the margin allows (deep) can still be misclassified — there is no
    purely-geometric way to fully resolve that without extra information
    (e.g. the pre-deformation rest pose), only mitigate it.

    Returns a list of bool, length == vertex count, True == target side.
    """
    mesh = core_facade.get_mesh()
    num_verts = len(mesh.vertices)
    positions = [v.co.copy() for v in mesh.vertices]
    axis_vals = [p[axis_idx] for p in positions]

    span = max((abs(c) for c in axis_vals), default=0.0)
    margin = max(span * margin_frac, 1e-4)

    def _raw_side(c):
        if direction == 'POS_NEG':
            return c < 0
        return c > 0

    adjacency = [[] for _ in range(num_verts)]
    for e in mesh.edges:
        a, b = e.vertices[0], e.vertices[1]
        length = (positions[a] - positions[b]).length
        adjacency[a].append((b, length))
        adjacency[b].append((a, length))

    UNKNOWN = -1
    label = [UNKNOWN] * num_verts
    dist = [float('inf')] * num_verts
    heap = []

    for v_idx, c in enumerate(axis_vals):
        if abs(c) >= margin:
            label[v_idx] = 1 if _raw_side(c) else 0
            dist[v_idx] = 0.0
            heapq.heappush(heap, (0.0, v_idx))

    while heap:
        d, v_idx = heapq.heappop(heap)
        if d > dist[v_idx]:
            continue
        for n, length in adjacency[v_idx]:
            nd = d + length
            if nd < dist[n]:
                dist[n] = nd
                label[n] = label[v_idx]
                heapq.heappush(heap, (nd, n))

    for v_idx, c in enumerate(axis_vals):
        if label[v_idx] == UNKNOWN:
            label[v_idx] = 1 if _raw_side(c) else 0

    return [lbl == 1 for lbl in label]


def generate_pairs(vg_names, bone_centers, sr_list, axis, direction):
    """Build mirror name pairs from search/replace rules (string-based)."""
    rust = CoreFacade.get_rust_gateway("mirror_generate_pairs")
    result = rust.call(
        "rust_mirror_generate_pairs",
        vg_names,
        bone_centers,
        sr_list,
        axis,
        direction,
    )
    return {str(k): str(v) for k, v in result.items()}


def apply(layer_dict, id_pairs, vertex_groups_lock, vertex_coords, target_side_mask, axis, direction):
    """Layer-aware mirror with centre-plane splitting operating on Integer Bone IDs.

    All bone references are already integer vertex-group indices when they
    arrive here — UIController converted name_pairs→id_pairs upstream.

    Args:
        layer_dict: ``{v_idx: {bone_id: weight}}`` (int→int→float)
        id_pairs: ``{src_bone_id: tgt_bone_id}``
        vertex_groups_lock: ``{bone_id: bool}``
        target_side_mask: per-vertex bool list from ``classify_sides_by_topology()``
            — see module docstring's "TOPOLOGY-BASED SIDE CLASSIFICATION".

    Returns:
        ``(result, gap_v_indices)`` — see module docstring's "GAP-FILL FALLBACK".
    """
    if not id_pairs:
        return layer_dict, []

    layer_dict = {k: dict(v) for k, v in layer_dict.items()}

    rust = CoreFacade.get_rust_gateway("mirror_apply")
    result, gap_v_indices = rust.call(
        "rust_mirror_apply",
        layer_dict,
        id_pairs,
        vertex_groups_lock,
        vertex_coords,
        target_side_mask,
        axis,
        direction,
    )
    return {int(k): dict(v) for k, v in result.items()}, [int(v) for v in gap_v_indices]


def apply_mask(mask_dict, vertex_coords, axis, direction):
    """Mirror mask values across axis — no bone dimension.

    Args:
        mask_dict: ``{v_idx: float}``
    """
    rust = CoreFacade.get_rust_gateway("mirror_apply_mask")
    result = rust.call(
        "rust_mirror_apply_mask",
        mask_dict,
        vertex_coords,
        axis,
        direction,
    )
    return {k: v for k, v in result.items()}


def apply_mask_flat(mask_dict: dict, vertex_coords: list,
                    axis: str, direction: str,
                    num_verts: int) -> tuple:
    """⚡ Zero-copy flat-array mirror mask via CSR bridge.

    Deliberately kept on the original raw coordinate-sign test (unlike
    ``apply()``'s ``target_side_mask``) — the topology-based classification
    was confirmed to badly misclassify most of a complex character mesh's
    mask coverage (gap-fill was silently papering over most of it). The
    weight channel's classification fix stays; the mask channel keeps this
    self-contained raw coordinate test per ``features/mirror/README.md``'s
    guardrail: the two channels must never share a side-classification
    helper again.

    Returns ``(mask_dict, gap_v_indices)`` — ``gap_v_indices`` are target-side
    vertices (by the SAME raw coordinate test, computed inside Rust) left
    unmirrored by the nearest-vertex pass, for ``_fill_mask_gaps()`` to fill
    via a BVH surface fallback built from ``build_source_side_surface_raw()``
    — never ``build_source_side_surface()``/``target_side_mask``, which is
    weight-channel-only. ``mask_dict`` is int-keyed.
    """
    rust = CoreFacade.get_rust_gateway("mirror_apply_mask_flat")
    _bridge = CoreFacade.get_flat_array_bridge()
    mask_flat = _bridge.mask_to_flat(mask_dict, num_verts, sentinel=_bridge.MASK_SENTINEL)
    res_mask_flat, gap_v_indices = rust.call(
        "rust_mirror_apply_mask_flat",
        mask_flat, vertex_coords, axis, direction,
    )
    result = _bridge.flat_to_mask(res_mask_flat, sentinel=_bridge.MASK_SENTINEL)
    return result, [int(v) for v in gap_v_indices]


_AXIS_IDX = {"X": 0, "Y": 1, "Z": 2}


def build_source_side_surface(core_facade, target_side_mask):
    """Local-space triangulated BVH of the source side ONLY, for the gap-fill
    fallback (see module docstring's "GAP-FILL FALLBACK").

    A whole-mesh BVH is wrong here whenever the source and target sides
    physically overlap (e.g. an oversized pant leg intersecting the other
    leg): the flipped query point can end up geometrically closer to the
    target side's own folded
    geometry than to the true mirrored source point, bleeding in weight
    data from the wrong side entirely (confirmed by a visible "spiky" weight
    artifact on an intersecting model). Restricting the BVH to triangles
    with all 3 vertices on the source side guarantees the nearest point
    found can only ever come from the source, regardless of how much the
    target side overlaps it in space.

    *target_side_mask* must be the same ``classify_sides_by_topology()``
    result used everywhere else in the pipeline — a raw coordinate-sign test
    here would reintroduce the exact same misclassification problem in the
    intersecting region that motivated topology-based classification in the
    first place.

    Returns ``None`` if the mesh has no fully source-side triangle (nothing
    for the fallback to blend from).
    """
    mesh = core_facade.get_mesh()
    mesh.calc_loop_triangles()
    positions = [Vector(v.co) for v in mesh.vertices]
    triangles = [
        tuple(lt.vertices) for lt in mesh.loop_triangles
        if all(not target_side_mask[i] for i in lt.vertices)
    ]
    if not triangles:
        return None
    bvh = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
    return bvh, triangles, positions


def _is_source_side_raw(coord, axis_idx, direction):
    """Raw coordinate-sign source-side test — the MASK channel's own,
    self-contained side test. Deliberately NOT ``target_side_mask``/
    ``classify_sides_by_topology()`` — see ``build_source_side_surface_raw()``.
    """
    eps = 1e-5
    val = coord[axis_idx]
    if direction == 'POS_NEG':
        return val >= -eps
    return val <= eps


def build_source_side_surface_raw(core_facade, axis_idx, direction):
    """Local-space triangulated BVH of the source side ONLY, for the MASK
    channel's gap-fill fallback — using a plain raw coordinate-sign test,
    NOT ``target_side_mask``/``classify_sides_by_topology()``.

    Per ``features/mirror/README.md``'s guardrail: the weight and mask
    channels must use independent side-classification methods, never share
    one. Wiring the mask channel into the topology-based classification was
    tried and confirmed to badly misclassify most of a complex character
    mesh's mask coverage — this function exists specifically so the mask
    channel's gap-fill never touches that classification again.

    Returns ``None`` if the mesh has no fully source-side triangle (nothing
    for the fallback to blend from).
    """
    mesh = core_facade.get_mesh()
    mesh.calc_loop_triangles()
    positions = [Vector(v.co) for v in mesh.vertices]
    triangles = [
        tuple(lt.vertices) for lt in mesh.loop_triangles
        if all(_is_source_side_raw(positions[i], axis_idx, direction) for i in lt.vertices)
    ]
    if not triangles:
        return None
    bvh = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
    return bvh, triangles, positions


def _flip_point(coord, axis_idx):
    flipped = list(coord)
    flipped[axis_idx] = -flipped[axis_idx]
    return Vector(flipped)


def _fill_weight_gaps(layer_dict_before, result, gap_v_indices, id_pairs,
                       vertex_coords, axis_idx, surface, vertex_groups_lock):
    """BVH + barycentric fallback for the bone-weight channel's gaps.

    Mirrors ``transfer_core.closest_surface_point_transfer()``: for each gap
    target vertex, find the closest point on the mesh's own surface to its
    flipped (source-side) position, and barycentric-blend that triangle's 3
    vertices' *pre-mirror* weight data (id-remapped src->tgt via id_pairs).
    A location with genuinely no mirrored-bone weight nearby (e.g. a
    Spine-only area incorrectly flagged as a gap) blends to nothing and is
    left untouched — see the Rust-side gap detection comment.

    *surface* must be a **source-side-only** surface (see
    ``build_source_side_surface``) — a whole-mesh surface can bleed weight
    from the target side's own geometry when the mesh self-intersects.
    """
    if not gap_v_indices or surface is None:
        return

    bvh, triangles, positions = surface
    touched = []
    for v_idx in gap_v_indices:
        flipped = _flip_point(vertex_coords[v_idx], axis_idx)
        location, _normal, tri_idx, _dist = bvh.find_nearest(flipped)
        if tri_idx is None:
            continue

        tri = triangles[tri_idx]
        bary = poly_3d_calc([positions[i] for i in tri], location)

        bone_weights = {}
        for w, src_v in zip(bary, tri):
            for src_id, bw in layer_dict_before.get(src_v, {}).items():
                tgt_id = id_pairs.get(src_id)
                if tgt_id is None:
                    continue
                bone_weights[tgt_id] = bone_weights.get(tgt_id, 0.0) + w * bw

        if not bone_weights:
            continue

        bucket = result.setdefault(v_idx, {})
        for tgt_id, w in bone_weights.items():
            bucket[tgt_id] = bucket.get(tgt_id, 0.0) + w
        touched.append(v_idx)

    if not touched:
        return

    rust = CoreFacade.get_rust_gateway("norm_all_unlocked")
    for v_idx in touched:
        v_weights = result.get(v_idx)
        if not v_weights:
            continue
        result[v_idx] = dict(rust.call("rust_norm_all_unlocked", v_weights, vertex_groups_lock))


_CENTER_EPSILON = 1e-4


def _is_true_center_vertex(coord, axis_idx, epsilon=_CENTER_EPSILON):
    """Strict "is this vertex sitting exactly on the mirror seam" test.

    Deliberately a small FIXED absolute epsilon, NOT related to
    ``classify_sides_by_topology()``'s wide (15%-of-span) classification
    margin. A prior attempt at forcing seam vertices to a 50/50 split
    reused/overlapped with that wide margin and regressed the
    self-intersecting-mesh handling the margin exists for — keeping this
    check narrow and fully independent avoids that trap. This only ever
    matches vertices genuinely on the mirror plane, never the "confidently
    one side or the other, just within the classification margin" vertices
    ``classify_sides_by_topology()`` deals with.
    """
    return abs(coord[axis_idx]) <= epsilon


def _symmetrize_center_vertices(result, id_pairs, vertex_coords, axis_idx, vertex_groups_lock):
    """Force a true 50/50 split on each mirrored bone-pair's weight for
    vertices sitting exactly on the mirror seam (see
    ``_is_true_center_vertex``).

    A seam vertex is a single, shared point on a symmetric mesh — neither
    "source" nor "target" — so it should never end up holding 100% of one
    paired bone and 0% of the other (e.g. a vertex authored with only
    ``Leg.L`` weight and never split, that happens to sit exactly on the
    mirror plane). ``classify_sides_by_topology()``'s raw-coordinate
    fallback (and the Rust ``is_target``/``_raw_side`` tests) always
    classify an exact-zero coordinate as source-side, so ``apply()``'s
    STEP 1-4 never clears or writes into these vertices at all — this is a
    deliberately separate post-process, run entirely independently of
    ``target_side_mask``, so it can never perturb the self-intersection-safe
    classification used everywhere else in the weight channel.

    Locked bones are left untouched on a given vertex (that bone pair is
    skipped) to respect the same lock contract as the rest of the pipeline.

    Returns the list of vertex indices actually touched.
    """
    touched = []
    for v_idx, coord in enumerate(vertex_coords):
        if not _is_true_center_vertex(coord, axis_idx):
            continue
        vw = result.get(v_idx)
        if not vw:
            continue

        changed = False
        for src_id, tgt_id in id_pairs.items():
            if vertex_groups_lock.get(src_id) or vertex_groups_lock.get(tgt_id):
                continue
            w_src = vw.get(src_id, 0.0)
            w_tgt = vw.get(tgt_id, 0.0)
            if w_src == 0.0 and w_tgt == 0.0:
                continue
            half = (w_src + w_tgt) / 2.0
            if w_src != half:
                vw[src_id] = half
                changed = True
            if w_tgt != half:
                vw[tgt_id] = half
                changed = True

        if changed:
            touched.append(v_idx)

    if not touched:
        return touched

    rust = CoreFacade.get_rust_gateway("norm_all_unlocked")
    for v_idx in touched:
        v_weights = result.get(v_idx)
        if not v_weights:
            continue
        result[v_idx] = dict(rust.call("rust_norm_all_unlocked", v_weights, vertex_groups_lock))

    return touched


def _fill_mask_gaps(mask_dict_before, result, gap_v_indices, vertex_coords, axis_idx, surface, mask_default):
    """BVH + barycentric fallback for the mask channel's gaps.

    Mirrors ``_fill_weight_gaps()``'s approach (same "closest point on
    surface + barycentric blend" as ``transfer_core``), but for the mask
    channel — restricted to gap vertices only, so exact-match vertices are
    never touched or softened.

    Missing-vertex fallback uses *mask_default* — the active Layer's own
    ``mask_default`` field (see ``docs/bug-history/0023`` and ``0024``) —
    never a hardcoded constant. A "filled" Layer has no dense per-vertex
    mask entries at all, and its real default can legitimately be anything
    (e.g. ``0.0`` for a mostly-unpainted "cutout" layer), not just ``1.0``.

    *surface* must be from ``build_source_side_surface_raw()`` — the mask
    channel's own raw-coordinate-test surface, NEVER
    ``build_source_side_surface()``/``target_side_mask`` (weight-channel-only,
    see ``features/mirror/README.md``'s guardrail).
    """
    if not gap_v_indices or surface is None:
        return

    bvh, triangles, positions = surface
    for v_idx in gap_v_indices:
        flipped = _flip_point(vertex_coords[v_idx], axis_idx)
        location, _normal, tri_idx, _dist = bvh.find_nearest(flipped)
        if tri_idx is None:
            continue

        tri = triangles[tri_idx]
        bary = poly_3d_calc([positions[i] for i in tri], location)
        mask_val = sum(w * mask_dict_before.get(src_v, mask_default) for w, src_v in zip(bary, tri))
        if mask_val > 0.0:
            result[v_idx] = mask_val


def build_layer_mirror_plan(core_facade, axis, direction, sr_raw):
    """Resolve VG name pairs -> int ID pairs for the mirror computation.

    Returns None when no matching pairs exist (caller skips the layer channel).
    """
    vg_names = [vg.name for vg in core_facade.get_vertex_groups()]
    bone_centers = get_bone_centers(core_facade)
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): sr_raw={sr_raw!r} "
        f"vg_names_sample={vg_names[:10]!r}",
    )
    name_pairs = generate_pairs(
        vg_names=vg_names,
        bone_centers=bone_centers,
        sr_list=sr_raw,
        axis=axis,
        direction=direction,
    )
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): vg_names={len(vg_names)} "
        f"bone_centers={len(bone_centers)} name_pairs={name_pairs}",
    )

    # Cross-check: for every identity (unmatched) pair, redo the *.l -> *.r
    # style replace in plain Python and check byte-exact presence in
    # vg_names. Surfaces case/whitespace/naming mismatches invisible from
    # the Rust-side result alone.
    vg_name_set = set(vg_names)
    for src, tgt in name_pairs.items():
        if src != tgt:
            continue
        for search_text, replace_text in sr_raw:
            if not search_text or not replace_text or '*' not in search_text:
                continue
            if search_text.replace('*', '') not in src:
                continue
            candidate = src.replace(search_text.replace('*', ''), replace_text.replace('*', ''))
            CoreFacade.debug_log(
                "feature_domains",
                f"mirror.build_layer_mirror_plan(): identity-pair check src={src!r} "
                f"rule=({search_text!r},{replace_text!r}) candidate={candidate!r} "
                f"present_in_vg_names={candidate in vg_name_set}",
            )

    if not name_pairs:
        return None

    bone_to_id, id_to_bone = core_facade.get_unified_mapping()
    id_pairs = {
        bone_to_id[src]: bone_to_id[tgt]
        for src, tgt in name_pairs.items()
        if src in bone_to_id and tgt in bone_to_id
    }
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): id_pairs={id_pairs}",
    )
    return id_pairs if id_pairs else None


def _mirror_active_layer(core_facade, *, do_mask, do_layer, id_pairs,
                          axis, axis_idx, direction,
                          target_side_mask, fill_surface, mask_fill_surface,
                          vertex_coords):
    """Mirror the mask/weight channels of whichever layer is CURRENTLY active.

    Shared body for ``execute_mirror_pipeline()`` (single active layer) and
    ``execute_mirror_pipeline_all_layers()`` (looped across every layer in
    the stack) — side classification, gap-fill surfaces, and the layer-
    independent bone-pair plan (*id_pairs*) are all computed ONCE by the
    caller and passed in here, since none of them depend on which layer
    happens to be active.
    """
    if do_mask:
        mask_dict = core_facade.get_active_mask_dict()

        # Missing-vertex gap-fill fallback must use the active Layer's own
        # mask_default, not a hardcoded constant — see docs/bug-history/0024
        # (a sibling bug to 0023: a sparse mask dict's missing entries mean
        # "use this Layer's own default," which is not always 1.0).
        active_idx = core_facade.get_active_layer_index()
        mask_default = 1.0
        for m in core_facade.get_meta_list():
            if m.get("index", -1) == active_idx:
                mask_default = float(m.get("mask_default", 1.0))
                break

        res_mask, mask_gaps = apply_mask_flat(
            mask_dict=mask_dict,
            vertex_coords=vertex_coords,
            axis=axis,
            direction=direction,
            num_verts=core_facade.get_num_verts(),
        )
        if mask_gaps:
            _fill_mask_gaps(
                mask_dict, res_mask, mask_gaps, vertex_coords, axis_idx,
                mask_fill_surface, mask_default,
            )
            CoreFacade.debug_log(
                "feature_domains",
                f"mirror._mirror_active_layer(): mask gap-fill covered "
                f"{len(mask_gaps)} candidate vertex(es) on layer {active_idx}, "
                f"mask_default={mask_default}",
            )
        core_facade.write_mask_dict(res_mask)

    if do_layer:
        # read_active_layer() caches the unified mapping for the write call.
        layer_str = core_facade.read_active_layer()
        bone_to_id, id_to_bone = core_facade.get_unified_mapping()

        layer_int = {
            int(v_idx): {
                int(bone_to_id[b]): float(w)
                for b, w in weights.items()
                if b in bone_to_id
            }
            for v_idx, weights in layer_str.items()
        }
        locks_by_id = core_facade.get_locks_by_id()
        res_layer_int, weight_gaps = apply(
            layer_dict=layer_int,
            id_pairs=id_pairs,
            vertex_groups_lock=locks_by_id,
            vertex_coords=vertex_coords,
            target_side_mask=target_side_mask,
            axis=axis,
            direction=direction,
        )
        if weight_gaps:
            _fill_weight_gaps(
                layer_int, res_layer_int, weight_gaps, id_pairs,
                vertex_coords, axis_idx, fill_surface, locks_by_id,
            )
            CoreFacade.debug_log(
                "feature_domains",
                f"mirror._mirror_active_layer(): weight gap-fill covered "
                f"{len(weight_gaps)} candidate vertex(es)",
            )
        _symmetrize_center_vertices(
            res_layer_int, id_pairs, vertex_coords, axis_idx, locks_by_id,
        )
        res_layer_str = {
            int(v_idx): {
                str(id_to_bone[b_id]): float(w)
                for b_id, w in weights.items()
                if b_id in id_to_bone
            }
            for v_idx, weights in res_layer_int.items()
        }
        # write_active_layer calls finish() internally — no explicit finish needed.
        core_facade.write_active_layer(res_layer_str)
    else:
        # Mask-only path: write_mask_dict does not call finish.
        core_facade.finish_color_only()


def _prepare_mirror_pipeline(core_facade, axis, axis_idx, direction, sr_raw, mirror_data):
    """Resolve everything the mirror pipeline needs that does NOT depend on
    which layer is active: the do_mask/do_layer flags, the layer-independent
    bone-pair plan, side classification, gap-fill surfaces, and vertex
    coordinates.

    Shared by ``execute_mirror_pipeline()`` and
    ``execute_mirror_pipeline_all_layers()`` so the (expensive) whole-mesh
    Dijkstra classification and bone-pair resolution only ever run once per
    invocation, never once per layer.

    Raises ValueError when neither channel can produce output at all.
    """
    do_mask = mirror_data in ('MASK', 'BOTH')
    do_layer = mirror_data in ('BONE', 'BOTH')

    if do_layer:
        id_pairs = build_layer_mirror_plan(core_facade, axis, direction, sr_raw)
        if id_pairs is None:
            do_layer = False
    else:
        id_pairs = None

    if not do_mask and not do_layer:
        raise ValueError("No mirror pairs found for either channel.")

    # Topology-based side classification (see module docstring's
    # "WEIGHT vs. MASK CHANNEL" note) — used ONLY by the weight channel
    # (``apply()``) and its gap-fill's source-side surface below. The mask
    # channel deliberately computes its OWN, independent raw-coordinate
    # surface further down — see ``features/mirror/README.md``'s guardrail:
    # the two channels must never share a side-classification helper.
    target_side_mask = (
        classify_sides_by_topology(core_facade, axis_idx, direction) if do_layer else None
    )
    # Gap-fill always runs when gaps exist — each surface builder already
    # returns None gracefully if the mesh has no fully source-side triangle,
    # and both fill functions already no-op on an empty gap list, so building
    # these unconditionally (for the channel that's actually active) is safe.
    fill_surface = build_source_side_surface(core_facade, target_side_mask) if do_layer else None
    # Mask channel's OWN gap-fill surface — raw coordinate-sign test, NOT
    # target_side_mask (see the guardrail above).
    mask_fill_surface = (
        build_source_side_surface_raw(core_facade, axis_idx, direction) if do_mask else None
    )
    vertex_coords = core_facade.get_vertex_coordinates()

    return {
        "do_mask": do_mask,
        "do_layer": do_layer,
        "id_pairs": id_pairs,
        "target_side_mask": target_side_mask,
        "fill_surface": fill_surface,
        "mask_fill_surface": mask_fill_surface,
        "vertex_coords": vertex_coords,
    }


def execute_mirror_pipeline(core_facade):
    """Full mirror pipeline for the ACTIVE layer only, via CoreFacade only —
    no UIController dependency.

    Raises ValueError when neither channel can produce output, allowing the
    domain to return CANCELLED without an unhandled exception.
    """
    from .mirror_feature import MirrorPreferencesService

    axis = MirrorPreferencesService.get_mirror_axis()
    direction = MirrorPreferencesService.get_mirror_direction()
    sr_raw = MirrorPreferencesService.get_mirror_search_replace_pairs()
    mirror_data = MirrorPreferencesService.get_mirror_data()
    axis_idx = _AXIS_IDX[axis]

    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.execute_mirror_pipeline(): mirror_data={mirror_data} "
        f"obj.mode={core_facade.get_obj().mode} axis={axis} direction={direction}",
    )

    prep = _prepare_mirror_pipeline(core_facade, axis, axis_idx, direction, sr_raw, mirror_data)
    _mirror_active_layer(
        core_facade,
        do_mask=prep["do_mask"], do_layer=prep["do_layer"], id_pairs=prep["id_pairs"],
        axis=axis, axis_idx=axis_idx, direction=direction,
        target_side_mask=prep["target_side_mask"], fill_surface=prep["fill_surface"],
        mask_fill_surface=prep["mask_fill_surface"], vertex_coords=prep["vertex_coords"],
    )


def execute_mirror_pipeline_all_layers(core_facade):
    """Mirror EVERY layer's weight/mask channels, not just the active one —
    same settings (axis/direction/mirror_data/search-replace pairs) as
    ``execute_mirror_pipeline()``, applied once per layer in the stack.

    Reuses ``_prepare_mirror_pipeline()``'s per-mesh preparation across every
    layer via ``_mirror_active_layer()`` instead of redoing the expensive
    whole-mesh Dijkstra classification and bone-pair resolution once per
    layer — none of that depends on which layer is active.

    Restores whichever layer was active before the call once every layer has
    been mirrored, so this reads as a single bulk action from the user's
    perspective, not an implicit layer switch.

    Raises ValueError under the same condition as ``execute_mirror_pipeline()``.
    """
    from .mirror_feature import MirrorPreferencesService

    axis = MirrorPreferencesService.get_mirror_axis()
    direction = MirrorPreferencesService.get_mirror_direction()
    sr_raw = MirrorPreferencesService.get_mirror_search_replace_pairs()
    mirror_data = MirrorPreferencesService.get_mirror_data()
    axis_idx = _AXIS_IDX[axis]

    prep = _prepare_mirror_pipeline(core_facade, axis, axis_idx, direction, sr_raw, mirror_data)

    layer_indices = [m["index"] for m in core_facade.get_meta_list() if "index" in m]
    original_index = core_facade.get_active_layer_index()

    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.execute_mirror_pipeline_all_layers(): mirror_data={mirror_data} "
        f"layers={layer_indices} axis={axis} direction={direction}",
    )

    for index in layer_indices:
        core_facade.switch_to_layer(index)
        _mirror_active_layer(
            core_facade,
            do_mask=prep["do_mask"], do_layer=prep["do_layer"], id_pairs=prep["id_pairs"],
            axis=axis, axis_idx=axis_idx, direction=direction,
            target_side_mask=prep["target_side_mask"], fill_surface=prep["fill_surface"],
            mask_fill_surface=prep["mask_fill_surface"], vertex_coords=prep["vertex_coords"],
        )

    core_facade.switch_to_layer(original_index)
