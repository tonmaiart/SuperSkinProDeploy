
import bpy

from ...core.facade import CoreFacade
from .brush_tool import BRUSH_ENABLED
from .brush_tool.brush_tool import SuperSkinWeightBrushTool
from .vertex_tool.brush_isolate import reveal_all as reveal_brush_hidden
from .vertex_tool.select_tool import SuperSkinWeightSelectTool

_NATIVE_WEIGHT_TOOL_IDNAME = "builtin.brush"

_CONDITIONAL_SELECT_IDNAMES = frozenset((
    "builtin.select", "builtin.select_box", "builtin.select_circle", "builtin.select_lasso",
))

_last_addon_tool_idname = None


def _tool_classes():
    if BRUSH_ENABLED:
        return (SuperSkinWeightBrushTool, SuperSkinWeightSelectTool)
    return (SuperSkinWeightSelectTool,)


def _addon_tool_idnames():
    return {cls.bl_idname for cls in _tool_classes()}


def _tag_view3d_redraw():
    wm = bpy.context.window_manager
    if wm is None:
        return
    CoreFacade.tag_redraw_areas(window_manager=wm)


def last_weight_tool_idname():
    return _last_addon_tool_idname


def show_weight_tools():
    for i, cls in enumerate(_tool_classes()):
        if not hasattr(cls, "_bl_tool"):
            bpy.utils.register_tool(cls, separator=(i == 0))
    _tag_view3d_redraw()


def reset_stale_weight_tool():
    global _last_addon_tool_idname
    wm = bpy.context.window_manager
    if wm is None:
        return
    idnames = _addon_tool_idnames()
    for window in wm.windows:
        workspace = window.workspace
        tool = workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False) if workspace else None
        if tool is None or tool.idname not in idnames | _CONDITIONAL_SELECT_IDNAMES:
            continue
        if tool.idname in idnames:
            _last_addon_tool_idname = tool.idname
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    if bpy.context.mode != 'PAINT_WEIGHT':
                        break
                    bpy.ops.wm.tool_set_by_id(name=_NATIVE_WEIGHT_TOOL_IDNAME)
                break
            except Exception:
                continue


def _has_stale_weight_tool():
    idnames = _addon_tool_idnames()
    for workspace in bpy.data.workspaces:
        tool = workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
        if tool is not None and tool.idname in idnames:
            return True
    return False


def hide_weight_tools(force=False):
    try:
        reveal_brush_hidden()
    except Exception:
        pass
    try:
        reset_stale_weight_tool()
    except Exception:
        pass
    if not force and _has_stale_weight_tool():
        return
    for cls in reversed(_tool_classes()):
        if hasattr(cls, "_bl_tool"):
            bpy.utils.unregister_tool(cls)
    _tag_view3d_redraw()


def _sync_registration():
    try:
        in_session = CoreFacade.is_editing_weights()
    except Exception:
        in_session = False
    try:
        stale = _has_stale_weight_tool()
    except Exception:
        stale = False
    if in_session or stale:
        show_weight_tools()
    return None


@bpy.app.handlers.persistent
def _sync_registration_load_post(*_):
    bpy.app.timers.register(_sync_registration, first_interval=0.0)


def tool_keymap_name(cls):
    return f"3D View Tool: {cls.bl_context_mode.replace('_', ' ').title()}, {cls.bl_label}"


def _drop_tool_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    for cls in _tool_classes():
        km = kc.keymaps.get(tool_keymap_name(cls))
        if km is not None:
            kc.keymaps.remove(km)


def register():
    _drop_tool_keymaps()
    _sync_registration()
    bpy.app.handlers.load_post.append(_sync_registration_load_post)


def unregister():
    if _sync_registration_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_sync_registration_load_post)
    hide_weight_tools(force=True)
    _drop_tool_keymaps()
