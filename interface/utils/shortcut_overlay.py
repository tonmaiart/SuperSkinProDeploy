
import bpy
import blf

_draw_handle = None

_LINE_SIZE = 12
_LINE_HEIGHT = 17
_GROUP_GAP = 12
_COLUMN_GAP = 14
_LEFT_MARGIN = 90
_BOTTOM_MARGIN = 60

_KEY_COLOR = (1.0, 0.85, 0.15, 1.0)
_LABEL_COLOR = (0.9, 0.9, 0.9, 0.9)
_ACTIVE_LABEL_COLOR = (0.5, 1.0, 0.6, 1.0)

_STROKE_COLOR = (0.0, 0.0, 0.0, 0.95)
_STROKE_WIDTH = 1.35
_STROKE_OFFSETS = (
    (-1, -1), (0, -1), (1, -1),
    (-1, 0), (1, 0),
    (-1, 1), (0, 1), (1, 1),
)


def _draw_text(font_id, x, y, text, color):
    if not text:
        return
    for ox, oy in _STROKE_OFFSETS:
        blf.position(font_id, x + ox * _STROKE_WIDTH, y + oy * _STROKE_WIDTH, 0)
        blf.color(font_id, *_STROKE_COLOR)
        blf.draw(font_id, text)

    blf.position(font_id, x, y, 0)
    blf.color(font_id, *color)
    blf.draw(font_id, text)


def _is_active(km, flag: str = "is_active") -> bool:
    check = km.get(flag)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False



def _row_key_text(context, km: dict) -> str:
    from .keymap_editor import resolve_live_key_text

    default = km.get("key", "")
    source_label = km.get("source_label")
    if not source_label:
        return default
    return resolve_live_key_text(context, source_label, default=default)


def _row(context, km: dict) -> tuple[str, str, str]:
    key = _row_key_text(context, km)
    drawn = f"Hold {key}" if km.get("mode") == "Hold" and key else key
    return key, drawn, km.get("label", "")


def _collect_groups(context):
    from ..registry.register_api import UnifiedRegistry
    from .keymap_editor import CATEGORY_ORDER, category_for

    exts = sorted(UnifiedRegistry.get_all(), key=lambda e: (e.get_priority(), e.get_id()))

    groups = []
    for ext in exts:
        for km in ext.get_keymaps():
            sub_keymaps = km.get("sub_keymaps")
            if not sub_keymaps or not _is_active(km):
                continue
            rows = [_row(context, sub) for sub in sub_keymaps if sub.get("key") or sub.get("label")]
            if not rows:
                continue
            if km.get("exclusive"):
                return [(rows, True)]
            groups.append((rows, True))

    shadowed = {key for rows, _c in groups for key, _d, _l in rows if key}

    grouped: dict[str, list] = {}
    for ext in exts:
        rows = []
        for km in ext.get_keymaps():
            if not (km.get("key") or km.get("label")) or _is_active(km, "is_hidden"):
                continue
            row = _row(context, km)
            if km.get("source_label") and not row[0]:
                continue
            if row[0] not in shadowed:
                rows.append(row)
        if rows:
            grouped.setdefault(category_for(ext), []).extend(rows)

    for category in CATEGORY_ORDER:
        rows = grouped.pop(category, None)
        if rows:
            groups.append((rows, False))
    for category in sorted(grouped):
        groups.append((grouped[category], False))
    return groups


def _draw_shortcut_overlay_callback():
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    from ...core.facade import CoreFacade
    if not CoreFacade.is_editing_weights():
        return

    groups = _collect_groups(context)
    if not groups:
        return

    font_id = 0

    blf.size(font_id, _LINE_SIZE)
    key_width = 0.0
    for rows, _is_context in groups:
        for _raw, key, label in rows:
            key_width = max(key_width, blf.dimensions(font_id, key)[0])

    x_key = _LEFT_MARGIN
    x_label = x_key + key_width + _COLUMN_GAP

    total_height = sum(
        _LINE_HEIGHT * len(rows) for rows, _c in groups
    ) + _GROUP_GAP * (len(groups) - 1)

    y = _BOTTOM_MARGIN + total_height - _LINE_HEIGHT

    for rows, is_context in groups:
        label_color = _ACTIVE_LABEL_COLOR if is_context else _LABEL_COLOR
        for _raw, key, label in rows:
            _draw_text(font_id, x_key, y, key, _KEY_COLOR)
            _draw_text(font_id, x_label, y, label, label_color)
            y -= _LINE_HEIGHT

        y -= _GROUP_GAP


def register_overlay():
    global _draw_handle
    if _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_shortcut_overlay_callback, (), 'WINDOW', 'POST_PIXEL'
        )


def unregister_overlay():
    global _draw_handle
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        except Exception:
            pass
        _draw_handle = None
