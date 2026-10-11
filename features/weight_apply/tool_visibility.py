
import bpy

from ...core.facade import CoreFacade
from .brush_tool import BRUSH_ENABLED
from .brush_tool.brush_tool import SuperSkinWeightBrushTool
from .vertex_tool.select_tool import SuperSkinWeightSelectTool

_NATIVE_WEIGHT_TOOL_IDNAME = "builtin.brush"

_CONDITIONAL_SELECT_IDNAMES = frozenset((
    "builtin.select", "builtin.select_box", "builtin.select_circle", "builtin.select_lasso",
))

_last_addon_tool_idname = None


def _tool_classes():
    if BRUSH_ENABLED:
        return (SuperSkinWeightSelectTool, SuperSkinWeightBrushTool)
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
    for cls in _tool_classes():
        if not hasattr(cls, "_bl_tool"):
            bpy.utils.register_tool(cls, after={"builtin.transform"}, separator=True)
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


def hide_weight_tools():
    try:
        reset_stale_weight_tool()
    except Exception:
        pass
    for cls in reversed(_tool_classes()):
        if hasattr(cls, "_bl_tool"):
            bpy.utils.unregister_tool(cls)
    _tag_view3d_redraw()


def register():
    try:
        in_session = CoreFacade.is_editing_weights()
    except Exception:
        in_session = False
    if in_session:
        show_weight_tools()


def unregister():
    hide_weight_tools()
