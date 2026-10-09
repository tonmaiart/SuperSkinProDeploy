
from __future__ import annotations



def extract_weight_subset(layer_dict: dict, verts: list[int]) -> dict:
    layer_dict = {str(k): v for k, v in layer_dict.items()}
    verts_set = {str(v) for v in verts}
    return {
        v: dict(weights)
        for v, weights in layer_dict.items()
        if v in verts_set and weights
    }


def extract_mask_subset(mask_dict: dict, verts: list[int]) -> dict:
    mask_dict = {str(k): v for k, v in mask_dict.items()}
    verts_set = {str(v) for v in verts}
    return {
        v: float(w)
        for v, w in mask_dict.items()
        if v in verts_set
    }



def _is_same_mesh(source_mesh_name: str, current_mesh_name: str) -> bool:
    return source_mesh_name == current_mesh_name


def resolve_paste_targets_weight(
    clip_data: dict,
    target_verts: list[int],
    source_mesh_name: str,
    current_mesh_name: str,
) -> dict[int, dict[str, float]]:
    clip_data = {str(k): v for k, v in clip_data.items()}

    clip_count = len(clip_data)
    target_count = len(target_verts)
    same_mesh = _is_same_mesh(source_mesh_name, current_mesh_name)

    if clip_count == 1:
        src_v, src_data = next(iter(clip_data.items()))
        return {t: dict(src_data) for t in target_verts}

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

    if clip_count > 1 and clip_count == target_count and same_mesh:
        clip_verts = sorted(int(v) for v in clip_data.keys())
        target_sorted = sorted(target_verts)
        return {
            t: dict(clip_data[str(c)])
            for c, t in zip(clip_verts, target_sorted)
        }

    if clip_count > 1 and target_count > 1 and clip_count != target_count:
        raise ValueError(
            f"Cannot paste: clipboard has {clip_count} vertices "
            f"but {target_count} are selected. Select exactly "
            f"{clip_count}, exactly 1 (blend), or no vertices "
            f"(whole-mesh broadcast)."
        )

    if clip_count > 1 and clip_count == target_count and not same_mesh:
        raise ValueError(
            f"Cannot paste {clip_count}-to-{target_count} across different "
            f"meshes. Use broadcast (select 1 target) or blend (select no "
            f"vertices)."
        )

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
    clip_data = {str(k): v for k, v in clip_data.items()}

    clip_count = len(clip_data)
    target_count = len(target_verts)
    same_mesh = _is_same_mesh(source_mesh_name, current_mesh_name)

    if clip_count == 1:
        src_v, src_val = next(iter(clip_data.items()))
        return {t: float(src_val) for t in target_verts}

    if clip_count > 1 and target_count == 1:
        target_v = target_verts[0]
        avg = sum(float(w) for w in clip_data.values()) / clip_count
        return {target_v: avg}

    if clip_count > 1 and clip_count == target_count and same_mesh:
        clip_verts = sorted(int(v) for v in clip_data.keys())
        target_sorted = sorted(target_verts)
        return {
            t: float(clip_data[str(c)])
            for c, t in zip(clip_verts, target_sorted)
        }

    if clip_count > 1 and target_count > 1 and clip_count != target_count:
        raise ValueError(
            f"Cannot paste: clipboard has {clip_count} vertices "
            f"but {target_count} are selected. Select exactly "
            f"{clip_count}, exactly 1 (blend), or no vertices "
            f"(whole-mesh broadcast)."
        )

    if clip_count > 1 and clip_count == target_count and not same_mesh:
        raise ValueError(
            f"Cannot paste {clip_count}-to-{target_count} across different "
            f"meshes. Use broadcast (select 1 target) or blend (select no "
            f"vertices)."
        )

    raise ValueError(
        f"Unhandled paste resolution: clip={clip_count}, "
        f"target={target_count}, same_mesh={same_mesh}"
    )



def validate_bone_compatibility(
    clip_data: dict,
    target_vg_names: set[str],
    clip_kind: str,
) -> tuple[bool, str]:
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



def vertices_with_mask_override(mask_dict: dict) -> set[int]:
    return {int(v) for v in mask_dict.keys()}


def vertices_with_weight(layer_dict: dict, bone_name: str) -> set[int]:
    result: set[int] = set()
    for v_str, weights in layer_dict.items():
        w = weights.get(bone_name, 0.0)
        if w > 0.001:
            result.add(int(v_str))
    return result
