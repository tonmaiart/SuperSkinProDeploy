"""Operator shells for the overlay_color feature domain.

Currently just the Multi Color Preview toggle. The weight/mask ramp editors
(``_ramp_io.py`` + Blender's own native ColorRamp widget) need no operators
of their own — ``template_color_ramp()`` already provides add/remove/drag
UI natively.
"""

import bpy
from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified


class SUPERSKIN_OT_toggle_multi_color(bpy.types.Operator):
    """Press Alt+3 to toggle multi-bone color preview on/off.

    Used to be a hold gesture (press to start, release to stop, via a
    modal operator watching its own trigger key's RELEASE event) — now a
    plain one-shot press-to-toggle, matching the `toggle_multi_color`
    action name that already existed for this (`draw.toggle()` /
    `multi_color_draw.toggle()`) but was previously only reachable by a
    different code path.
    """
    bl_idname = "superskin.toggle_multi_color"
    bl_label = "Multi Color Preview (Toggle)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_editing_weights()

    def execute(self, context):
        return run_domain_via_unified(context, "overlay_color", "toggle_multi_color")


_classes = (SUPERSKIN_OT_toggle_multi_color,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
