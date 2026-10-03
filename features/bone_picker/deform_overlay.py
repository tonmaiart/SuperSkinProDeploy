
import math

import bpy
import blf
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

_draw_handle = None
_hovered_bone: str | None = None
_hover_mouse_pos: tuple | None = None
_is_holding: bool = False
_cursor_badge: str | None = None

_preview_active: bool = False
_preview_token: int = 0
_PREVIEW_DURATION = 1.2

_NO_OVERRIDE = object()
_active_override = _NO_OVERRIDE

_COLOR_ACTIVE       = (1.0, 1.0, 0.00784313725490196, 1.0)
_COLOR_MULTI        = (0.0, 1.0, 0.00392156862745098, 0.9019607843137255)
_COLOR_DEFAULT      = (0.6, 0.7137254901960784, 0.7843137254901961, 1.0)
_COLOR_NO_INFLUENCE = (0.7019607843137254, 0.7019607843137254, 0.7019607843137254, 1.0)

_WEDGE_WIDTH        = 5.0
_LINE_WIDTH         = 1
_HOVER_EXTRA_WIDTH  = 6
_HOLD_EXTRA_WIDTH   = 0
_PIVOT_RATIO        = 0.15
_FILL_OPACITY       = 0.5
_HEAD_CIRCLE_SIZE   = 1.2

_CURSOR_LENGTH        = 20
_CURSOR_HALF_WIDTH    = 7
_CURSOR_TILT_DEG      = 25
_CURSOR_COLOR         = (1.0, 1.0, 1.0, 1.0)
_CURSOR_OUTLINE_COLOR = (0.0, 0.0, 0.0, 1.0)

_BADGE_OFFSET        = (13, 11)
_BADGE_RADIUS        = 6
_BADGE_ARM_LEN        = 3.5
_BADGE_BG_COLOR       = (1.0, 1.0, 1.0, 1.0)
_BADGE_BG_OUTLINE     = (0.0, 0.0, 0.0, 1.0)
_BADGE_ADD_COLOR      = (0.1568627450980392, 0.7215686274509804, 0.19215686274509805, 1.0)
_BADGE_REMOVE_COLOR   = (0.8509803921568627, 0.17254901960784313, 0.12941176470588237, 1.0)


def _get_overall_size() -> float:
    try:
        return bpy.context.window_manager.superskin_bone_picker_prefs.overall_size
    except Exception:
        return 1.0


def _brighten(color, amount=0.35):
    r, g, b, a = color
    return (
        r + (1.0 - r) * amount,
        g + (1.0 - g) * amount,
        b + (1.0 - b) * amount,
        a,
    )


def _draw_filled_circle(shader, center, radius, fill_color, outline_color, segments=16):
    cx, cy = center
    verts = [(cx, cy)]
    for i in range(segments):
        a = (2 * math.pi * i) / segments
        verts.append((cx + math.cos(a) * radius, cy + math.sin(a) * radius))
    indices = [(0, i + 1, (i + 1) % segments + 1) for i in range(segments)]
    shader.uniform_float("color", fill_color)
    batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=indices).draw(shader)
    outline_verts = list(verts[1:]) + [verts[1]]
    shader.uniform_float("color", outline_color)
    batch_for_shader(shader, 'LINE_STRIP', {"pos": outline_verts}).draw(shader)


_HEAD_CIRCLE_SEGMENTS = 16
_UNIT_CIRCLE = np.array(
    [(math.cos(2 * math.pi * i / _HEAD_CIRCLE_SEGMENTS), math.sin(2 * math.pi * i / _HEAD_CIRCLE_SEGMENTS))
     for i in range(_HEAD_CIRCLE_SEGMENTS)],
    dtype=np.float32,
)

_CAT_ACTIVE, _CAT_MULTI, _CAT_DEFAULT, _CAT_NO_INFLUENCE = range(4)
_CAT_COLORS = (_COLOR_ACTIVE, _COLOR_MULTI, _COLOR_DEFAULT, _COLOR_NO_INFLUENCE)


def _pose_points(pose_bones, attr):
    flat = np.empty(len(pose_bones) * 3, dtype=np.float32)
    try:
        pose_bones.foreach_get(attr, flat)
    except Exception:
        flat = np.array([c for b in pose_bones for c in getattr(b, attr)], dtype=np.float32)
    return flat.reshape(-1, 3)


def _project_to_region(region, matrix, pts):
    clip = np.c_[pts, np.ones(len(pts), dtype=np.float32)] @ matrix.T
    w = clip[:, 3]
    valid = w > 0.0
    safe_w = np.where(valid, w, 1.0)
    half_w = region.width / 2.0
    half_h = region.height / 2.0
    xy = np.empty((len(pts), 2), dtype=np.float32)
    xy[:, 0] = half_w + half_w * clip[:, 0] / safe_w
    xy[:, 1] = half_h + half_h * clip[:, 1] / safe_w
    return xy, valid


