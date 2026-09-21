"""Tool operators for SuperSkinPro — fast timeline scrub."""

import bpy


# ==============================================================================
# FAST TIMELINE SCRUB (Alt+Shift+Scroll)
# ==============================================================================

class SUPERSKIN_OT_scrub_timeline_fast(bpy.types.Operator):
    """Step the current frame by 5 at a time per scroll notch, for faster timeline scrubbing."""
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
