
import math
import time

import bpy
import blf
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader

from . import bone_axis as _bone_axis
from . import supersample as _supersample

_draw_handle = None
_cursor_draw_handle = None
_hovered_bone: str | None = None
_highlight_hover = False
_hover_mouse_pos: tuple | None = None
_is_holding: bool = False
_cursor_badge: str | None = None
_lasso_points: list | None = None

_preview_active: bool = False
_preview_token: int = 0
_PREVIEW_DURATION = 1.2
_LIST_PREVIEW_DURATION = 3

_RELEASE_DELAY = 0.8
_FADE_DURATION = 0.1
_FADE_STEP     = 1.0 / 60.0

_lingering: bool = False
_fade_alpha: float = 1.0
_fade_token: int = 0

HUD_LOCK_PROP = "superskin_bone_hud_lock"

_NO_OVERRIDE = object()
_active_override = _NO_OVERRIDE

_COLOR_ACTIVE       = (1.0, 1.0, 0.00784313725490196, 1.0)
_COLOR_MULTI        = (0.0, 1.0, 0.00392156862745098, 0.9019607843137255)
_COLOR_DEFAULT      = (0.6, 0.7137254901960784, 0.7843137254901961, 1.0)
_COLOR_NO_INFLUENCE = (0.7019607843137254, 0.7019607843137254, 0.7019607843137254, 1.0)

_WEDGE_WIDTH        = 10
_LINE_WIDTH         = 2
_HOVER_EXTRA_WIDTH  = 6
_HOLD_EXTRA_WIDTH   = 0
_PIVOT_RATIO        = 0.1
_FILL_OPACITY       = 0.6
_HEAD_CIRCLE_SIZE   = 1.2
_MIN_PICK_SIZE      = 1.0
_MIN_WEDGE_PX       = 1.0
_STROKE_DARKEN      = 0.4
_HOVER_BRIGHTEN     = 0.35
_DEPTH_MIN_BRIGHTNESS = 1

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

_LASSO_COLOR         = (1.0, 1.0, 1.0, 1.0)
_LASSO_OUTLINE_COLOR = (0.0, 0.0, 0.0, 1.0)


def _get_overall_size() -> float:
    try:
        return bpy.context.window_manager.superskin_bone_picker_prefs.overall_size
    except Exception:
        return 1.0


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
CATEGORY_COLOR_PROPS = ("active_color", "selected_color", "influenced_color", "uninfluenced_color")
CATEGORY_COLOR_DEFAULTS = dict(zip(CATEGORY_COLOR_PROPS, _CAT_COLORS))


def _category_colors():
    try:
        prefs = bpy.context.window_manager.superskin_bone_picker_prefs
        return [tuple(getattr(prefs, prop)) for prop in CATEGORY_COLOR_PROPS]
    except Exception:
        return _CAT_COLORS


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


def _view_depth(view_matrix, pts):
    return -(np.c_[pts, np.ones(len(pts), dtype=np.float32)] @ view_matrix[2])


def _stroke_quads(a, b, half):
    seg = b - a
    length = np.linalg.norm(seg, axis=-1, keepdims=True)
    d = np.where(length > 0.0, seg / np.where(length > 0.0, length, 1.0), 0.0) * half
    n = np.stack((-d[..., 1], d[..., 0]), axis=-1)
    a2, b2 = a - d, b + d
    return np.stack((a2 + n, a2 - n, b2 + n, a2 - n, b2 - n, b2 + n), axis=-2)


def _bone_geometry(heads, tails, head_r, half_width):
    seg = tails - heads
    seg_len = np.linalg.norm(seg, axis=1)
    has_wedge = seg_len > _MIN_WEDGE_PX
    dir_n = seg / np.where(seg_len > 0.0, seg_len, 1.0)[:, None]
    start = heads
    perp = np.stack((-dir_n[:, 1], dir_n[:, 0]), axis=1) * np.reshape(half_width, (-1, 1))
    pivot = start + dir_n * (seg_len * _PIVOT_RATIO)[:, None]
    pl = pivot + perp
    pr = pivot - perp
    wedge_fill = np.stack((start, pl, tails, start, tails, pr), axis=1)
    wedge_lines = np.stack((start, pl, pl, tails, tails, pr, pr, start), axis=1)

    ring = heads[:, None, :] + _UNIT_CIRCLE[None, :, :] * np.reshape(head_r, (-1, 1, 1))
    prev = np.roll(ring, 1, axis=1)
    center = np.broadcast_to(heads[:, None, :], ring.shape)
    circle_fill = np.stack((center, prev, ring), axis=2)
    circle_lines = np.stack((prev, ring), axis=2)
    return has_wedge, wedge_fill, wedge_lines, circle_fill, circle_lines


