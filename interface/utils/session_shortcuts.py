
import fnmatch

import bpy

KEYMAP_NAME = 'Weight Paint'
ADDON_TOOLS = "superskin.*"


def active_tool_idname(context) -> str:
    workspace = getattr(context, "workspace", None)
    if workspace is None:
        return ""
    try:
        tool = workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
    except Exception:
        return ""
    return tool.idname if tool is not None else ""


def blocked(op, context) -> bool:
    pattern = getattr(op, "shortcut_tool", "")
    return bool(pattern) and not fnmatch.fnmatchcase(active_tool_idname(context), pattern)


class ToolScopedShortcut:
    """Operator mixin for bind(): outside the binding's tools the key passes through to the active tool.
    An operator with its own invoke() must return PASS_THROUGH when blocked() is true, before anything else."""

    shortcut_tool: bpy.props.StringProperty(default="", options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        if blocked(self, context):
            return {'PASS_THROUGH'}
        return self.execute(context)


def _keymap():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return None
    return kc.keymaps.new(name=KEYMAP_NAME, space_type='EMPTY')


def bind(idname, *, tool=ADDON_TOOLS, properties=None, **key):
    km = _keymap()
    if km is None:
        return None
    kmi = km.keymap_items.new(idname, **key)
    kmi.properties.shortcut_tool = tool
    for name, value in (properties or {}).items():
        setattr(kmi.properties, name, value)
    return km, kmi


def observe(idname, **key):
    km = _keymap()
    if km is None:
        return None
    return km, km.keymap_items.new(idname, **key)
