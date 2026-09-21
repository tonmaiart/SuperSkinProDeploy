"""Mirror-weight logic — Rust Accelerated Multi-OS Portal."""

import heapq

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc
from mathutils.kdtree import KDTree

from ...core.facade import CoreFacade


def get_bone_centers(core_facade):
    """Compute world-space bone centre positions keyed by vertex-group name."""
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
    return centers


def classify_sides_by_topology(core_facade, axis_idx, direction, margin_frac=0.15):
    """Classify every vertex as source-side or target-side using geodesic (edge-length-
    weighted) distance along the mesh surface, not a raw coordinate-sign test."""
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
    """Layer-aware mirror with centre-plane splitting operating on Integer Bone IDs."""
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
    """Mirror mask values across axis — no bone dimension."""
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
    """Zero-copy flat-array mirror mask via CSR bridge."""
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
    """Local-space triangulated BVH of the source side ONLY, for the gap-fill fallback (see
    module docstring's "GAP-FILL FALLBACK")."""
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
    """Raw coordinate-sign source-side test."""
    eps = 1e-5
    val = coord[axis_idx]
    if direction == 'POS_NEG':
        return val >= -eps
    return val <= eps


def build_source_side_surface_raw(core_facade, axis_idx, direction):
    """Local-space triangulated BVH of the source side ONLY, for the MASK channel's gap-fill
    fallback."""
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
    """BVH + barycentric fallback for the bone-weight channel's gaps."""
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


_CENTER_EPSILON = 1e-9
_CENTER_NEIGHBOR_FRAC = 0.1


def _find_center_vertices(vertex_coords, axis_idx):
    """Indices of vertices sitting on the mirror seam: offset from the plane by at most a small
    fraction of the distance to their nearest other vertex, so the test is scale-free."""
    n = len(vertex_coords)
    if n == 0:
        return set()
    tree = KDTree(n)
    for i, c in enumerate(vertex_coords):
        tree.insert(c, i)
    tree.balance()

    center = set()
    for i, c in enumerate(vertex_coords):
        offset = abs(c[axis_idx])
        if offset <= _CENTER_EPSILON:
            center.add(i)
            continue
        nearest = tree.find_n(c, 2)
        if len(nearest) < 2:
            continue
        if offset <= nearest[1][2] * _CENTER_NEIGHBOR_FRAC:
            center.add(i)
    return center


def _symmetrize_center_vertices(result, id_pairs, center_vertices, vertex_groups_lock):
    """Force a true 50/50 split on each mirrored bone-pair's weight for vertices sitting
    on the mirror seam."""
    touched = []
    for v_idx in sorted(center_vertices):
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
    """BVH + barycentric fallback for the mask channel's gaps."""
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
    """Resolve VG name pairs -> int ID pairs for the mirror computation."""
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
    for src_id, tgt_id in list(id_pairs.items()):
        id_pairs.setdefault(tgt_id, src_id)
    CoreFacade.debug_log(
        "feature_domains",
        f"mirror.build_layer_mirror_plan(): id_pairs={id_pairs}",
    )
    return id_pairs if id_pairs else None


def _mirror_active_layer(core_facade, *, do_mask, do_layer, id_pairs,
                          axis, axis_idx, direction,
                          target_side_mask, fill_surface, mask_fill_surface,
                          vertex_coords, center_vertices):
    """Mirror the mask/weight channels of whichever layer is CURRENTLY active (the layer
    selected in the Layer list."""
    if do_mask:
        mask_dict = core_facade.get_active_mask_dict()

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
            res_layer_int, id_pairs, center_vertices, locks_by_id,
        )
        res_layer_str = {
            int(v_idx): {
                str(id_to_bone[b_id]): float(w)
                for b_id, w in weights.items()
                if b_id in id_to_bone
            }
            for v_idx, weights in res_layer_int.items()
        }
        core_facade.write_active_layer(res_layer_str)
    else:
        # Mask-only path: write_mask_dict does not call finish.
        core_facade.finish_color_only()


def _prepare_mirror_pipeline(core_facade, axis, axis_idx, direction, sr_raw, mirror_data):
    """Resolve everything the mirror pipeline needs that does NOT depend on which layer is
    active: the."""
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

    target_side_mask = (
        classify_sides_by_topology(core_facade, axis_idx, direction) if do_layer else None
    )
    vertex_coords = core_facade.get_vertex_coordinates()
    center_vertices = _find_center_vertices(vertex_coords, axis_idx) if do_layer else set()
    if target_side_mask is not None:
        for v_idx in center_vertices:
            target_side_mask[v_idx] = False
    fill_surface = build_source_side_surface(core_facade, target_side_mask) if do_layer else None
    # Mask channel's OWN gap-fill surface — raw coordinate-sign test, NOT
    # target_side_mask (see the guardrail above).
    mask_fill_surface = (
        build_source_side_surface_raw(core_facade, axis_idx, direction) if do_mask else None
    )

    return {
        "do_mask": do_mask,
        "do_layer": do_layer,
        "id_pairs": id_pairs,
        "target_side_mask": target_side_mask,
        "fill_surface": fill_surface,
        "mask_fill_surface": mask_fill_surface,
        "vertex_coords": vertex_coords,
        "center_vertices": center_vertices,
    }


def execute_mirror_pipeline(core_facade):
    """Full mirror pipeline for the layer currently selected in the Layer list (the active
    layer, via CoreFacade only."""
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
        center_vertices=prep["center_vertices"],
    )
