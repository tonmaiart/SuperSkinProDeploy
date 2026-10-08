
import uuid


def get_or_create_bone_id(bone) -> str:
    existing = bone.get("ss_uuid")
    if existing:
        return existing
    new_id = uuid.uuid4().hex
    bone["ss_uuid"] = new_id
    return new_id


def resolve_bone_by_uuid(arm_obj, bone_uuid: str):
    if not arm_obj or not bone_uuid:
        return None
    for bone in arm_obj.data.bones:
        if bone.get("ss_uuid") == bone_uuid:
            return bone
    return None
