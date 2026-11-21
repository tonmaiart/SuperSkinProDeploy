
import bpy
from ...core.facade import CoreFacade
from ...interface.utils.op_exec import run_domain_via_unified
from ...interface.utils.session_shortcuts import ToolScopedShortcut, blocked


class SUPERSKIN_OT_toggle_multi_color(ToolScopedShortcut, bpy.types.Operator):
    """Show each bone's weight in its own color"""
    bl_idname = "superskin.toggle_multi_color"
    bl_label = "Multi Color Preview (Toggle)"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return CoreFacade.is_skinning_session(context)

    def execute(self, context):
        return run_domain_via_unified(context, "overlay_color", "toggle_multi_color")

    def invoke(self, context, event):
        if blocked(self, context):
            return {'PASS_THROUGH'}
        return self.execute(context)


class SUPERSKIN_OT_toggle_multi_color_mask(bpy.types.Operator):
    """Show each layer's mask in its own color"""
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
    """Turn on the layer mask color preview"""
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
    """Turn off the layer mask color preview"""
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


RAMP_ITEMS = (
    ('influence', "Influence", "Colors of the active bone's weight, from no influence to full"),
    ('mask', "Mask", "Colors of the active layer's mask, from none to full"),
    ('wire', "Wireframe", "Wireframe color over the shown weight or mask"),
)


class SUPERSKIN_OT_reset_color_ramp(bpy.types.Operator):
    """Put this color ramp back to its default colors"""
    bl_idname = "superskin.reset_color_ramp"
    bl_label = "Reset Colors"
    bl_options = {'INTERNAL'}

    ramp: bpy.props.EnumProperty(items=RAMP_ITEMS)

    def execute(self, context):
        from . import ramps
        ramps.reset_ramp(self.ramp)
        return {'FINISHED'}


_classes = (
    SUPERSKIN_OT_toggle_multi_color,
    SUPERSKIN_OT_toggle_multi_color_mask,
    SUPERSKIN_OT_start_multi_color_mask,
    SUPERSKIN_OT_stop_multi_color_mask,
    SUPERSKIN_OT_reset_color_ramp,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
