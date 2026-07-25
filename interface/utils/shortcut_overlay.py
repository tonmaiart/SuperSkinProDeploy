"""Centralized shortcut-overlay HUD — lists every registered domain's
keyboard shortcuts (``UnifiedFeatureExtension.keymaps``) as viewport text,
one section per domain, stacked vertically and anchored to the left-middle
of the 3D viewport (offset from the edge to clear Blender's native Toolbar).
Visible only while SuperSkinPro's own "Edit Layer Weight" mode is active,
and only while toggled on (Alt+1, ``superskin.toggle_shortcut_overlay``).

Each keymap entry may include an optional ``"mode"`` field ("Hold" /
"Toggle") describing the gesture kind; all three columns (mode, key, label)
are aligned to a shared column width computed across every entry, so rows
line up regardless of which domain they belong to.

Plain ``blf`` / ``POST_PIXEL`` text draw, matching the convention already
used by ``features/overlay_color/multi_color_draw.py`` and the (removed)
X-ray warning HUD in ``core/shaders/shader_manager.py`` -- this codebase
deliberately moved away from GPU-batch viewport overlays.
"""

import bpy
import blf

_draw_handle = None
_keymaps = []
_overlay_state = {"visible": True}

_HEADER_SIZE = 13
_LINE_SIZE = 12
_LINE_HEIGHT = 17
_HEADER_HEIGHT = 20
_DOMAIN_GAP = 10
_COLUMN_GAP = 14
_LEFT_MARGIN = 60  # clears Blender's native Toolbar on the left edge

_HEADER_COLOR = (1.0, 1.0, 1.0, 1.0)
_MODE_COLOR = (0.6, 0.6, 0.6, 0.9)
_KEY_COLOR = (1.0, 0.85, 0.15, 1.0)
_LABEL_COLOR = (0.9, 0.9, 0.9, 0.9)
_ACTIVE_LABEL_COLOR = (0.5, 1.0, 0.6, 1.0)
_SUB_INDENT = 14


def _draw_text(font_id, x, y, text, color):
    if not text:
        return
    blf.position(font_id, x + 1, y - 1, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.75)
    blf.draw(font_id, text)

    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _is_active(km) -> bool:
    check = km.get("is_active")
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False


def _collect_domains():
    from ..registry.register_api import UnifiedRegistry

    exts = sorted(UnifiedRegistry.get_all(), key=lambda e: (e.get_priority(), e.get_id()))

    # Focused/dynamic mode: the moment any keymap entry anywhere is
    # currently active, show ONLY that entry's own domain header plus its
    # live sub_keymaps -- every other domain, and every other (non-active)
    # entry in that same domain, is hidden while the gesture is in progress.
    for ext in exts:
        for km in ext.get_keymaps():
            sub_keymaps = km.get("sub_keymaps")
            if sub_keymaps and _is_active(km):
                rows = [
                    (sub.get("mode", ""), sub.get("key", ""), sub.get("label", ""), True)
                    for sub in sub_keymaps
                    if sub.get("key") or sub.get("label")
                ]
                if rows:
                    return [(ext.get_section_title() or ext.get_id(), rows)]

    # Normal mode: the full static list across every domain.
    domains = []
    for ext in exts:
        rows = [
            (km.get("mode", ""), km.get("key", ""), km.get("label", ""), False)
            for km in ext.get_keymaps()
            if km.get("key") or km.get("label")
        ]
        if rows:
            domains.append((ext.get_section_title() or ext.get_id(), rows))
    return domains


def _draw_shortcut_overlay_callback():
    if not _overlay_state["visible"]:
        return

    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    from ...core.facade import CoreFacade
    if not CoreFacade.is_editing_weights():
        return

    domains = _collect_domains()
    if not domains:
        return

    font_id = 0

    # Column widths shared across every domain so rows line up throughout
    # the whole vertical list, not just within one domain's own block.
    blf.size(font_id, _LINE_SIZE)
    mode_width = 0.0
    key_width = 0.0
    for _header, rows in domains:
        for mode, key, _label, _is_sub in rows:
            mode_width = max(mode_width, blf.dimensions(font_id, mode)[0])
            key_width = max(key_width, blf.dimensions(font_id, key)[0])

    x_mode = _LEFT_MARGIN
    x_key = x_mode + mode_width + _COLUMN_GAP if mode_width else x_mode
    x_label = x_key + key_width + _COLUMN_GAP

    total_height = sum(
        _HEADER_HEIGHT + _LINE_HEIGHT * len(rows) for _h, rows in domains
    ) + _DOMAIN_GAP * (len(domains) - 1)

    region_height = context.region.height
    y = region_height // 2 + total_height // 2

    for header, rows in domains:
        blf.size(font_id, _HEADER_SIZE)
        _draw_text(font_id, _LEFT_MARGIN, y, header, _HEADER_COLOR)
        y -= _HEADER_HEIGHT

        blf.size(font_id, _LINE_SIZE)
        for mode, key, label, is_sub in rows:
            indent = _SUB_INDENT if is_sub else 0
            label_color = _ACTIVE_LABEL_COLOR if is_sub else _LABEL_COLOR
            _draw_text(font_id, x_mode + indent, y, mode, _MODE_COLOR)
            _draw_text(font_id, x_key + indent, y, key, _KEY_COLOR)
            _draw_text(font_id, x_label + indent, y, label, label_color)
            y -= _LINE_HEIGHT

        y -= _DOMAIN_GAP


def _tag_redraw_all():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class SUPERSKIN_OT_toggle_shortcut_overlay(bpy.types.Operator):
    """Toggle the shortcut-overlay HUD on/off (Alt+1)."""
    bl_idname = "superskin.toggle_shortcut_overlay"
    bl_label = "Toggle Shortcut Overlay"

    def execute(self, context):
        _overlay_state["visible"] = not _overlay_state["visible"]
        _tag_redraw_all()
        return {'FINISHED'}


def register_overlay():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_shortcut_overlay_callback, (), 'WINDOW', 'POST_PIXEL'
        )

    bpy.utils.register_class(SUPERSKIN_OT_toggle_shortcut_overlay)

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            "superskin.toggle_shortcut_overlay", type='ONE', value='PRESS', alt=True)
        _keymaps.append((km, kmi))


def unregister_overlay():
    global _draw_handle

    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()

    try:
        bpy.utils.unregister_class(SUPERSKIN_OT_toggle_shortcut_overlay)
    except Exception:
        pass

    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None