def _bone_geometry(heads, tails, head_r, half_width):
    seg = tails - heads
    seg_len = np.linalg.norm(seg, axis=1)
    has_wedge = seg_len > head_r + 1e-4
    dir_n = seg / np.where(seg_len > 0.0, seg_len, 1.0)[:, None]
    start = heads + dir_n * head_r
    perp = np.stack((-dir_n[:, 1], dir_n[:, 0]), axis=1) * half_width
    pivot = start + dir_n * ((seg_len - head_r) * _PIVOT_RATIO)[:, None]
    pl = pivot + perp
    pr = pivot - perp
    wedge_fill = np.stack((start, pl, tails, start, tails, pr), axis=1)
    wedge_lines = np.stack((start, pl, pl, tails, tails, pr, pr, start), axis=1)

    ring = heads[:, None, :] + _UNIT_CIRCLE[None, :, :] * head_r
    prev = np.roll(ring, 1, axis=1)
    center = np.broadcast_to(heads[:, None, :], ring.shape)
    circle_fill = np.stack((center, prev, ring), axis=2)
    circle_lines = np.stack((prev, ring), axis=2)
    return has_wedge, wedge_fill, wedge_lines, circle_fill, circle_lines


def _draw_skeleton(shader, region, rv3d, armature, vg_names, deform_bones,
                   active_name, pool_names, influenced_bones):
    pose_bones = armature.pose.bones
    names = pose_bones.keys()
    keep = [i for i, name in enumerate(names) if name in deform_bones and name in vg_names]
    if not keep:
        return

    matrix = np.array(rv3d.perspective_matrix, dtype=np.float32) @ np.array(armature.matrix_world, dtype=np.float32)
    heads, head_ok = _project_to_region(region, matrix, _pose_points(pose_bones, "head")[keep])
    tails, tail_ok = _project_to_region(region, matrix, _pose_points(pose_bones, "tail")[keep])
    visible = head_ok & tail_ok
    if not visible.any():
        return

    group = np.empty(len(keep), dtype=np.int8)
    for j, i in enumerate(keep):
        name = names[i]
        if name == active_name:
            cat = _CAT_ACTIVE
        elif name in pool_names:
            cat = _CAT_MULTI
        elif name in influenced_bones:
            cat = _CAT_DEFAULT
        else:
            cat = _CAT_NO_INFLUENCE
        group[j] = cat * 2 + (1 if name == _hovered_bone else 0)

    heads, tails, group = heads[visible], tails[visible], group[visible]
    eff_width = _WEDGE_WIDTH * _get_overall_size()
    head_r = eff_width * _HEAD_CIRCLE_SIZE
    has_wedge, wedge_fill, wedge_lines, circle_fill, circle_lines = _bone_geometry(heads, tails, head_r, eff_width)

    hold_extra = _HOLD_EXTRA_WIDTH
    groups = sorted(np.unique(group).tolist(), key=lambda g: (g & 1, g))
    for g in groups:
        in_group = group == g
        color = _CAT_COLORS[g >> 1]
        hovered = bool(g & 1)
        if hovered:
            color = _brighten(color)
        wedge = in_group & has_wedge
        fill = np.concatenate((wedge_fill[wedge].reshape(-1, 2), circle_fill[in_group].reshape(-1, 2)))
        shader.uniform_float("color", (*color[:3], color[3] * _FILL_OPACITY))
        batch_for_shader(shader, 'TRIS', {"pos": np.ascontiguousarray(fill)}).draw(shader)

        lines = np.concatenate((wedge_lines[wedge].reshape(-1, 2), circle_lines[in_group].reshape(-1, 2)))
        gpu.state.line_width_set(_LINE_WIDTH + (_HOVER_EXTRA_WIDTH if hovered else 0) + hold_extra)
        shader.uniform_float("color", color)
        batch_for_shader(shader, 'LINES', {"pos": np.ascontiguousarray(lines)}).draw(shader)


