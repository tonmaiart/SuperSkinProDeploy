"""Shared "closest point on surface" weight-transfer engine.

Used by both the live-mesh Copy Skin Weight operator (`ops.py`) and the JSON
import operator (`io_ops.py`) — importing a JSON file is conceptually the
same transfer, just with a "source" reconstructed from a file instead of a
live `bpy.types.Object`. A source only needs to supply, generically:

  - `composite_weights` / `composite_mask`: the flattened single-result
    `{v_idx: {bone_name: weight}}` / `{v_idx: mask_value}` pair used by
    `MERGE` (for a live mesh this is its native Vertex Groups; for a JSON
    file this is a `composite_weights` block captured at export time).
  - `layers`: a list of `(name, weight_dict, mask_dict, mask_default)`
    tuples used by `SEPARATE` — one entry per source SuperSkinPro Layer (or
    a single synthetic entry when there is no Layer system).
  - a `source_surface` — `(bvh, triangles, positions)` — needed only for
    `CLOSEST_DISTANCE`.
"""

from mathutils.bvhtree import BVHTree
from mathutils.interpolate import poly_3d_calc

from ...core.facade import CoreFacade
from ...interface.utils.utils import (
    _has_layer_system,
    _run_in_object_context,
    _select_only_layer,
    _enforce_visualizer_from_tab_state,
    sync_layers_to_ui_collection,
)


def unique_layer_name(existing_names, name):
    """Disambiguate *name* against *existing_names* with a Blender-style '.001' suffix."""
    if name not in existing_names:
        return name
    i = 1
    while f"{name}.{i:03d}" in existing_names:
        i += 1
    return f"{name}.{i:03d}"


def ensure_armature_modifier(target, armature_obj):
    """Create/find target's Armature modifier and point it at *armature_obj*.

    Shared by the live-mesh transfer (armature_obj = the source mesh's own
    bound armature) and JSON import's "Auto Assign Modifier" option
    (armature_obj = found-or-created by the name recorded at export time).
    """
    arm_mod = next((m for m in target.modifiers if m.type == 'ARMATURE'), None)
    if not arm_mod:
        arm_mod = target.modifiers.new(name="Armature", type='ARMATURE')
    arm_mod.object = armature_obj
    arm_mod.use_deform_preserve_volume = True
    return arm_mod


def ensure_native_vertex_groups(target, bone_names):
    """Create whichever of *bone_names* are missing from `target.vertex_groups`.

    Same auto-setup philosophy as auto-detecting/creating the Armature
    modifier: `finish()`'s reflatten always needs a slot to write into, even
    on a brand-new target or one with a partially-matching rig.
    """
    existing_vg_names = {vg.name for vg in target.vertex_groups}
    for name in bone_names:
        if name not in existing_vg_names:
            target.vertex_groups.new(name=name)


def build_surface(positions, triangles):
    """Build a world-space BVH from raw vertex positions + triangle index tuples.

    Purely geometric — independent of any Layer's weight data — so it should
    be built once per operator run and shared across every target/Layer.
    """
    bvh = BVHTree.FromPolygons(positions, triangles, all_triangles=True)
    return bvh, triangles, positions


def build_restricted_surface(positions, triangles, allowed_verts):
    """Build a BVH from only the subset of *triangles* whose 3 corners are
    all in *allowed_verts* — a genuine sub-surface patch, used as the
    "Use Selected Source Vertices" WEIGHT surface (see rule 18 in this
    domain's README).

    Post-hoc filtering weight contributions per-vertex after a `find_nearest()`
    against the FULL surface (an earlier approach) meant the nearest triangle
    found could straddle the selection boundary — a triangle with only 1 or 2
    of its 3 corners selected still gets returned as "nearest" by the full
    BVH, and the excluded corner(s) simply dropped out of the blend, leaving
    scattered zero-weight points wherever the nearest triangle happened to
    have zero selected corners even deep inside an otherwise well-selected
    patch. Building a dedicated BVH from ONLY fully-selected triangles instead
    guarantees every nearest-point query for weight purposes lands on a
    triangle entirely inside the selection, giving a coherent, hole-free
    blend across the whole reachable patch.

    Returns `None` if no triangle has all 3 corners selected (e.g. a sparse,
    non-contiguous vertex selection) — the caller must handle this rather
    than silently falling back to the full surface.
    """
    restricted = [tri for tri in triangles if all(vi in allowed_verts for vi in tri)]
    if not restricted:
        return None
    return build_surface(positions, restricted)


