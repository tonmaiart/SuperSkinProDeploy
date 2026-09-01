"""ProximityAnalyzer -- Rust-accelerated bone proximity and display-order computation.

All methods are classmethods; the class is never instantiated.
"""

from ..rust_weight_engine import RustWeightEngine


class ProximityAnalyzer:
    """Stateless bone geometry analyzer delegating heavy math to the Rust core."""

    @classmethod
    def _harvest_bone_raw_data(cls, arm_obj, deform_bone_names) -> list:
        """Extract head/tail coordinates for each named bone.

        Args:
            arm_obj: bpy.types.Object of type ARMATURE.
            deform_bone_names: Iterable of bone name strings.

        Returns:
            List of ``(name, (hx, hy, hz), (tx, ty, tz))`` tuples for bones
            that exist in *arm_obj*. Absent names are silently skipped.
        """
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
        """Return *deform_bone_names* sorted by proximity for panel display.

        Delegates the spatial sort to ``RustWeightEngine.call(
        "rust_compute_bone_display_order", bone_raw_data)``.

        Args:
            arm_obj: bpy.types.Object (ARMATURE) supplying bone geometry.
            deform_bone_names: Iterable of vertex-group/bone names to sort.

        Returns:
            Sorted list of bone name strings. Falls back to the original order
            when fewer than two bones are present or *arm_obj* is None.
        """
        if not arm_obj or not deform_bone_names:
            return list(deform_bone_names)

        bone_raw_data = cls._harvest_bone_raw_data(arm_obj, deform_bone_names)
        if len(bone_raw_data) < 2:
            return [item[0] for item in bone_raw_data]

        rust = RustWeightEngine("bone_display_order")
        return rust.call("rust_compute_bone_display_order", bone_raw_data)
