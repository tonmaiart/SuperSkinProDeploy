"""Deform Bone Skeleton Overlay."""

import math

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils
from mathutils import Vector

_draw_handle = None
_hovered_bone: str | None = None
_hover_mouse_pos: tuple | None = None
_is_holding: bool = False
_cursor_badge: str | None = None

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

_HUD_LABEL = "BONE PICKER"
_HUD_COLOR = (1.0, 0.8, 0.0, 1.0)  # yellow
_HUD_SLOT = 2


def _get_overall_size() -> float:
    """The one remaining live/adjustable value."""
    try:
        return bpy.context.window_manager.superskin_bone_picker_prefs.overall_size
    except Exception:
        return 1.0


def _brighten(color, amount=0.35):
    """Blend an RGBA color toward white by `amount` (0..1), alpha untouched."""
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


def _draw_bone_rhombus(shader, head_2d, tail_2d, fill_color, outline_color, half_width, pivot_ratio=0.333):
    direction = tail_2d - head_2d
    length = direction.length
    if length < 0.0001:
        return
    direction_n = direction.normalized()
    perp = Vector((-direction_n.y, direction_n.x)) * half_width

    pivot = head_2d + direction_n * (length * pivot_ratio)
    h  = tuple(head_2d)
    pl = tuple(pivot + perp)
    pr = tuple(pivot - perp)
    t  = tuple(tail_2d)

    shader.uniform_float("color", fill_color)
    batch_for_shader(shader, 'TRIS', {"pos": [h, pl, t, pr]},
                     indices=[(0, 1, 2), (0, 2, 3)]).draw(shader)

    shader.uniform_float("color", outline_color)
    batch_for_shader(shader, 'LINE_STRIP', {"pos": [h, pl, t, pr, h]}).draw(shader)


def _draw_callback():
    context = bpy.context
    obj = context.active_object
    # Self-silencing: draw nothing outside Edit Mode.  No separate hide needed.
    if not obj or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return

    region = context.region
    rv3d   = context.region_data
    if not region or not rv3d:
        return

    armature = next((m.object for m in obj.modifiers if m.type == 'ARMATURE'), None)
    if not armature:
        return

    vg_names     = {vg.name for vg in obj.vertex_groups}
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

    overall_size = _get_overall_size()

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    gpu.state.blend_set('ALPHA')

    for bone in armature.pose.bones:
        if bone.name not in deform_bones or bone.name not in vg_names:
            continue

        head_3d = armature.matrix_world @ bone.head
        tail_3d = armature.matrix_world @ bone.tail

        head_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, head_3d)
        tail_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, tail_3d)
        if head_2d is None or tail_2d is None:
            continue

        is_active  = (bone.name == active_name)
        is_in_pool = bone.name in pool_names
        is_hovered = (bone.name == _hovered_bone)
        has_influence = bone.name in influenced_bones
        if is_active:
            color = _COLOR_ACTIVE
        elif is_in_pool:
            color = _COLOR_MULTI
        elif has_influence:
            color = _COLOR_DEFAULT
        else:
            color = _COLOR_NO_INFLUENCE
        if is_hovered:
            color = _brighten(color)
        w = _LINE_WIDTH + (_HOVER_EXTRA_WIDTH if is_hovered else 0) + (_HOLD_EXTRA_WIDTH if _is_holding else 0)
        gpu.state.line_width_set(w)

        fill_color = (*color[:3], color[3] * _FILL_OPACITY)
        eff_width = _WEDGE_WIDTH * overall_size
        head_r = eff_width * _HEAD_CIRCLE_SIZE

        seg_dir = tail_2d - head_2d
        seg_len = seg_dir.length
        if seg_len > head_r:
            dir_n = seg_dir / seg_len
            bone_start = head_2d + dir_n * head_r
            _draw_bone_rhombus(shader, bone_start, tail_2d, fill_color, color, eff_width, _PIVOT_RATIO)

        _draw_filled_circle(shader, tuple(head_2d), head_r, fill_color, color)

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
    """Custom stand-in for the OS cursor."""
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
    """Small circular badge at the cursor's top-right corner while a sweep add/remove is in
    progress."""
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
    """Small bone-name label next to the cursor while hovering (bone_picker session only."""
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


# ── Public API ────────────────────────────────────────────────────────────────

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
    """symbol is 'add', 'remove', or None."""
    global _cursor_badge
    _cursor_badge = symbol


def set_active_override(bone_name: str | None) -> None:
    """Pin the overlay's "active bone" (used for _COLOR_ACTIVE) to bone_name regardless of what
    obj.superskin_storage.last_clicked_index says."""
    global _active_override
    _active_override = bone_name


def clear_active_override() -> None:
    global _active_override
    _active_override = _NO_OVERRIDE


def set_holding(state: bool) -> None:
    global _is_holding
    _is_holding = state
    from ...core.facade import CoreFacade
    if state:
        CoreFacade.request_hud_slot("bone_picker", _HUD_LABEL, slot=_HUD_SLOT, color=_HUD_COLOR)
    else:
        CoreFacade.release_hud_slot("bone_picker")

def show():
    """Install the draw handler. Called once from register()."""
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
    """Called from features/bone_picker/__init__.py unregister()."""
    hide()
    if _is_holding:
        set_holding(False)


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