def _draw_callback():
    if not (_is_holding or _preview_active):
        return
    context = bpy.context
    obj = context.active_object
    if not obj or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return

    region = context.region
    rv3d   = context.region_data
    if not region or not rv3d:
        return

    armature = next((m.object for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if not armature:
        return

    vg_names     = set(obj.vertex_groups.keys())
    deform_bones = {b.name for b in armature.data.bones if b.use_deform}

    storage = getattr(obj, "superskin_storage", None)
    if _active_override is not _NO_OVERRIDE:
        active_name = _active_override
    else:
        active_idx  = getattr(storage, "last_clicked_index", -1) if storage else -1
        active_name = None
        if 0 <= active_idx < len(obj.vertex_groups):
            active_name = obj.vertex_groups[active_idx].name

    try:
        from ...core.facade import CoreFacade
        pool_names = set(CoreFacade(context).get_selected_bones_pool())
    except Exception:
        pool_names = set()

    try:
        from ...interface.utils.utils import _get_visible_influence_bones
        influenced_bones = _get_visible_influence_bones(context, obj)
    except Exception:
        influenced_bones = set()

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    gpu.state.blend_set('ALPHA')

    _draw_skeleton(shader, region, rv3d, armature, vg_names, deform_bones,
                   active_name, pool_names, influenced_bones)

    gpu.state.line_width_set(1)
    gpu.state.blend_set('NONE')

    if _hover_mouse_pos:
        _draw_arrow_cursor(shader, _hover_mouse_pos)
        if _cursor_badge:
            _draw_cursor_badge(shader, _hover_mouse_pos, _cursor_badge)

    if _hovered_bone and _hover_mouse_pos:
        _draw_hover_label(_hover_mouse_pos, _hovered_bone)


_HOVER_LABEL_OUTLINE_OFFSETS = (
    (-1, -1), (0, -1), (1, -1),
    (-1,  0),          (1,  0),
    (-1,  1), (0,  1), (1,  1),
    (-2,  0), (2,  0), (0, -2), (0,  2),
)


def _draw_arrow_cursor(shader, center):
    cx, cy = center
    a = math.radians(_CURSOR_TILT_DEG)
    cos_a, sin_a = math.cos(a), math.sin(a)

    def _rot(x, y):
        return (cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a)

    tip    = _rot(0, 0)
    base_l = _rot(-_CURSOR_HALF_WIDTH, -_CURSOR_LENGTH)
    base_r = _rot(_CURSOR_HALF_WIDTH, -_CURSOR_LENGTH)

    shader.uniform_float("color", _CURSOR_COLOR)
    batch_for_shader(shader, 'TRIS', {"pos": [tip, base_l, base_r]}).draw(shader)

    gpu.state.line_width_set(1.5)
    shader.uniform_float("color", _CURSOR_OUTLINE_COLOR)
    batch_for_shader(shader, 'LINE_STRIP', {"pos": [tip, base_l, base_r, tip]}).draw(shader)


def _draw_cursor_badge(shader, center, symbol):
    cx = center[0] + _BADGE_OFFSET[0]
    cy = center[1] + _BADGE_OFFSET[1]
    _draw_filled_circle(shader, (cx, cy), _BADGE_RADIUS, _BADGE_BG_COLOR, _BADGE_BG_OUTLINE, segments=12)

    color = _BADGE_ADD_COLOR if symbol == 'add' else _BADGE_REMOVE_COLOR
    gpu.state.line_width_set(2.0)
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'LINES', {"pos": [(cx - _BADGE_ARM_LEN, cy), (cx + _BADGE_ARM_LEN, cy)]}).draw(shader)
    if symbol == 'add':
        batch_for_shader(shader, 'LINES', {"pos": [(cx, cy - _BADGE_ARM_LEN), (cx, cy + _BADGE_ARM_LEN)]}).draw(shader)
    gpu.state.line_width_set(1)


def _draw_hover_label(mouse_pos, text):
    font_id = 0
    x, y = mouse_pos
    x += 16
    y += 16
    blf.size(font_id, 13)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.95)
    for dx, dy in _HOVER_LABEL_OUTLINE_OFFSETS:
        blf.position(font_id, x + dx, y + dy, 0)
        blf.draw(font_id, text)
    blf.position(font_id, x, y, 0)
    blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
    blf.draw(font_id, text)



def set_hover(bone_name: str | None, mouse_pos: tuple | None = None) -> None:
    global _hovered_bone, _hover_mouse_pos
    _hovered_bone = bone_name
    _hover_mouse_pos = mouse_pos


def clear_hover() -> None:
    global _hovered_bone, _hover_mouse_pos, _cursor_badge
    _hovered_bone = None
    _hover_mouse_pos = None
    _cursor_badge = None


def set_cursor_badge(symbol: str | None) -> None:
    global _cursor_badge
    _cursor_badge = symbol


def set_active_override(bone_name: str | None) -> None:
    global _active_override
    _active_override = bone_name


def clear_active_override() -> None:
    global _active_override
    _active_override = _NO_OVERRIDE


def set_holding(state: bool) -> None:
    global _is_holding
    _is_holding = state

def start_preview(duration: float = _PREVIEW_DURATION) -> None:
    global _preview_active, _preview_token
    _preview_active = True
    _preview_token += 1
    token = _preview_token
    bpy.app.timers.register(lambda: _end_preview(token), first_interval=duration)
    _tag_redraw()


def _end_preview(token: int) -> None:
    global _preview_active
    if token == _preview_token:
        _preview_active = False
        _tag_redraw()
    return None


def show():
    global _draw_handle
    if _draw_handle is not None:
        return
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_callback, (), 'WINDOW', 'POST_PIXEL'
    )
    _tag_redraw()


def hide():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
        _tag_redraw()


def cleanup():
    global _preview_active
    hide()
    if _is_holding:
        set_holding(False)
    _preview_active = False


def _tag_redraw():
    from ...core.facade import CoreFacade
    CoreFacade.tag_redraw_areas()
