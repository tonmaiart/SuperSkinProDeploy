"""Pure-data helpers for clipboard extract, resolve, and validation.

No Blender imports — operates on plain dicts, lists, and sets.
These functions are used by ``features/clipboard/logic.py`` during Copy, Cut, and Paste.

Data conventions
----------------
- WEIGHT layer dict: ``{v_idx: {"BoneName": weight, ...}, ...}``  (str→str→float)
- MASK dict:          ``{v_idx: float, ...}``                       (str→float)
  (Vertex keys are str in storage, int in resolution outputs.)
- All resolve functions return int-keyed dicts ready for direct merge
  into the target storage dict.
"""

from __future__ import annotations


# ═════════════════════════════════════════════════════════════════════════
#  Extract helpers (used by copy() / cut())
# ═════════════════════════════════════════════════════════════════════════

def extract_weight_subset(layer_dict: dict, verts: list[int]) -> dict:
    """Return ``{v: {bone: w}}`` for only the requested vertex indices.

    Args:
        layer_dict: Full layer dict (keys may be int or str).
        verts: List of int vertex indices to include.

    Returns:
        Subset dict with str-keyed vertex indices.
    """
    # Normalise to string keys (storage may use int keys via pickle)
    layer_dict = {str(k): v for k, v in layer_dict.items()}
    verts_set = {str(v) for v in verts}
    return {
        v: dict(weights)
        for v, weights in layer_dict.items()
        if v in verts_set and weights
    }


def extract_mask_subset(mask_dict: dict, verts: list[int]) -> dict:
    """Return ``{v: float}`` for only the requested vertex indices.

    Args:
        mask_dict: Full mask dict (keys may be int or str).
        verts: List of int vertex indices to include.

    Returns:
        Subset dict with str-keyed vertex indices.
    """
    # Normalise to string keys (storage may use int keys via pickle)
    mask_dict = {str(k): v for k, v in mask_dict.items()}
    verts_set = {str(v) for v in verts}
    return {
        v: float(w)
        for v, w in mask_dict.items()
        if v in verts_set
    }


# ═════════════════════════════════════════════════════════════════════════
#  Paste-target resolution (vertex-count rules — AMENDED)
# ═════════════════════════════════════════════════════════════════════════

def _is_same_mesh(source_mesh_name: str, current_mesh_name: str) -> bool:
    """Check whether the clipboard was copied from the same mesh."""
    return source_mesh_name == current_mesh_name


def resolve_paste_targets_weight(
    clip_data: dict,
    target_verts: list[int],
    source_mesh_name: str,
    current_mesh_name: str,
) -> dict[int, dict[str, float]]:
    """Resolve clipboard→target vertex mapping for WEIGHT data.

    Args:
        clip_data: Clipboard weight dict ``{v: {bone: w}}`` (str-keyed v's).
        target_verts: List of target vertex indices (int).
        source_mesh_name: Name of the mesh the clipboard was copied from.
        current_mesh_name: Name of the current active mesh.

    Returns:
        ``{target_v_idx: {bone_name: weight}}`` ready for direct merge.
        All keys are int.

    Raises:
        ValueError: When the vertex-count combination is invalid
            (see the resolution table in AGENTS.md).
    """
    # Normalise clip_data keys to strings (storage may use int keys via pickle)
    clip_data = {str(k): v for k, v in clip_data.items()}

    clip_count = len(clip_data)
    target_count = len(target_verts)
    same_mesh = _is_same_mesh(source_mesh_name, current_mesh_name)

    # Case 1: Broadcast — 1 clipboard vertex → any number of targets
    if clip_count == 1:
        src_v, src_data = next(iter(clip_data.items()))
        return {t: dict(src_data) for t in target_verts}

    # Case 2: Blend — N clipboard vertices → 1 target
    if clip_count > 1 and target_count == 1:
        target_v = target_verts[0]
        all_bones: set[str] = set()
        for weights in clip_data.values():
            all_bones.update(weights.keys())

        blended: dict[str, float] = {}
        for bone in all_bones:
            total = 0.0
            count = 0
            for weights in clip_data.values():
                w = weights.get(bone, 0.0)
                total += w
                count += 1
            blended[bone] = total / count if count else 0.0
        return {target_v: blended}

    # Case 3: Pair — N→N, same mesh, 1:1 zip by ascending vertex index
    if clip_count > 1 and clip_count == target_count and same_mesh:
        clip_verts = sorted(int(v) for v in clip_data.keys())
        target_sorted = sorted(target_verts)
        return {
            t: dict(clip_data[str(c)])
            for c, t in zip(clip_verts, target_sorted)
        }

    # Case 4: N→M mismatch (N≠M, both >1)
    if clip_count > 1 and target_count > 1 and clip_count != target_count:
        raise ValueError(
            f"Cannot paste: clipboard has {clip_count} vertices "
            f"but {target_count} are selected. Select exactly "
            f"{clip_count}, exactly 1 (blend), or no vertices "
            f"(whole-mesh broadcast)."
        )

    # Case 5: N→N but different source mesh
    if clip_count > 1 and clip_count == target_count and not same_mesh:
        raise ValueError(
            f"Cannot paste {clip_count}-to-{target_count} across different "
            f"meshes. Use broadcast (select 1 target) or blend (select no "
            f"vertices)."
        )

    # Fallback (should not be reached if resolution table is complete)
    raise ValueError(
        f"Unhandled paste resolution: clip={clip_count}, "
        f"target={target_count}, same_mesh={same_mesh}"
    )


