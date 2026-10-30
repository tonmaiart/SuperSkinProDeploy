
import bpy

_BASE_STOPS = (0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0)

STOP_VALUES = {
    "add": _BASE_STOPS,
    "scale": (0.0, 0.25, 0.5, 0.75, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999),
    "smooth": _BASE_STOPS + (2.0, 5.0, 10.0, 25.0, 50.0, 100.0),
    "sharpen": _BASE_STOPS,
}
_ON_STOP = 1e-4


def skip_target(action, amount, direction):
    values = sorted(STOP_VALUES[action])
    if direction > 0:
        return next((v for v in values if v > amount + _ON_STOP), max(values[-1], amount))
    return next((v for v in reversed(values) if v < amount - _ON_STOP), min(values[0], amount))


def step_amount(prefs, action, direction):
    prop = f"{action}_val"
    value = skip_target(action, getattr(prefs, prop), direction)
    setattr(prefs, prop, value)
    return getattr(prefs, prop)



class SUPERSKIN_OT_weight_amount_skip(bpy.types.Operator):
    """Step the amount to the next preset value"""
    bl_idname = "superskin.weight_amount_skip"
    bl_label = "Skip Amount"
    bl_options = {'INTERNAL'}

    action: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    direction: bpy.props.IntProperty(default=1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return "Step Up" if properties.direction > 0 else "Step Down"

    def execute(self, context):
        if self.action not in STOP_VALUES:
            return {'CANCELLED'}
        step_amount(context.window_manager.superskin_weight_apply_prefs, self.action, self.direction)
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()
        return {'FINISHED'}


_classes = (SUPERSKIN_OT_weight_amount_skip,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
