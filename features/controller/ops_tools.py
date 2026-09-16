"""Tool operators for SuperSkinPro — fast timeline scrub.

OBJECT_OT_mw_copy_skin_weight_maya moved to weight_transfer/ops.py.
WM_OT_set_op_weight_preset moved to features/weight_apply/ops.py.
SUPERSKIN_OT_safe_shrink removed (2026-09-16) — dead code with no
remaining UI/keymap/pie-menu reference, fully superseded by
`features/vertex_selector`'s `grow_shrink_selection` (a boundary-predicting
shrink that avoids collapsing a thin vertex loop to nothing, unlike this
operator's naive "selected count <= 1" guard — see that domain's ops.py).

Relocated from operators/ops_tools.py to features/controller/ (2026-06).
"""

import bpy


# ==============================================================================
# FAST TIMELINE SCRUB (Alt+Shift+Scroll)
# ==============================================================================

class SUPERSKIN_OT_scrub_timeline_fast(bpy.types.Operator):
    """Step the current frame by 5 at a time per scroll notch, for faster
    timeline scrubbing.

    Deliberately NOT built on `screen.keyframe_jump()` (an earlier revision
    called that 3 times instead) -- that native operator only moves to the
    next/previous *existing* keyframe on selected/visible objects, so on a
    scene with no animation (or nothing keyframed currently selected) it is
    a permanent no-op that spams an "No more keyframes to jump to in this
    direction" Info message on every single scroll notch, which reads as
    "scrubbing doesn't work at all." Stepping `scene.frame_current`
    directly works unconditionally, matching plain timeline-scrub
    behavior, regardless of whether any keyframes exist anywhere."""
    bl_idname = "superskin.scrub_timeline_fast"
    bl_label = "Scrub Timeline (Fast, x5)"
    bl_options = {'REGISTER'}  # no UNDO -- moving the playhead isn't a data edit

    next: bpy.props.BoolProperty(
        name="Next", default=True,
        description="Advance forward (True) or backward (False)",
    )

    def execute(self, context):
        scene = context.scene
        step = 5 if self.next else -5
        scene.frame_current = max(
            scene.frame_start, min(scene.frame_end, scene.frame_current + step)
        )
        return {'FINISHED'}


# ==============================================================================
# REGISTRATION
# ==============================================================================

_classes = (
    SUPERSKIN_OT_scrub_timeline_fast,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
