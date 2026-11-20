
import bpy

_ON_POSE_BONE = "select" in bpy.types.PoseBone.bl_rna.properties


def _owner(pose_bone):
    return pose_bone if _ON_POSE_BONE else pose_bone.bone


def is_selected(pose_bone) -> bool:
    return _owner(pose_bone).select


def set_selected(pose_bone, selected: bool) -> None:
    owner = _owner(pose_bone)
    if owner.select != selected:
        owner.select = selected


def is_hidden(pose_bone) -> bool:
    return _owner(pose_bone).hide