def _cross2d(u, v):
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def bone_shape_hits(heads, tails, point):
    if not len(heads):
        return np.zeros(0, dtype=bool)
    eff_width = _WEDGE_WIDTH * max(_get_overall_size(), _MIN_PICK_SIZE)
    head_r = eff_width * _HEAD_CIRCLE_SIZE
    has_wedge, wedge_fill, _, _, _ = _bone_geometry(heads, tails, head_r, eff_width)
    p = np.asarray(point, dtype=np.float32)

    in_circle = ~has_wedge & (np.linalg.norm(heads - p, axis=1) <= head_r)

    tris = wedge_fill.reshape(-1, 2, 3, 2)
    a, b, c = tris[:, :, 0], tris[:, :, 1], tris[:, :, 2]
    d1 = _cross2d(b - a, p - a)
    d2 = _cross2d(c - b, p - b)
    d3 = _cross2d(a - c, p - c)
    has_neg = (d1 < 0) | (d2 < 0) | (d3 < 0)
    has_pos = (d1 > 0) | (d2 > 0) | (d3 > 0)
    in_wedge = has_wedge & (~(has_neg & has_pos)).any(axis=1)
    return in_circle | in_wedge


def _bone_colors(group, depth, near, far, alpha):
    hovered = (group & 1).astype(bool)
    cats = group >> 1
    base = np.array(_category_colors(), dtype=np.float32)[cats]
    base[hovered, :3] += (1.0 - base[hovered, :3]) * _HOVER_BRIGHTEN
    t = np.clip((depth - near) / (far - near), 0.0, 1.0) if far > near else np.zeros_like(depth)
    dim = np.where(hovered | ((cats != _CAT_DEFAULT) & (cats != _CAT_NO_INFLUENCE)),
                   1.0, 1.0 - t * (1.0 - _DEPTH_MIN_BRIGHTNESS))
    base[:, :3] *= dim[:, None]
    base[:, 3] *= alpha
    fill_col = base.copy()
    fill_col[:, 3] *= _FILL_OPACITY
    outline = base.copy()
    outline[:, :3] *= _STROKE_DARKEN
    stroke_col = np.where(hovered[:, None], base, outline)
    return fill_col, stroke_col, hovered


