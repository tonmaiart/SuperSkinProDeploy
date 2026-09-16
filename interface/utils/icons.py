"""Custom icon loader for SuperSkinPro's interface.

A single ``bpy.utils.previews`` collection (currently twenty icons: the
help/link glyph, the addon-updater's download glyph, the layer_viewer
Mesh selector's uninitialised/initialised body-type glyphs, the deform
bone list's edit-mask glyph, the layer list's combine/duplicate/edit
glyphs, the bone-weight glyph, the deform bone list's two clipboard
drop-down glyphs, the Weight Brush's Hard/Medium/Soft hardness-preset
glyphs, the Weight Brush's Surface/Screen projection glyphs, and the
Weight Apply tool-select row's Brush/Box/Circle/Lasso glyphs -- Box and
Circle are no longer drawn by that row, see ``get_tool_lasso_icon_id()``,
but their glyphs are kept loaded rather than deleted -- plus 24 more:
``assets/layer_icons/{WhiteMask,BlackMask,EditedMask}_{color}.png`` for
each of 8 colors, backing the Layer list's per-row mask-state icon, see
``get_layer_state_icon_id()``/``get_layer_swatch_icon_id()`` below), loaded
once at addon-register time and torn down at unregister. No core/ or
core_subsystems/ imports (only ``os``, ``gpu``, and Blender's own
``bpy.utils.previews``), so this loads at module scope from
``interface/utils/__init__.py`` exactly like ``gpu_utils`` does -- no
deferred-loading dance needed.

``get_*_icon_id()`` functions return a UILayout-facing ``icon_value``.
``get_*_icon_texture()`` functions return a ``gpu.types.GPUTexture`` built
from the same source PNG's full-resolution pixels, lazily on first call
and cached thereafter -- for callers that need to draw the icon directly
in a raw ``SpaceView3D`` ``POST_PIXEL`` handler (e.g.
``core/shaders/shader_manager.py``'s shared HUD stack), where a UILayout
``icon_value`` cannot be used.
"""

import os
import time
import bpy.utils.previews
import gpu

_ADDON_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_ICON_HELP_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_help.png")
_ICON_UPDATE_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-download-64.png")
_ICON_MESH_UNINIT_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_mesh_uninit.png")
_ICON_MESH_INIT_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_mesh_init.png")
_ICON_LAYER_MASK_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-layer-mask-50.png")
_ICON_COMBINE_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-combine-50.png")
_ICON_DUPLICATE_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-duplicate-24.png")
_ICON_EDIT_PATH = os.path.join(_ADDON_ROOT, "assets", "icons8-edit-48.png")
_ICON_BONE_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_bone.png")
_ICON_CLIPBOARD_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_clipboard.png")
_ICON_CLIPBOARD_FILL_PATH = os.path.join(_ADDON_ROOT, "assets", "icon_clipboard_fill.png")
_ICON_BRUSH_HARD_PATH = os.path.join(_ADDON_ROOT, "assets", "hard.png")
_ICON_BRUSH_MEDIUM_PATH = os.path.join(_ADDON_ROOT, "assets", "medium.png")
_ICON_BRUSH_SOFT_PATH = os.path.join(_ADDON_ROOT, "assets", "soft.png")
_ICON_SURFACE_PROJECTION_PATH = os.path.join(_ADDON_ROOT, "assets", "surface_projection.png")
_ICON_SCREEN_PROJECTION_PATH = os.path.join(_ADDON_ROOT, "assets", "screen_projection.png")
_ICON_TOOL_BRUSH_PATH = os.path.join(_ADDON_ROOT, "assets", "tool_brush.png")
_ICON_TOOL_BOX_PATH = os.path.join(_ADDON_ROOT, "assets", "tool_box.png")
_ICON_TOOL_CIRCLE_PATH = os.path.join(_ADDON_ROOT, "assets", "tool_circle.png")
_ICON_TOOL_LASSO_PATH = os.path.join(_ADDON_ROOT, "assets", "tool_lasso.png")

_LAYER_ICONS_DIR = os.path.join(_ADDON_ROOT, "assets", "layer_icons")

# The 8 colors available for a Layer's mask-state icon -- filenames under
# assets/layer_icons/ are exactly f"{state_prefix}_{color}.png" for every
# combination of these colors and _STATE_TO_FILE_PREFIX's values below.
# This is the sole source of truth for "which colors exist" -- callers
# (e.g. the Layer list's "More" menu color picker) should iterate this
# tuple rather than hardcoding their own copy.
LAYER_ICON_COLORS = ("blue", "cyan", "green", "orange", "pink", "purple", "white", "yellow")