def resolve_paste_targets_mask(
    clip_data: dict,
    target_verts: list[int],
    source_mesh_name: str,
    current_mesh_name: str,
) -> dict[int, float]:
    """Resolve clipboard→target vertex mapping for MASK data.

    Args:
        clip_data: Clipboard mask dict ``{v: float}`` (str-keyed v's).
        target_verts: List of target vertex indices (int).
        source_mesh_name: Name of the mesh the clipboard was copied from.
        current_mesh_name: Name of the current active mesh.

    Returns:
        ``{target_v_idx: float}`` ready for direct merge.  All keys are int.

    Raises:
        ValueError: When the vertex-count combination is invalid.
    """
    # Normalise clip_data keys to strings (storage may use int keys via pickle)
    clip_data = {str(k): v for k, v in clip_data.items()}

    clip_count = len(clip_data)
    target_count = len(target_verts)
    same_mesh = _is_same_mesh(source_mesh_name, current_mesh_name)

    # Case 1: Broadcast — 1 clipboard vertex → any number of targets
    if clip_count == 1:
        src_v, src_val = next(iter(clip_data.items()))
        return {t: float(src_val) for t in target_verts}

    # Case 2: Blend — N clipboard vertices → 1 target
    if clip_count > 1 and target_count == 1:
        target_v = target_verts[0]
        avg = sum(float(w) for w in clip_data.values()) / clip_count
        return {target_v: avg}

    # Case 3: Pair — N→N, same mesh, 1:1 zip by ascending vertex index
    if clip_count > 1 and clip_count == target_count and same_mesh:
        clip_verts = sorted(int(v) for v in clip_data.keys())
        target_sorted = sorted(target_verts)
        return {
            t: float(clip_data[str(c)])
            for c, t in zip(clip_verts, target_sorted)
        }

    # Case 4: N→M mismatch
    if clip_count > 1 and target_count > 1 and clip_count != target_count:
        raise ValueError(
            f"Cannot paste: clipboard has {clip_count} vertices "
            f"but {target_count} are selected. Select exactly "
            f"{clip_count}, exactly 1 (blend), or no vertices "
            f"(whole-mesh broadcast)."
        )

    # Case 5: N→N but different source mesh
    if clip_count > 1 and clip_count == target_count and not same_mesh:
        raise ValueError(
            f"Cannot paste {clip_count}-to-{target_count} across different "
            f"meshes. Use broadcast (select 1 target) or blend (select no "
            f"vertices)."
        )

    # Fallback
    raise ValueError(
        f"Unhandled paste resolution: clip={clip_count}, "
        f"target={target_count}, same_mesh={same_mesh}"
    )


# ═════════════════════════════════════════════════════════════════════════
#  Cross-mesh bone-name validation (AMENDED)
# ═════════════════════════════════════════════════════════════════════════

def validate_bone_compatibility(
    clip_data: dict,
    target_vg_names: set[str],
    clip_kind: str,
) -> tuple[bool, str]:
    """Check whether the bone names inside a WEIGHT clipboard are a subset
    of the target mesh's vertex-group names.

    Args:
        clip_data: Clipboard dict
            (``{v: {bone_name: w}}`` for WEIGHT, ``{v: float}`` for MASK).
        target_vg_names: ``set(vg.name)`` for every vertex group on the
            target object.
        clip_kind: ``'WEIGHT'`` or ``'MASK'``.

    Returns:
        ``(True, "")`` if compatible.
        ``(False, human_readable_reason)`` if not compatible.

    MASK clipboard is always compatible (no bone dimension).
    WEIGHT clipboard is compatible iff every bone name present in any
    vertex of clip_data exists in *target_vg_names*.
    """
    if clip_kind == 'MASK':
        return True, ""

    all_bones_in_clip: set[str] = set()
    for weights in clip_data.values():
        all_bones_in_clip.update(weights.keys())

    missing = all_bones_in_clip - target_vg_names
    if missing:
        missing_list = sorted(missing)
        missing_str = ", ".join(missing_list[:5])
        if len(missing_list) > 5:
            missing_str += f" … (+{len(missing_list) - 5} more)"
        return False, (
            f"Clipboard contains bone(s) not present on this mesh: "
            f"{missing_str}. Copy/paste across meshes requires identical "
            f"bone sets."
        )

    return True, ""


# ═════════════════════════════════════════════════════════════════════════
#  Affected-vertex queries (used by select_affected / get_affected_vertex_indices)
# ═════════════════════════════════════════════════════════════════════════

def vertices_with_mask_override(mask_dict: dict) -> set[int]:
    """Return set of vertex indices that have an explicit mask override.

    A vertex with an entry in ``mask_dict`` is considered to have an
    explicit override (vertices sitting at ``mask_default`` are absent
    from the dict entirely).

    Args:
        mask_dict: ``{v_idx: float, ...}`` with str-keyed vertex indices.

    Returns:
        ``set(int)`` of vertex indices with an explicit mask value.
    """
    return {int(v) for v in mask_dict.keys()}


def vertices_with_weight(layer_dict: dict, bone_name: str) -> set[int]:
    """Return set of vertex indices where ``bone_name`` has weight > 0.001.

    Args:
        layer_dict: ``{v_idx: {"BoneName": weight, ...}, ...}``
            with str-keyed vertex indices.
        bone_name: The bone/vertex-group name to query.

    Returns:
        ``set(int)`` of vertex indices where the bone's weight exceeds 0.001.
    """
    result: set[int] = set()
    for v_str, weights in layer_dict.items():
        w = weights.get(bone_name, 0.0)
        if w > 0.001:
            result.add(int(v_str))
    return result
