
import fnmatch

import bpy

from ...core.facade import CoreFacade

KEYMAP_NAME = 'Object Mode'
ADDON_TOOLS = "superskin.*"


def active_tool_idname(context) -> str:
    workspace = getattr(context, "workspace", None)
    if workspace is None:
        return ""
    try:
        tool = workspace.tools.from_space_view3d_mode(CoreFacade.SESSION_CONTEXT_MODE, create=False)
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


_BLOCKED_KEYMAPS = ('Object Mode', 'Object Non-modal')
_UNBLOCKED_TYPES = frozenset(('TIMER', 'TEXTINPUT', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'))
_blocks = []


def block_native(idname):
    unblock_native()
    wm = bpy.context.window_manager
    user = wm.keyconfigs.user if wm is not None else None
    addon = wm.keyconfigs.addon if wm is not None else None
    if user is None or addon is None:
        return
    for name in _BLOCKED_KEYMAPS:
        native = user.keymaps.get(name)
        if native is None:
            continue
        km = addon.keymaps.new(name=name, space_type=native.space_type, region_type=native.region_type)
        own = {(k.type, k.shift, k.ctrl, k.alt, k.oskey) for k in km.keymap_items
               if k.idname != idname}
        seen = set()
        for kmi in native.keymap_items:
            if (not kmi.active or kmi.idname.startswith("superskin.")
                    or kmi.map_type in {'TIMER', 'NDOF', 'TEXTINPUT'} or kmi.type in _UNBLOCKED_TYPES):
                continue
            for mods in _modifier_sets(kmi):
                key = (kmi.type, *mods)
                if key in own or (key, kmi.value, kmi.key_modifier, kmi.direction) in seen:
                    continue
                seen.add((key, kmi.value, kmi.key_modifier, kmi.direction))
                shift, ctrl, alt, oskey = mods
                try:
                    item = km.keymap_items.new(
                        idname, kmi.type, kmi.value, shift=shift, ctrl=ctrl, alt=alt, oskey=oskey,
                        key_modifier=kmi.key_modifier, direction=kmi.direction)
                except Exception:
                    continue
                _blocks.append((km, item))


def _modifier_sets(kmi):
    if kmi.any:
        return [(s, c, a, 0) for s in (0, 1) for c in (0, 1) for a in (0, 1)]
    return [tuple(int(v) for v in (kmi.shift, kmi.ctrl, kmi.alt, kmi.oskey))]


def unblock_native():
    for km, kmi in _blocks:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _blocks.clear()


def observe(idname, **key):
    km = _keymap()
    if km is None:
        return None
    return km, km.keymap_items.new(idname, **key)
