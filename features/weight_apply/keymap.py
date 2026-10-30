
from ...interface.utils import session_shortcuts
from .vertex_tool.common import LASSO_TOOL_IDNAME

_keymaps = []

_GESTURE_BINDINGS = (
    ("add_scale", 'LEFTMOUSE', "Add / Scale"),
    ("smooth_sharpen", 'RIGHTMOUSE', "Smooth / Sharpen"),
)


def register():
    for action, mouse_type, label in _GESTURE_BINDINGS:
        bound = session_shortcuts.bind(
            "superskin.weight_gesture", tool=LASSO_TOOL_IDNAME, properties={"action": action},
            type=mouse_type, value='PRESS', alt=True,
        )
        if bound:
            _keymaps.append((*bound, f"{label} (start normal)"))

    bound = session_shortcuts.bind(
        "superskin.repeat_last_weight_apply", type='R', value='PRESS', shift=True,
    )
    if bound:
        _keymaps.append((*bound, "Repeat Last Weight Apply"))


def unregister():
    for km, kmi, _label in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


def get_registered_keymap_items():
    return list(_keymaps)
