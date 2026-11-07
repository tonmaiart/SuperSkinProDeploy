

def _is_editing(obj) -> bool:
    from ...core.facade import CoreFacade
    return bool(
        obj and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
        and CoreFacade.is_editing_weights()
    )


def _has_armature(obj) -> bool:
    if obj is None:
        return False
    if obj.type == 'ARMATURE':
        return True
    return obj.type == 'MESH' and any(
        m.type == 'ARMATURE' and m.object for m in obj.modifiers
    )


def draw_mode_tool_row(
    row,
    context,
    *,
    brush_tool_idname: str,
    lasso_tool_idname: str,
    show_brush: bool = True,
    mode_tool_idname: str = "object.mw_mode_tool",
    target_mesh_obj=None,
) -> None:
    from .icons import get_object_mode_icon_id, get_tool_brush_icon_id, get_tool_lasso_icon_id
    obj = context.active_object
    is_edit = _is_editing(obj)
    is_posing = bool(obj and obj.type == 'ARMATURE' and obj.mode == 'POSE')
    active_tool = None
    if is_edit and context.workspace is not None:
        tool = context.workspace.tools.from_space_view3d_mode('PAINT_WEIGHT', create=False)
        active_tool = tool.idname if tool else None

    is_object = obj is None or obj.mode == 'OBJECT'
    entries = [('OBJECT', get_object_mode_icon_id(), 'OBJECT_DATAMODE', is_object)]
    if show_brush:
        entries.append(('BRUSH', get_tool_brush_icon_id(), 'BRUSH_DATA',
                        active_tool == brush_tool_idname))
    entries.append(('VERTEX', get_tool_lasso_icon_id(), 'VERTEXSEL',
                    active_tool == lasso_tool_idname))
    entries.append(('ARMATURE', 0, 'ARMATURE_DATA', is_posing))

    pair = row.row(align=True)
    pair.scale_x = 1.8
    pair.enabled = is_edit or is_posing or any(
        _has_armature(o) for o in (obj, target_mesh_obj)
    )
    for target, icon_id, fallback, pressed in entries:
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": fallback}
        props = pair.operator(mode_tool_idname, text="", depress=pressed, **icon_kwargs)
        props.target = target
        if target_mesh_obj is not None:
            props.mesh_name = target_mesh_obj.name


def draw_edit_mask_button(
    layout,
    context,
    *,
    enter_edit_idname: str = "object.mw_toggle_edit_mode",
    edit_mask_idname: str = "superskin.toggle_mask_mode",
    disabled: bool = False,
) -> None:
    obj = context.active_object
    if not _is_editing(obj):
        return

    is_mask = bool(obj) and obj.superskin_storage.active_is_mask

    pair = layout.row(align=True)
    pair.alignment = 'RIGHT'
    pair.enabled = not disabled
    for text, target, active in (
        ("Bone", 'WEIGHT', not is_mask),
        ("Mask", 'MASK', is_mask),
    ):
        sub_props = pair.operator(edit_mask_idname, text=text, depress=active)
        sub_props.enter_edit_idname = enter_edit_idname
        sub_props.target = target