# Maps CoreFacade.get_layer_mask_state()'s return values to the asset
# filename prefix used under assets/layer_icons/.
_STATE_TO_FILE_PREFIX = {
    'WHITE': "WhiteMask",
    'BLACK': "BlackMask",
    'EDITED': "EditedMask",
}

_preview_collection = None
_icon_textures = {}

# bpy.utils.previews.load() only reserves a preview slot and returns a valid
# icon_id immediately -- the compact icon bitmap widgets actually sample via
# icon_value= is generated lazily, only once something reads icon_pixels
# for that preview (see _force_icon_generation() in register() below, which
# does this eagerly for every icon before any panel of this addon can even
# draw). The timer/polling below is a defensive backstop for whatever the
# eager pass doesn't fully settle (GPU-side upload lag -- see
# _ICON_POLL_STABLE_TICKS) rather than the primary fix.
_PENDING_ICON_NAMES = []
_ICON_POLL_START_TIME = 0.0
_ICON_POLL_INTERVAL = 0.05
_ICON_POLL_TIMEOUT = 5.0

# image_size turning non-zero only means the CPU-side bitmap has been
# decoded -- the GPU-side icon texture Blender actually samples for
# icon_value= draws is uploaded in a separate, later step. Stopping the
# timer on the very first "ready" tick can redraw right into that gap,
# reading as the icon flashing on then going blank again a moment later
# (reported specifically for icons never drawn before, e.g. the first time
# a fresh session enters Edit Layer Weight and the SKINNING tab draws its
# brush/projection icons for the first time). Keep polling (and
# redrawing) for a few extra ticks after "ready" is first observed so a
# lagging GPU upload gets picked up too, instead of stopping on the first
# CPU-only signal.
_ICON_POLL_STABLE_TICKS = 4
_icon_poll_ready_streak = 0


def get_help_icon_id() -> int:
    """Return the ``icon_value`` for the custom help/link icon, or ``0``
    (Blender's "no icon" sentinel) if it failed to load -- callers should
    fall back to a built-in icon rather than pass ``0`` straight through."""
    if _preview_collection is None or "icon_help" not in _preview_collection:
        return 0
    return _preview_collection["icon_help"].icon_id


def get_update_icon_id() -> int:
    """Return the ``icon_value`` for the custom addon-update download icon,
    or ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_update" not in _preview_collection:
        return 0
    return _preview_collection["icon_update"].icon_id


def get_mesh_uninit_icon_id() -> int:
    """Return the ``icon_value`` for a mesh with no layer system initialised
    yet, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_mesh_uninit" not in _preview_collection:
        return 0
    return _preview_collection["icon_mesh_uninit"].icon_id


