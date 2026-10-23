
import re
import uuid

BONE_ID_KEY = "ss_uuid"

_DUPLICATE_SUFFIX = re.compile(r"\.\d{3,}$")


def _pose(bone) -> list:
    return [round(c, 5) for c in (*bone.head_local, *bone.tail_local)]


def _distance(bone, entry) -> float:
    if not entry or len(entry) != 7:
        return float("inf")
    return sum((a - b) ** 2 for a, b in zip(_pose(bone), entry[1:]))


def _rightful_owner(bones, entry):
    if entry:
        for bone in bones:
            if bone.name == entry[0]:
                return bone
    return min(bones, key=lambda b: (_distance(b, entry), bool(_DUPLICATE_SUFFIX.search(b.name)),
                                     len(b.name), b.name))


def stamp_and_collect(armatures, record: dict) -> dict:
    current = {}
    for arm in armatures:
        by_id = {}
        for bone in arm.data.bones:
            if bone.use_deform:
                by_id.setdefault(bone.get(BONE_ID_KEY) or "", []).append(bone)
        for bone_id, bones in by_id.items():
            if bone_id and len(bones) == 1 and bone_id not in current:
                current[bone_id] = [bones[0].name, *_pose(bones[0])]
                continue
            owner = _rightful_owner(bones, record.get(bone_id)) \
                if bone_id and bone_id not in current else None
            for bone in bones:
                if bone is owner:
                    current[bone_id] = [bone.name, *_pose(bone)]
                    continue
                new_id = uuid.uuid4().hex
                bone[BONE_ID_KEY] = new_id
                current[new_id] = [bone.name, *_pose(bone)]
    return current


def renames_between(record: dict, current: dict) -> dict:
    return {entry[0]: current[bone_id][0] for bone_id, entry in record.items()
            if entry and bone_id in current and current[bone_id][0] != entry[0]}


def merge_renamed(row: dict, renames: dict) -> dict:
    out = {}
    for name, w in row.items():
        name = renames.get(name, name)
        out[name] = min(1.0, out.get(name, 0.0) + w)
    return out


def rename_vertex_groups(obj, renames: dict, deform: frozenset) -> None:
    vgs = obj.vertex_groups
    for old, new in renames.items():
        vg = vgs.get(old)
        if vg is not None and old not in deform and vgs.get(new) is None:
            vg.name = new
