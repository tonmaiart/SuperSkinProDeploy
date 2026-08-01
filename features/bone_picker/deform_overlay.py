"""Deform Bone Skeleton Overlay — persistent wedge-skeleton, always shown in
Edit Mode (draw_handler is installed once at addon register() and stays
installed for the addon's lifetime -- there is no show/hide toggle anymore).

Owned by the bone_picker feature package.  draw_handler_add with POST_PIXEL
always renders on top of ShaderManager's POST_VIEW weight colors.

Colors and shape constants (active/multi/default/no-influence color,
wedge width, line widths, pivot ratio, fill opacity, head circle size)
are hardcoded module constants below -- no longer user-configurable, no
UI, no JSON persistence. ``overall_size`` is the sole exception: still a
live ``superskin_bone_picker_prefs`` WindowManager property, adjustable
via the Settings UI slider (``BonePickerFeature.draw_section()`` in
``bone_picker_feature.py``) -- the only control for it now. There used to
also be an Alt+2+Scroll shortcut (``SUPERSKIN_OT_bone_overlay_size_step``);
removed, since its Blender-native redo panel got clobbered on every Alt+2
release (see ``draw_section()``'s docstring for why).

A deform bone with zero weight anywhere on the active layer draws in
``_COLOR_NO_INFLUENCE`` instead of ``_COLOR_DEFAULT``,
distinguishing "not yet painted" bones from ones that do have influence --
see ``_get_visible_influence_bones()`` (``interface/utils/utils.py``) for
the (already cached/debounced) weight scan this reuses. Only applies to
the "default" (unselected) state -- active/multi-select colors still take
priority regardless of influence.

Note: there is no *distinct* hover color -- the hovered bone keeps
whichever color it would already draw as (active/multi/default/
no-influence), just blended toward white (``_brighten()``) so it visually
pops, plus a hardcoded extra line-width boost (``_HOVER_EXTRA_WIDTH``).
Both are tracked via ``set_hover()``/``clear_hover()``. The modal's ``MOUSEMOVE``
handler still live-applies the hovered bone as the real active bone (so
the mesh-surface weight-color preview updates live as you hover -- that
mechanic is untouched), but that would otherwise *also* repaint this
overlay's wedge to ``_COLOR_ACTIVE`` on every hover move, which is
exactly the behavior this module's ``active override`` exists to
suppress: while a bone_picker session is open, ``set_active_override()``/
``clear_active_override()`` (called from ``ops.py``) pin this overlay's
idea of "the active bone" to the last genuinely *committed* selection
(session start, or a real single-select/sweep commit), so mere hovering
never visually changes which bone reads as active here, even though the
real active-bone state elsewhere does change live for the weight preview.

A small text label (bone name) is drawn next to the mouse cursor while
hovering during a bone_picker session, via ``set_hover(name, mouse_pos)``.

Bug fixes:
- LINE_LOOP removed in Blender 5.0 → LINE_STRIP with closed vertex list.
- batch_for_shader in Blender 5.0 requires plain tuples, not Vector objects.
- Removed depsgraph auto-hide handler: it fired immediately after show() on
  every depsgraph update and called hide() again before the first frame
  rendered.  The draw callback guards itself with obj.mode != 'EDIT' so it
  simply draws nothing when not in Edit Mode — no separate hide mechanism
  needed.
"""

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
# 'add' while a LEFTMOUSE sweep-add is in progress, 'remove' while a
# MIDDLEMOUSE sweep-remove is in progress, None otherwise -- see
# set_cursor_badge() / _draw_cursor_badge().
_cursor_badge: str | None = None

# Sentinel distinguishing "no override in effect" from "override to no bone
# being active" (None is a valid override value -- e.g. the pre-session
# active bone might genuinely be "nothing").
_NO_OVERRIDE = object()
_active_override = _NO_OVERRIDE

# Hardcoded appearance constants -- no longer user-configurable (no UI, no
# JSON persistence). Colors given as hex RGBA -> normalized float tuples:
#   Active       #FFFF02FF   Multi   #00FF01E6
#   Default      #99B6C8FF   No Influence  #B3B3B3FF
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

# Custom arrow/picker cursor -- replaces the native OS cursor (hidden via
# cursor_modal_set('NONE') in ops.py's _set_picker_cursor()) while a
# bone_picker session is running. Drawn from this module's own persistent
# draw handler at whatever position _hover_mouse_pos currently holds (kept
# live by every MOUSEMOVE in ops.py's modal(), regardless of whether a bone
# is actually hovered), so it tracks the real mouse 1:1. Tilted triangular
# pointer, tip pinned to the actual mouse position (the hotspot); white
# fill + black stroke drawn the same fill-then-outline way
# _draw_bone_rhombus()/_draw_filled_circle() already do above.
_CURSOR_LENGTH        = 20
_CURSOR_HALF_WIDTH    = 7
_CURSOR_TILT_DEG      = 25
_CURSOR_COLOR         = (1.0, 1.0, 1.0, 1.0)
_CURSOR_OUTLINE_COLOR = (0.0, 0.0, 0.0, 1.0)

# Small add/remove badge pinned to the cursor's top-right while a sweep is
# in progress (LEFTMOUSE = add, MIDDLEMOUSE = remove) -- fixed screen-space
# offset from the hotspot, independent of the arrow's own tilt, so it stays
# legibly "up and to the right" regardless of _CURSOR_TILT_DEG.
_BADGE_OFFSET        = (13, 11)
_BADGE_RADIUS        = 6
_BADGE_ARM_LEN        = 3.5
_BADGE_BG_COLOR       = (1.0, 1.0, 1.0, 1.0)
_BADGE_BG_OUTLINE     = (0.0, 0.0, 0.0, 1.0)
_BADGE_ADD_COLOR      = (0.1568627450980392, 0.7215686274509804, 0.19215686274509805, 1.0)
_BADGE_REMOVE_COLOR   = (0.8509803921568627, 0.17254901960784313, 0.12941176470588237, 1.0)

