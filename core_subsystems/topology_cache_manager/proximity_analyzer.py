
from ..rust_weight_engine import RustWeightEngine


class ProximityAnalyzer:
    """Stateless bone geometry analyzer delegating heavy math to the Rust core."""

    @classmethod
    def _harvest_bone_raw_data(cls, arm_obj, deform_bone_names) -> list:
        names = list(deform_bone_names)
        bone_raw_data = []
        for name in names:
            bone = arm_obj.data.bones.get(name)
            if bone:
                h = bone.head_local
                t = bone.tail_local
                bone_raw_data.append((name, (h.x, h.y, h.z), (t.x, t.y, t.z)))
        return bone_raw_data

    @classmethod
    def compute_bone_display_order(cls, arm_obj, deform_bone_names) -> list[str]:
        if not arm_obj or not deform_bone_names:
            return list(deform_bone_names)

        bone_raw_data = cls._harvest_bone_raw_data(arm_obj, deform_bone_names)
        if len(bone_raw_data) < 2:
            return [item[0] for item in bone_raw_data]

        rust = RustWeightEngine("bone_display_order")
        return rust.call("rust_compute_bone_display_order", bone_raw_data)
