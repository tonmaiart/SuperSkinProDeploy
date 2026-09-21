"""Shared "Pose" / "Weight" toggle buttons + "Edit Mask" widget."""


def _is_editing(obj) -> bool:
    """Shared `is_edit` resolution for `draw_mode_edit_toggle()` and `draw_edit_mask_button()`."""
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


def draw_mode_edit_toggle(
    row,
    context,
    *,
    pose_toggle_idname: str = "object.mw_force_pose_mode",
    object_exit_idname: str = "superskin.save_weights",
    enter_edit_idname: str = "object.mw_toggle_edit_mode",
    enter_edit_text: str = "Edit Bone",
    edit_mask_idname: str = None,
    edit_mask_text: str = "Mask",
    edit_mask_enter_text: str = None,
    show_edit_mask: bool = True,
    target_mesh_obj=None,
) -> None:
    """Draw a Pose button and a separate Weight button, plus an optional Mask button."""
    obj = context.active_object
    is_edit = _is_editing(obj)
    is_pose = bool(obj and obj.type == 'ARMATURE' and obj.mode == 'POSE')

    # Locked until the target mesh is bound to an armature; never locks an active session.
    unlocked = is_edit or is_pose or any(
        _has_armature(o) for o in (obj, target_mesh_obj)
    )

    pose_row = row.row(align=True)
    pose_row.enabled = unlocked
    pose_row.operator(pose_toggle_idname, text="Pose", depress=is_pose)

    if is_edit:
        row.operator(object_exit_idname, text="Weight", depress=True)
    else:
        weight_row = row.row(align=True)
        weight_row.enabled = unlocked
        edit_props = weight_row.operator(enter_edit_idname, text="Weight", depress=False)
        if target_mesh_obj is not None and hasattr(edit_props, 'mesh_name'):
            edit_props.mesh_name = target_mesh_obj.name

    if edit_mask_idname and show_edit_mask:
        if is_edit:
            # is_edit already guarantees obj is the Mesh itself.
            mask_label = edit_mask_text
            mask_depress = bool(obj) and obj.superskin_storage.active_is_mask
        else:
            mask_label = edit_mask_enter_text or edit_mask_text
            mask_depress = False

        from .icons import get_layer_mask_icon_id
        mask_props = row.operator(
            edit_mask_idname, text=mask_label, icon_value=get_layer_mask_icon_id(),
            depress=mask_depress,
        )
        mask_props.enter_edit_idname = enter_edit_idname
        mask_props.target = 'TOGGLE'


def draw_edit_mask_button(
    layout,
    context,
    *,
    enter_edit_idname: str = "object.mw_toggle_edit_mode",
    edit_mask_idname: str = "superskin.toggle_mask_mode",
    edit_mask_text: str = "Mask",
) -> None:
    """Draw a single, standalone "Edit Mask" button."""
    obj = context.active_object
    if not _is_editing(obj):
        return

    from .icons import get_layer_mask_icon_id
    mask_props = layout.operator(
        edit_mask_idname, text=edit_mask_text, icon_value=get_layer_mask_icon_id(),
        depress=bool(obj) and obj.superskin_storage.active_is_mask,
    )
    mask_props.enter_edit_idname = enter_edit_idname
    mask_props.target = 'TOGGLE'
