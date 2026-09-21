"""Operator shells for the overlay_color feature domain."""

import bpy
from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified


class SUPERSKIN_OT_toggle_multi_color(bpy.types.Operator):
    """Press Alt+3 to cycle the weight-overlay mode: Single Color -> Multi Color ->
    Single Color (see `multi_color_draw.cycle()`)."""
    bl_idname = "superskin.toggle_multi_color"
    bl_label = "Multi Color Preview (Toggle)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_editing_weights()

    def execute(self, context):
        return run_domain_via_unified(context, "overlay_color", "toggle_multi_color")


class SUPERSKIN_OT_toggle_multi_color_mask(bpy.types.Operator):
    """Manual toggle for the multi-Layer MASK color preview (`multi_color_mask_draw.py`)."""
    bl_idname = "superskin.toggle_multi_color_mask"
    bl_label = "Multi Color Mask Preview (Toggle)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import multi_color_mask_draw
        return multi_color_mask_draw.can_toggle()

    def execute(self, context):
        from . import multi_color_mask_draw
        target = multi_color_mask_draw._resolve_target_mesh()
        if target is None:
            return {'CANCELLED'}
        return multi_color_mask_draw._with_target_active(
            target,
            lambda: run_domain_via_unified(context, "overlay_color", "toggle_multi_color_mask"),
        )


class SUPERSKIN_OT_start_multi_color_mask(bpy.types.Operator):
    """Force-start the multi-Layer MASK color preview."""
    bl_idname = "superskin.start_multi_color_mask"
    bl_label = "Multi Color Mask Preview (Start)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import multi_color_mask_draw
        return multi_color_mask_draw.can_toggle()

    def execute(self, context):
        from . import multi_color_mask_draw
        target = multi_color_mask_draw._resolve_target_mesh()
        if target is None:
            return {'CANCELLED'}
        return multi_color_mask_draw._with_target_active(
            target,
            lambda: run_domain_via_unified(context, "overlay_color", "start_multi_color_mask"),
        )


class SUPERSKIN_OT_stop_multi_color_mask(bpy.types.Operator):
    """Force-stop the multi-Layer MASK color preview."""
    bl_idname = "superskin.stop_multi_color_mask"
    bl_label = "Multi Color Mask Preview (Stop)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        from . import multi_color_mask_draw
        return multi_color_mask_draw.is_enabled()

    def execute(self, context):
        from . import multi_color_mask_draw
        target = multi_color_mask_draw._resolve_target_mesh() or multi_color_mask_draw._bound_obj
        if target is None:
            multi_color_mask_draw.stop()
            return {'FINISHED'}
        return multi_color_mask_draw._with_target_active(
            target,
            lambda: run_domain_via_unified(context, "overlay_color", "stop_multi_color_mask"),
        )


_classes = (
    SUPERSKIN_OT_toggle_multi_color,
    SUPERSKIN_OT_toggle_multi_color_mask,
    SUPERSKIN_OT_start_multi_color_mask,
    SUPERSKIN_OT_stop_multi_color_mask,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