def get_mesh_init_icon_id() -> int:
    """Return the ``icon_value`` for a mesh that already has a layer system
    initialised, or ``0`` (Blender's "no icon" sentinel) if it failed to
    load -- callers should fall back to a built-in icon rather than pass
    ``0`` straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_mesh_init" not in _preview_collection:
        return 0
    return _preview_collection["icon_mesh_init"].icon_id


def get_layer_mask_icon_id() -> int:
    """Return the ``icon_value`` for the custom edit-mask icon, or ``0``
    (Blender's "no icon" sentinel) if it failed to load -- callers should
    fall back to a built-in icon rather than pass ``0`` straight through.
    Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_layer_mask" not in _preview_collection:
        return 0
    return _preview_collection["icon_layer_mask"].icon_id


def get_combine_icon_id() -> int:
    """Return the ``icon_value`` for the custom merge/combine layers icon,
    or ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_combine" not in _preview_collection:
        return 0
    return _preview_collection["icon_combine"].icon_id


def get_duplicate_icon_id() -> int:
    """Return the ``icon_value`` for the custom duplicate-layer icon, or
    ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_duplicate" not in _preview_collection:
        return 0
    return _preview_collection["icon_duplicate"].icon_id


def get_edit_icon_id() -> int:
    """Return the ``icon_value`` for the custom "Edit Layer Weight" icon,
    or ``0`` (Blender's "no icon" sentinel) if it failed to load -- callers
    should fall back to a built-in icon rather than pass ``0`` straight
    through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_edit" not in _preview_collection:
        return 0
    return _preview_collection["icon_edit"].icon_id


def get_bone_icon_id() -> int:
    """Return the ``icon_value`` for the custom bone-weight icon, or ``0``
    (Blender's "no icon" sentinel) if it failed to load -- callers should
    fall back to a built-in icon rather than pass ``0`` straight through.
    Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_bone" not in _preview_collection:
        return 0
    return _preview_collection["icon_bone"].icon_id


def get_clipboard_icon_id() -> int:
    """Return the ``icon_value`` for the "Clipboard Bone Weight" drop-down
    icon, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_clipboard" not in _preview_collection:
        return 0
    return _preview_collection["icon_clipboard"].icon_id


def get_clipboard_fill_icon_id() -> int:
    """Return the ``icon_value`` for the "Clipboard Layer Weight" drop-down
    icon, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_clipboard_fill" not in _preview_collection:
        return 0
    return _preview_collection["icon_clipboard_fill"].icon_id


def get_brush_hard_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Brush's "Hard" hardness-preset
    icon, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_brush_hard" not in _preview_collection:
        return 0
    return _preview_collection["icon_brush_hard"].icon_id


def get_brush_medium_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Brush's "Medium"
    hardness-preset icon, or ``0`` (Blender's "no icon" sentinel) if it
    failed to load -- callers should fall back to a built-in icon rather
    than pass ``0`` straight through. Same convention as
    ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_brush_medium" not in _preview_collection:
        return 0
    return _preview_collection["icon_brush_medium"].icon_id


def get_brush_soft_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Brush's "Soft" hardness-preset
    icon, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_brush_soft" not in _preview_collection:
        return 0
    return _preview_collection["icon_brush_soft"].icon_id


def get_surface_projection_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Brush's "Surface" projection
    icon, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_surface_projection" not in _preview_collection:
        return 0
    return _preview_collection["icon_surface_projection"].icon_id


def get_screen_projection_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Brush's "Screen" projection
    icon, or ``0`` (Blender's "no icon" sentinel) if it failed to load --
    callers should fall back to a built-in icon rather than pass ``0``
    straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_screen_projection" not in _preview_collection:
        return 0
    return _preview_collection["icon_screen_projection"].icon_id


def get_tool_brush_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Apply tool-select row's
    "Brush" icon, or ``0`` (Blender's "no icon" sentinel) if it failed to
    load -- callers should fall back to a built-in icon rather than pass
    ``0`` straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_tool_brush" not in _preview_collection:
        return 0
    return _preview_collection["icon_tool_brush"].icon_id


def get_tool_box_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Apply tool-select row's
    "Box" icon, or ``0`` (Blender's "no icon" sentinel) if it failed to
    load -- callers should fall back to a built-in icon rather than pass
    ``0`` straight through. Same convention as ``get_help_icon_id()`` above."""
    if _preview_collection is None or "icon_tool_box" not in _preview_collection:
        return 0
    return _preview_collection["icon_tool_box"].icon_id


def get_tool_circle_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Apply tool-select row's
    "Circle" icon, or ``0`` (Blender's "no icon" sentinel) if it failed to
    load -- callers should fall back to a built-in icon rather than pass
    ``0`` straight through. Same convention as ``get_help_icon_id()`` above.
    No longer drawn by any panel row (the tool-select row's Box/Circle
    buttons were replaced by a single Lasso button, see
    ``get_tool_lasso_icon_id()`` below and
    ``docs/domains/weight_apply.md``'s "UI Layout" section) -- kept, not
    deleted, since ``builtin.select_circle`` itself is still fully
    functional via ``circle_tool_adjust`` and Blender's native Toolbar."""
    if _preview_collection is None or "icon_tool_circle" not in _preview_collection:
        return 0
    return _preview_collection["icon_tool_circle"].icon_id


def get_tool_lasso_icon_id() -> int:
    """Return the ``icon_value`` for the Weight Apply tool-select row's
    "Lasso" icon, or ``0`` (Blender's "no icon" sentinel) if it failed to
    load -- callers should fall back to a built-in icon rather than pass
    ``0`` straight through. Same convention as ``get_help_icon_id()`` above.
    Backs the button that replaced the row's former separate Box/Circle
    buttons -- see ``docs/domains/lasso_tool_adjust.md``."""
    if _preview_collection is None or "icon_tool_lasso" not in _preview_collection:
        return 0
    return _preview_collection["icon_tool_lasso"].icon_id


def get_layer_state_icon_id(state: str, color: str) -> int:
    """Return the ``icon_value`` for the Layer list's per-row mask-state
    icon, or ``0`` (Blender's "no icon" sentinel) if *state*/*color* is
    unrecognized or the source PNG failed to load -- callers should fall
    back to a built-in icon rather than pass ``0`` straight through. Same
    convention as ``get_help_icon_id()`` above.

    Args:
        state: One of ``'WHITE'``/``'BLACK'``/``'EDITED'`` -- see
            ``CoreFacade.get_layer_mask_state()``.
        color: One of ``LAYER_ICON_COLORS``.
    """
    prefix = _STATE_TO_FILE_PREFIX.get(state)
    if prefix is None or color not in LAYER_ICON_COLORS:
        return 0
    key = f"layer_icon_{prefix}_{color}"
    if _preview_collection is None or key not in _preview_collection:
        return 0
    return _preview_collection[key].icon_id


def get_layer_swatch_icon_id(color: str) -> int:
    """Return the ``icon_value`` for *color*'s swatch preview (the fully
    masked-in ``WhiteMask`` variant), used by the Layer list's "More" menu
    color picker (``SUPERSKIN_MT_layer_list_more_options.draw()``) to show
    the color alone, independent of any particular layer's actual mask
    state. Returns ``0`` if *color* is unrecognized or failed to load."""
    return get_layer_state_icon_id('WHITE', color)


def _build_icon_texture(name: str):
    """Build (or return the cached) ``gpu.types.GPUTexture`` for preview
    *name*, sourced from that preview's full-resolution pixels rather than
    its small fixed-size icon thumbnail. Returns ``None`` if the preview
    failed to load -- callers should skip drawing an icon rather than pass
    ``None`` into a texture sampler."""
    if name in _icon_textures:
        return _icon_textures[name]
    if _preview_collection is None or name not in _preview_collection:
        return None
    preview = _preview_collection[name]
    width, height = preview.image_size[:]
    if width == 0 or height == 0:
        return None
    pixels = gpu.types.Buffer('FLOAT', width * height * 4, list(preview.image_pixels_float))
    texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=pixels)
    _icon_textures[name] = texture
    return texture


def get_layer_mask_icon_texture():
    """Return the cached ``gpu.types.GPUTexture`` for the custom edit-mask
    icon (same source PNG as ``get_layer_mask_icon_id()``), or ``None`` if
    it failed to load. For raw ``SpaceView3D`` HUD drawing -- see this
    module's docstring."""
    return _build_icon_texture("icon_layer_mask")


def get_bone_icon_texture():
    """Return the cached ``gpu.types.GPUTexture`` for the custom
    bone-weight icon (same source PNG as ``get_bone_icon_id()``), or
    ``None`` if it failed to load. For raw ``SpaceView3D`` HUD drawing --
    see this module's docstring."""
    return _build_icon_texture("icon_bone")


def _icon_is_ready(name: str) -> bool:
    """Return ``True`` once *name*'s compact icon representation -- the one
    ``icon_value=`` widgets actually sample, distinct from the full-size
    ``image_size``/``image_pixels*`` used by ``_build_icon_texture()`` --
    has been generated. ``icon_size`` reads ``(0, 0)`` until that happens,
    even though the preview slot and its ``icon_id`` already exist right
    after ``load()`` returns. ``register()`` forces this eagerly for every
    icon (see ``_force_icon_generation()``), so this should already read
    ``True`` for everything by the time any panel gets a chance to draw --
    this check (and the timer polling it) is a defensive backstop only."""
    preview = _preview_collection.get(name) if _preview_collection else None
    if preview is None:
        return True
    return tuple(preview.icon_size[:]) != (0, 0)


def _force_icon_generation(preview) -> None:
    """Force Blender to synchronously generate *preview*'s compact icon
    representation right now, instead of leaving it to whichever UI code
    happens to draw with this preview's ``icon_value`` first. Reading
    ``icon_pixels`` is what actually triggers generation -- merely reading
    ``icon_size``/``icon_id`` beforehand does not. Called from ``register()``
    for every loaded icon, before any panel of this addon has even been
    registered yet (icons load in the Foundations step, ahead of Layout
    Frames -- see docs/core-interfaces/interface.md's Registration Order),
    so this eager pass has already run well before the first real draw()
    call can happen."""
    len(preview.icon_pixels)


def _tag_redraw_all_view3d():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _poll_icon_generation():
    """``bpy.app.timers`` callback -- redraws every ``VIEW_3D`` area on every
    tick while any preview queued by ``register()`` is still generating (or
    settling, see ``_ICON_POLL_STABLE_TICKS`` above), instead of leaving a
    blank icon on screen until some unrelated redraw happens to catch up.
    Gives up after ``_ICON_POLL_TIMEOUT`` seconds so a genuinely failed
    preview can't spin this timer forever."""
    global _icon_poll_ready_streak

    if _preview_collection is None:
        _icon_poll_ready_streak = 0
        return None  # Addon was disabled mid-poll.

    _tag_redraw_all_view3d()

    if all(_icon_is_ready(name) for name in _PENDING_ICON_NAMES):
        _icon_poll_ready_streak += 1
        if _icon_poll_ready_streak >= _ICON_POLL_STABLE_TICKS:
            return None
        return _ICON_POLL_INTERVAL

    _icon_poll_ready_streak = 0
    if time.monotonic() - _ICON_POLL_START_TIME > _ICON_POLL_TIMEOUT:
        return None

    return _ICON_POLL_INTERVAL


def register():
    global _preview_collection, _ICON_POLL_START_TIME, _icon_poll_ready_streak
    _preview_collection = bpy.utils.previews.new()
    _PENDING_ICON_NAMES.clear()
    _icon_poll_ready_streak = 0

    def _load(name, path):
        if os.path.isfile(path):
            _preview_collection.load(name, path, 'IMAGE')
            _force_icon_generation(_preview_collection[name])
            _PENDING_ICON_NAMES.append(name)

    _load("icon_help", _ICON_HELP_PATH)
    _load("icon_update", _ICON_UPDATE_PATH)
    _load("icon_mesh_uninit", _ICON_MESH_UNINIT_PATH)
    _load("icon_mesh_init", _ICON_MESH_INIT_PATH)
    _load("icon_layer_mask", _ICON_LAYER_MASK_PATH)
    _load("icon_combine", _ICON_COMBINE_PATH)
    _load("icon_duplicate", _ICON_DUPLICATE_PATH)
    _load("icon_edit", _ICON_EDIT_PATH)
    _load("icon_bone", _ICON_BONE_PATH)
    _load("icon_clipboard", _ICON_CLIPBOARD_PATH)
    _load("icon_clipboard_fill", _ICON_CLIPBOARD_FILL_PATH)
    _load("icon_brush_hard", _ICON_BRUSH_HARD_PATH)
    _load("icon_brush_medium", _ICON_BRUSH_MEDIUM_PATH)
    _load("icon_brush_soft", _ICON_BRUSH_SOFT_PATH)
    _load("icon_surface_projection", _ICON_SURFACE_PROJECTION_PATH)
    _load("icon_screen_projection", _ICON_SCREEN_PROJECTION_PATH)
    _load("icon_tool_brush", _ICON_TOOL_BRUSH_PATH)
    _load("icon_tool_box", _ICON_TOOL_BOX_PATH)
    _load("icon_tool_circle", _ICON_TOOL_CIRCLE_PATH)
    _load("icon_tool_lasso", _ICON_TOOL_LASSO_PATH)
    for _state_prefix in _STATE_TO_FILE_PREFIX.values():
        for _color in LAYER_ICON_COLORS:
            _path = os.path.join(_LAYER_ICONS_DIR, f"{_state_prefix}_{_color}.png")
            _load(f"layer_icon_{_state_prefix}_{_color}", _path)

    _ICON_POLL_START_TIME = time.monotonic()
    if _PENDING_ICON_NAMES and not bpy.app.timers.is_registered(_poll_icon_generation):
        bpy.app.timers.register(_poll_icon_generation, first_interval=_ICON_POLL_INTERVAL)


def unregister():
    global _preview_collection, _icon_poll_ready_streak
    if bpy.app.timers.is_registered(_poll_icon_generation):
        bpy.app.timers.unregister(_poll_icon_generation)
    _PENDING_ICON_NAMES.clear()
    _icon_poll_ready_streak = 0
    if _preview_collection is not None:
        bpy.utils.previews.remove(_preview_collection)
        _preview_collection = None
    for texture in _icon_textures.values():
        if hasattr(texture, "free"):
            texture.free()
    _icon_textures.clear()