def _draw_skeleton(region, rv3d, armature, vg_names, deform_bones,
                   active_name, pool_names, influenced_bones, alpha):
    pose_bones = armature.pose.bones
    names = pose_bones.keys()
    keep = [i for i, name in enumerate(names) if name in deform_bones and name in vg_names]
    if not keep:
        return

    head_pts = _pose_points(pose_bones, "head")[keep]
    tail_pts = _bone_axis.display_tails(armature, pose_bones)
    if tail_pts is None:
        tail_pts = _pose_points(pose_bones, "tail")
    tail_pts = tail_pts[keep]

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
        group[j] = cat * 2 + (1 if _highlight_hover and name == _hovered_bone else 0)

    view = np.array(rv3d.view_matrix, dtype=np.float32) @ np.array(armature.matrix_world, dtype=np.float32)
    ends = np.concatenate((head_pts, tail_pts))
    all_depth = _view_depth(view, ends)
    near, far = float(all_depth.min()), float(all_depth.max())
    mids = (head_pts + tail_pts) * 0.5
    depth = _view_depth(view, mids)

    matrix = np.array(rv3d.perspective_matrix, dtype=np.float32) @ np.array(armature.matrix_world, dtype=np.float32)
    heads, head_ok = _project_to_region(region, matrix, head_pts)
    tails, tail_ok = _project_to_region(region, matrix, tail_pts)
    visible = head_ok & tail_ok
    if not visible.any():
        return
    heads, tails, group, depth = heads[visible], tails[visible], group[visible], depth[visible]
    fill_col, stroke_col, hovered = _bone_colors(group, depth, near, far, alpha)

    eff_width = _WEDGE_WIDTH * _get_overall_size()
    head_r = eff_width * _HEAD_CIRCLE_SIZE
    has_wedge, wedge_fill, wedge_lines, circle_fill, circle_lines = _bone_geometry(heads, tails, head_r, eff_width)
    n = len(heads)
    wedge_fill = np.where(has_wedge[:, None, None], wedge_fill, heads[:, None, :])
    wedge_lines = np.where(has_wedge[:, None, None], wedge_lines, heads[:, None, :])
    circle_fill = np.where(has_wedge[:, None, None, None], heads[:, None, None, :], circle_fill)
    circle_lines = np.where(has_wedge[:, None, None, None], heads[:, None, None, :], circle_lines)
    half = (_LINE_WIDTH + np.where(hovered, _HOVER_EXTRA_WIDTH, 0) + _HOLD_EXTRA_WIDTH) * 0.5

    fills = np.concatenate((circle_fill.reshape(n, -1, 2), wedge_fill.reshape(n, -1, 2)), axis=1)
    segs = np.concatenate((wedge_lines.reshape(n, -1, 2, 2), circle_lines.reshape(n, -1, 2, 2)), axis=1)
    strokes = _stroke_quads(segs[:, :, 0], segs[:, :, 1], half[:, None, None]).reshape(n, -1, 2)
    nf, ns = fills.shape[1], strokes.shape[1]
    pos = np.concatenate((fills, strokes), axis=1)
    col = np.concatenate((np.repeat(fill_col[:, None], nf, axis=1), np.repeat(stroke_col[:, None], ns, axis=1)), axis=1)

    order = np.lexsort((-depth, hovered))
    _supersample.render_flat(region, pos[order].reshape(-1, 2), col[order].reshape(-1, 4))