def closest_surface_point_transfer(
    target, source_surface, layer_weights, layer_mask, mask_default,
    allowed_target_verts=None, weight_surface=None, target_positions=None,
):
    """True "closest point on surface" transfer (Maya's Closest Point / ngSkinTools'
    Transfer Weights): finds the nearest point on the source's triangulated surface
    for each target vertex and barycentric-blends the weight AND mask of that
    triangle's 3 vertices, using this Layer's own weight/mask data.

    This stays smooth wherever the source itself is smooth — unlike snapping to a
    single nearest vertex, which quantizes into flat, kinked bands whenever the
    target has more topology than the source — and it naturally confines a
    localized Layer's influence to wherever the source itself actually painted it,
    via the source's own mask values, instead of a separate distance-based falloff
    heuristic that doesn't know where the source's real paint boundary is.

    *mask_default* is used for any triangle vertex missing from *layer_mask* —
    a vertex not explicitly painted differently still carries the layer's own
    default mask level (typically 1.0 for a "filled white" layer), not 0.0.

    *allowed_target_verts* (optional set of vertex indices) restricts which
    target vertices participate — used by the live-transfer operator's "Use
    Selected Target Vertices" option. A target vertex outside it is skipped
    outright (never queried, never written); `None` means no restriction.

    *weight_surface* (optional `(bvh, triangles, positions)`, same shape as
    *source_surface*) implements "Use Selected Source Vertices": when given,
    it is a DIFFERENT, smaller BVH built by `build_restricted_surface()` from
    only the triangles fully inside the source selection, queried separately
    from *source_surface* for the WEIGHT half of the blend. *source_surface*
    itself is always used for the MASK half, unrestricted — so a target
    vertex whose nearest point (on the *restricted* surface) still finds a
    valid weight ends up with a real bone-weight blend, while mask coverage
    always matches what it would be with the option off, never leaving a
    genuine Mask Gap (see rule 18 in this domain's README for why mask and
    weight are deliberately queried against two different surfaces here).
    When `None`, weight is blended from *source_surface* like mask is.

    *target_positions* (optional list of world-space `Vector`s, aligned to
    `target.data.vertices` index order) implements the live-transfer
    operator's "Current Pose" option (rule 19 in this domain's README): when
    given, each target vertex's query point `P` is read from this list
    instead of `target.matrix_world @ v.co`, letting the caller supply
    depsgraph-evaluated (posed) coordinates instead of the mesh's stored
    rest/bind shape. `None` (the default, and always for JSON import) keeps
    the original rest-pose behavior.
    """
    mask_bvh, mask_tris, mask_world_verts = source_surface
    weight_bvh, weight_tris, weight_world_verts = (
        (mask_bvh, mask_tris, mask_world_verts) if weight_surface is None else weight_surface
    )
    matrix_world_tgt = target.matrix_world

    weight_map = {}
    mask_map = {}
    for v in target.data.vertices:
        if allowed_target_verts is not None and v.index not in allowed_target_verts:
            continue

        P = target_positions[v.index] if target_positions is not None else matrix_world_tgt @ v.co

        mask_value = 0.0
        location, _normal, tri_idx, _dist = mask_bvh.find_nearest(P)
        if tri_idx is not None:
            tri = mask_tris[tri_idx]
            bary = poly_3d_calc([mask_world_verts[i] for i in tri], location)
            for w, v_idx in zip(bary, tri):
                mask_value += w * layer_mask.get(v_idx, mask_default)

        bone_weights = {}
        w_location, _w_normal, w_tri_idx, _w_dist = weight_bvh.find_nearest(P)
        if w_tri_idx is not None:
            w_tri = weight_tris[w_tri_idx]
            w_bary = poly_3d_calc([weight_world_verts[i] for i in w_tri], w_location)
            for w, v_idx in zip(w_bary, w_tri):
                for bone, bw in layer_weights.get(v_idx, {}).items():
                    bone_weights[bone] = bone_weights.get(bone, 0.0) + w * bw

        if mask_value <= 0.0:
            continue

        mask_map[v.index] = mask_value
        if bone_weights:
            weight_map[v.index] = bone_weights

    return weight_map, mask_map


def compute_layer_payloads(
    layer_output, transfer_method, target, source_surface,
    composite_weights, composite_mask, layers, merge_name,
    allowed_source_verts=None, allowed_target_verts=None, weight_surface=None,
    target_positions=None,
):
    """Shared MERGE/SEPARATE x CLOSEST_DISTANCE/VERTEX_ID decision tree.

    Returns a list of `(name, weight_dict, mask_dict)` triples ready for
    `write_layers_to_target()`, identically for a live-mesh source or a
    JSON-file source.

    *allowed_source_verts* / *allowed_target_verts* / *weight_surface*
    (default `None` = unrestricted) implement the live-transfer operator's
    "Use Selected Source/Target Vertices" options — JSON export/import never
    pass these, so their behavior is unchanged. `VERTEX_ID` has no surface
    concept (it is a direct index copy) so it still uses *allowed_source_verts*
    directly; `CLOSEST_DISTANCE` instead uses *weight_surface* — a BVH
    pre-restricted to the source selection via `build_restricted_surface()` —
    since a surface-level restriction (rather than a post-hoc per-vertex
    filter) is what avoids scattered zero-weight holes (see rule 18 in this
    domain's README).

    *target_positions* (optional, `CLOSEST_DISTANCE` only) implements the
    live-transfer operator's "Current Pose" option — see rule 19 in this
    domain's README and `closest_surface_point_transfer()`'s own docstring.
    `VERTEX_ID` ignores it entirely since it never queries geometry at all.
    """
    if layer_output == 'MERGE':
        if transfer_method == 'VERTEX_ID':
            # Vertex index == source index == target index for this method,
            # so the same index must pass both restrictions at once.
            weight_map = {
                v_idx: bw for v_idx, bw in composite_weights.items()
                if (allowed_source_verts is None or v_idx in allowed_source_verts)
                and (allowed_target_verts is None or v_idx in allowed_target_verts)
            }
            mask_map = {v_idx: 1.0 for v_idx in weight_map}
        else:
            weight_map, mask_map = closest_surface_point_transfer(
                target, source_surface, composite_weights, composite_mask, 0.0,
                allowed_target_verts=allowed_target_verts, weight_surface=weight_surface,
                target_positions=target_positions,
            )
        return [(merge_name, weight_map, mask_map)]

    layer_payloads = []
    for name, layer_weights, layer_mask, mask_default in layers:
        if transfer_method == 'VERTEX_ID':
            # Source vertex index == target vertex index (checked by the caller),
            # so the layer's own dicts already ARE the target maps.
            layer_weight_map = {
                v_idx: bw for v_idx, bw in layer_weights.items() if bw
                and (allowed_source_verts is None or v_idx in allowed_source_verts)
                and (allowed_target_verts is None or v_idx in allowed_target_verts)
            }
            layer_mask_map = {v_idx: 1.0 for v_idx in layer_weight_map}
        else:
            layer_weight_map, layer_mask_map = closest_surface_point_transfer(
                target, source_surface, layer_weights, layer_mask, mask_default,
                allowed_target_verts=allowed_target_verts, weight_surface=weight_surface,
                target_positions=target_positions,
            )
        layer_payloads.append((name, layer_weight_map, layer_mask_map))
    return layer_payloads


