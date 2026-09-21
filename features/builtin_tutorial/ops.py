"""Standalone operator for the Built-in Tutorial domain."""

import textwrap

import bpy

from . import logic


class SUPERSKIN_OT_open_builtin_tutorial(bpy.types.Operator):
    """Open the Quick Start Guide -- a centered popup with a step list on
    the left and an image + description panel on the right."""
    bl_idname = "superskin.open_builtin_tutorial"
    bl_label = "SuperSkinPro Quick Start Guide"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        steps = logic.load_steps()
        if steps:
            context.window_manager.superskin_builtin_tutorial_prefs.active_step = logic.get_step_id(steps[0], 0)
        return context.window_manager.invoke_popup(self, width=760)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        steps = logic.load_steps()

        if not steps:
            layout.label(text="No tutorial content found.", icon='ERROR')
            return

        prefs = context.window_manager.superskin_builtin_tutorial_prefs
        active_id = prefs.active_step
        active_step = next(
            (step for idx, step in enumerate(steps) if logic.get_step_id(step, idx) == active_id),
            steps[0],
        )

        split = layout.split(factor=0.32)

        list_col = split.column(align=True)
        for idx, step in enumerate(steps):
            list_col.prop_enum(
                prefs, "active_step", logic.get_step_id(step, idx),
                text=str(step.get("title", f"Step {idx + 1}")),
            )

        info_col = split.column()
        image_box = info_col.box()
        icon_id = logic.get_step_image_icon_id(active_step.get("image"))
        if icon_id is not None:
            image_box.template_icon(icon_value=icon_id, scale=10.0)
        else:
            image_box.label(text="(No image yet)", icon='IMAGE_DATA')
            if active_step.get("image"):
                image_box.label(text=f"Expected file: {active_step['image']}")

        info_col.separator()
        info_col.label(text=str(active_step.get("title", "")), icon='INFO')
        text_col = info_col.column(align=True)
        for line in textwrap.wrap(str(active_step.get("description", "")), width=56):
            text_col.label(text=line)


_classes = [
    SUPERSKIN_OT_open_builtin_tutorial,
]


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
