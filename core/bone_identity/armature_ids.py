"""Stable per-bone identity — a UUID stamped directly on each Armature Bone.

``Bone`` (unlike ``VertexGroup``, which has no ID-property support) accepts
custom properties natively, so the UUID lives on the bone object itself and
survives renames for free — no separate name->UUID map is needed on the
armature side.
"""

import uuid


def get_or_create_bone_id(bone) -> str:
    """Return the bone's stable UUID, stamping a new one if missing."""
    existing = bone.get("ss_uuid")
    if existing:
        return existing
    new_id = uuid.uuid4().hex
    bone["ss_uuid"] = new_id
    return new_id


def resolve_bone_by_uuid(arm_obj, bone_uuid: str):
    """Return the live ``Bone`` carrying *bone_uuid*, or ``None``."""
    if not arm_obj or not bone_uuid:
        return None
    for bone in arm_obj.data.bones:
        if bone.get("ss_uuid") == bone_uuid:
            return bone
    return None