def write_layers_to_target(context, target, insert_method, layer_payloads):
    """Create real SuperSkinPro Layer(s) on target and let finish() reflatten to native VGs."""
    facade = CoreFacade(context)
    ctrl = facade.get_ctrl()

    if not _has_layer_system(target):
        _run_in_object_context(context, ctrl.init_layer_system)
        CoreFacade.debug_log("feature_domains", f"weight_transfer: init_layer_system() on {target.name!r}")

    # For REPLACE, snapshot the pre-existing layers but do NOT remove them
    # yet — remove only after the new layer(s) already exist below. Some
    # layer systems refuse to remove the last remaining layer as a safety
    # rail; creating the replacements first means the target is never
    # left with zero layers, so that guard (if present) is never hit and
    # a full "replace everything" never gets stuck leaving one stale
    # layer behind. This requires no change to core/ at all.
    old_slots_to_remove = []
    existing_names = set()
    if insert_method == 'REPLACE':
        old_slots_to_remove = sorted(
            (m.get("index", -1) for m in facade.get_meta_list() if m.get("index", -1) >= 0),
            reverse=True,
        )
    else:
        existing_names = {m.get("name") for m in facade.get_meta_list() if m.get("name")}

    # create_layer() inserts each new Layer at the top of the stack, so
    # creating layer_payloads in its given order would land them in
    # reverse (last one created ends up on top, i.e. first). Creating
    # them back-to-front makes the *first* payload end up on top, which
    # matches the source's own stack order (e.g. Head, Arm, Base stays
    # Head, Arm, Base instead of coming out as Base, Arm, Head).
    #
    # The whole create/switch/write/remove sequence runs inside a single
    # _run_in_object_context() call rather than one per structural call:
    # switch_to_layer() is mode-aware but write_layer_dict() is not (see
    # core/facade/README.md) — wrapping only create_layer()/remove_layer()
    # individually would restore Edit Mode in between calls, right before
    # the non-mode-aware write_layer_dict() runs, silently reintroducing
    # the 0018/0019 stale-store class of bug if `target` happens to already
    # be in Edit Mode (multi-object edit) when this runs.
    def _do_write():
        last_slot = -1
        for name, weight_dict, mask_dict in reversed(layer_payloads):
            unique_name = unique_layer_name(existing_names, name)
            existing_names.add(unique_name)

            new_slot = ctrl.create_layer(unique_name)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer: create_layer(name={unique_name!r}) on {target.name!r} -> slot={new_slot} verts={len(weight_dict)}",
            )
            if new_slot is None or new_slot < 0:
                continue
            last_slot = new_slot
            facade.switch_to_layer(new_slot, push_undo=False)
            facade.write_layer_dict(weight_dict)
            if mask_dict:
                # A freshly created Layer has no mask coverage by default, so
                # every vertex reads as "outside the mask" (Mask Gap error)
                # even though weight data was written.
                facade.write_mask_dict(mask_dict)

        if old_slots_to_remove:
            for slot in old_slots_to_remove:
                ctrl.remove_layer(slot)
            CoreFacade.debug_log(
                "feature_domains",
                f"weight_transfer: removed {len(old_slots_to_remove)} pre-existing layer(s) on {target.name!r} after creating replacements",
            )
            return ctrl.active_layer_index
        return last_slot

    last_slot = _run_in_object_context(context, _do_write)

    facade.finish()

    if last_slot >= 0:
        _select_only_layer(target, last_slot)
    sync_layers_to_ui_collection(target)
    _enforce_visualizer_from_tab_state(context)