def _overlay_target():
    locked = getattr(bpy.context.window_manager, HUD_LOCK_PROP, False)
    if not (_is_holding or _preview_active or _lingering or locked):
        return None
    alpha = 1.0 if (_is_holding or _preview_active or locked) else _fade_alpha
    context = bpy.context
    obj = context.active_object
    if not obj or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return None
    if not context.region or not context.region_data:
        return None

    armature = next((m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)
    if not armature:
        return None

    storage = getattr(obj, "superskin_storage", None)
    if storage is not None and getattr(storage, "active_is_mask", False):
        return None
    return obj, armature, alpha


def _draw_skeleton_callback():
    target = _overlay_target()
    if target is None:
        return
    obj, armature, alpha = target
    context = bpy.context

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

    gpu.state.blend_set('ALPHA')
    _draw_skeleton(context.region, context.region_data, armature, vg_names, deform_bones,
                   active_name, pool_names, influenced_bones, alpha)
    gpu.state.blend_set('NONE')


def _draw_cursor_callback():
    if not _hover_mouse_pos or _overlay_target() is None:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    if _lasso_points and len(_lasso_points) > 1:
        _draw_lasso(shader, _lasso_points)
    _draw_arrow_cursor(shader, _hover_mouse_pos)
    if _cursor_badge:
        _draw_cursor_badge(shader, _hover_mouse_pos, _cursor_badge)

    if _hovered_bone:
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


def _draw_lasso(shader, points):
    loop = list(points) + [points[0]]
    gpu.state.line_width_set(3.0)
    shader.uniform_float("color", _LASSO_OUTLINE_COLOR)
    batch_for_shader(shader, 'LINE_STRIP', {"pos": loop}).draw(shader)
    gpu.state.line_width_set(1.0)
    shader.uniform_float("color", _LASSO_COLOR)
    batch_for_shader(shader, 'LINE_STRIP', {"pos": loop}).draw(shader)


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



def set_hover(bone_name: str | None, mouse_pos: tuple | None = None, highlight: bool = True) -> None:
    global _hovered_bone, _hover_mouse_pos, _highlight_hover
    _hovered_bone = bone_name
    _highlight_hover = highlight
    _hover_mouse_pos = mouse_pos


def clear_hover() -> None:
    global _hovered_bone, _hover_mouse_pos, _cursor_badge, _lasso_points
    _hovered_bone = None
    _hover_mouse_pos = None
    _cursor_badge = None
    _lasso_points = None


def set_lasso(points: list | None) -> None:
    global _lasso_points
    _lasso_points = list(points) if points else None


def set_cursor_badge(symbol: str | None) -> None:
    global _cursor_badge
    _cursor_badge = symbol


def set_active_override(bone_name: str | None) -> None:
    global _active_override
    _active_override = bone_name


def clear_active_override() -> None:
    global _active_override
    _active_override = _NO_OVERRIDE


def set_holding(state: bool, fade: bool = True) -> None:
    global _is_holding
    _is_holding = state
    if state or not fade:
        _cancel_fade()
    else:
        _begin_fade(_RELEASE_DELAY)

def start_preview(duration: float = _PREVIEW_DURATION) -> None:
    global _preview_active, _preview_token
    _cancel_fade()
    _preview_active = True
    _preview_token += 1
    token = _preview_token
    bpy.app.timers.register(lambda: _end_preview(token), first_interval=duration)
    _tag_redraw()


def show_list_preview() -> None:
    start_preview(_LIST_PREVIEW_DURATION)


def _end_preview(token: int) -> None:
    global _preview_active
    if token == _preview_token:
        _preview_active = False
        if not _is_holding:
            _begin_fade(0.0)
    return None


def _cancel_fade() -> None:
    global _lingering, _fade_alpha, _fade_token
    _fade_token += 1
    _lingering = False
    _fade_alpha = 1.0


def _begin_fade(delay: float) -> None:
    global _lingering, _fade_alpha, _fade_token
    _fade_token += 1
    token = _fade_token
    _lingering = True
    _fade_alpha = 1.0
    start = time.perf_counter() + delay
    bpy.app.timers.register(lambda: _fade_tick(token, start), first_interval=max(delay, 0.0))
    _tag_view3d_redraw()


def _fade_tick(token: int, start: float):
    global _lingering, _fade_alpha
    if token != _fade_token:
        return None
    t = (time.perf_counter() - start) / _FADE_DURATION
    if t >= 1.0:
        _lingering = False
        _fade_alpha = 1.0
        _tag_view3d_redraw()
        return None
    _fade_alpha = 1.0 - max(t, 0.0)
    _tag_view3d_redraw()
    return _FADE_STEP


def _tag_view3d_redraw() -> None:
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _on_hud_lock_changed(self, context):
    _tag_view3d_redraw()


def register_props():
    setattr(bpy.types.WindowManager, HUD_LOCK_PROP, bpy.props.BoolProperty(
        name="Lock Bone HUD",
        description="Keep the deform bone overlay visible in Edit Layer Weight, not only while Alt+2 is held",
        default=False,
        options={'SKIP_SAVE'},
        update=_on_hud_lock_changed,
    ))


def unregister_props():
    if hasattr(bpy.types.WindowManager, HUD_LOCK_PROP):
        delattr(bpy.types.WindowManager, HUD_LOCK_PROP)


def show():
    global _draw_handle, _cursor_draw_handle
    if _draw_handle is not None:
        return
    _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_skeleton_callback, (), 'WINDOW', 'POST_VIEW'
    )
    _cursor_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw_cursor_callback, (), 'WINDOW', 'POST_PIXEL'
    )
    _tag_redraw()


def hide():
    global _draw_handle, _cursor_draw_handle
    if _draw_handle is None and _cursor_draw_handle is None:
        return
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None
    if _cursor_draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_cursor_draw_handle, 'WINDOW')
        _cursor_draw_handle = None
    _tag_redraw()


def cleanup():
    global _is_holding, _preview_active
    hide()
    _bone_axis.clear_cache()
    _supersample.free()
    _cancel_fade()
    _is_holding = False
    _preview_active = False


def _tag_redraw():
    from ...core.facade import CoreFacade
    CoreFacade.tag_redraw_areas()