# Bottom-center HUD label shown while the modal is held -- same bottom-center
# blf text treatment as overlay_color/multi_color_draw.py's "MULTI COLOR
# MODE" HUD (black drop-shadow pass + colored pass on top), just a different
# label/color so the two aren't visually confused.
_HUD_LABEL = "BONE PICKER"
_HUD_COLOR = (1.0, 0.8, 0.0, 1.0)


def _get_overall_size() -> float:
    """The one remaining live/adjustable value -- everything else above is
    now a hardcoded constant. Still a WindowManager property so the
    Settings UI slider (BonePickerFeature.draw_section(),
    bone_picker_feature.py) has something to persistently mutate."""
    try:
        return bpy.context.window_manager.superskin_bone_picker_prefs.overall_size
    except Exception:
        return 1.0


def _brighten(color, amount=0.35):
    """Blend an RGBA color toward white by `amount` (0..1), alpha untouched.
    Used to make the hovered bone visually pop without discarding its
    underlying active/multi/default/no-influence color coding -- see the
    module docstring's "Hover UX" note for why a fixed hover color was
    removed in favor of this."""
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
    if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
        return

    region = context.region
    rv3d   = context.region_data
    if not region or not rv3d:
        return

    if _is_holding:
        _draw_hud(context)

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
    selected_names = getattr(storage, "selected_names", "") if storage else ""

    # Which deform bones have any weight at all on the active layer --
    # reuses the same cached, deform-generation-keyed scanner the Deform
    # Bones list already uses for its BONE_DATA/BLANK1 icon (see
    # interface/utils/utils.py's own docstring for the Edit-Mode temp-VG
    # routing and debounce this already handles) rather than re-scanning
    # weights here. Defensively guarded, same as _get_overall_size() below --
    # a mesh with no layer system initialised yet (e.g. bare native Tab
    # into Edit Mode before any layer init) shouldn't crash this draw
    # handler; treat that as "nothing has influence yet" instead.
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
        is_in_pool = f",{bone.name}," in selected_names
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


def _draw_hud(context):
    """Bottom-center "BONE PICKER" label while the modal is held -- mirrors
    overlay_color/multi_color_draw.py's _draw_hud() bottom-center treatment
    (drop-shadow pass, then colored pass on top)."""
    font_id = 0
    blf.size(font_id, 24)
    text_w, _ = blf.dimensions(font_id, _HUD_LABEL)
    region_width = context.region.width
    center_x = max(0, region_width // 2 - int(text_w) // 2)
    y_offset = 25
    blf.position(font_id, center_x + 2, y_offset + 2, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, _HUD_LABEL)
    blf.position(font_id, center_x, y_offset, 0)
    blf.color(font_id, *_HUD_COLOR)
    blf.draw(font_id, _HUD_LABEL)


_HOVER_LABEL_OUTLINE_OFFSETS = (
    (-1, -1), (0, -1), (1, -1),
    (-1,  0),          (1,  0),
    (-1,  1), (0,  1), (1,  1),
    (-2,  0), (2,  0), (0, -2), (0,  2),
)


def _draw_arrow_cursor(shader, center):
    """Custom stand-in for the OS cursor -- see the constants block above
    for why this exists. `center` is the hotspot: the tip vertex lands
    exactly there, with the triangle's base swung out and tilted by
    _CURSOR_TILT_DEG so it reads as a classic angled pointer rather than a
    plain isoceles arrowhead pointing straight down."""
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
    """Small circular badge at the cursor's top-right corner while a sweep
    add/remove is in progress -- '+' (green) for add, '-' (red) for
    remove. White-filled/black-outlined backing circle (reusing
    _draw_filled_circle(), same as the bone wedges above) so the plus/minus
    strokes stay legible over any color already showing through the arrow
    cursor's own fill."""
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
    """Small bone-name label next to the cursor while hovering (bone_picker
    session only -- mouse_pos comes from set_hover(), cleared on modal exit).
    Drawn with a thick outline (stroke stamped at several surrounding
    offsets, not just a single 1px shadow) so it stays readable over any
    viewport background."""
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
    """symbol is 'add', 'remove', or None. Called from ops.py's modal() on
    every LEFTMOUSE/MIDDLEMOUSE PRESS/RELEASE that starts or ends a sweep;
    also reset by clear_hover() on every session exit path."""
    global _cursor_badge
    _cursor_badge = symbol


def set_active_override(bone_name: str | None) -> None:
    """Pin the overlay's "active bone" (used for _COLOR_ACTIVE) to
    bone_name regardless of what obj.superskin_storage.last_clicked_index
    says. Used by ops.py to keep the overlay's active-color display frozen
    on the last genuinely committed selection while a hover preview is
    live-mutating the real active bone underneath for the mesh-surface
    weight-color preview. Pass None to explicitly override to "no bone
    active" (distinct from clear_active_override(), which un-pins entirely
    and falls back to reading storage directly)."""
    global _active_override
    _active_override = bone_name


def clear_active_override() -> None:
    global _active_override
    _active_override = _NO_OVERRIDE


def set_holding(state: bool) -> None:
    global _is_holding
    _is_holding = state

def show():
    """Install the draw handler. Called once from register() -- the overlay
    is always shown in Edit Mode now, there is no toggle/hide-on-cancel
    mechanism anymore."""
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


def _tag_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
