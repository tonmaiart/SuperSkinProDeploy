
import bpy
import blf

from .keymap_editor import default_hint_labels, extra_hint_entries, hint_entry_visible

_draw_handle = None

_LINE_SIZE = 12
_LINE_HEIGHT = 17
_GROUP_GAP = 12
_COLUMN_GAP = 14

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



def _field(km: dict, name: str) -> str:
    value = km.get(name, "")
    if callable(value):
        try:
            value = value()
        except Exception:
            value = ""
    return value or ""


def _row_key_text(context, km: dict) -> str:
    from .keymap_editor import resolve_live_key_text

    default = _field(km, "key")
    source_label = km.get("source_label")
    if not source_label:
        return default
    if isinstance(source_label, tuple):
        texts = [resolve_live_key_text(context, label) for label in source_label]
        return " / ".join(texts) if all(texts) else default
    return resolve_live_key_text(context, source_label, default=default)


def _row(context, km: dict) -> tuple[str, str, str]:
    key = _row_key_text(context, km)
    drawn = f"Hold {key}" if _field(km, "mode") == "Hold" and key else key
    return key, drawn, km.get("label", "")


def _split_sub_keymaps(context, sub_keymaps, defaults) -> list[list]:
    chunks = [[]]
    for sub in sub_keymaps:
        if sub.get("separator"):
            chunks.append([])
        elif (sub.get("key") or sub.get("label")) and hint_entry_visible(context, sub, defaults):
            chunks[-1].append(_row(context, sub))
    return chunks


def _base_rows(context):
    from ..registry.register_api import UnifiedRegistry

    defaults = default_hint_labels()
    rows = []
    for ext in UnifiedRegistry.get_all():
        for km in ext.get_keymaps():
            if (km.get("key") or km.get("label")) and hint_entry_visible(context, km, defaults):
                rows.append(_row(context, km))
    rows.extend(_row(context, km) for km in extra_hint_entries(context, 'GENERAL'))
    return rows


def _collect_groups(context):
    from ..registry.register_api import UnifiedRegistry
    from .keymap_editor import CATEGORY_ORDER, category_for, domain_rank

    exts = sorted(UnifiedRegistry.get_all(), key=lambda e: (e.get_priority(), e.get_id()))
    defaults = default_hint_labels()

    groups = []
    for ext in exts:
        for km in ext.get_keymaps():
            sub_keymaps = km.get("sub_keymaps")
            if not sub_keymaps or not _is_active(km):
                continue
            chunks = _split_sub_keymaps(context, sub_keymaps, defaults)
            if km.get("editor_tab"):
                chunks[-1].extend(_row(context, extra) for extra in extra_hint_entries(context, km["editor_tab"]))
            context_groups = [(rows, True) for rows in chunks if rows]
            if not context_groups:
                continue
            if km.get("exclusive"):
                return context_groups
            groups.extend(context_groups)

    shadowed = {row[0] for rows, _c in groups for row in rows if row[0]}

    grouped: dict[str, list] = {}
    pinned = []
    for ext in sorted(exts, key=domain_rank):
        rows = []
        for km in ext.get_keymaps():
            if not (km.get("key") or km.get("label")) or _is_active(km, "is_hidden"):
                continue
            if not hint_entry_visible(context, km, defaults):
                continue
            row = _row(context, km)
            if km.get("source_label") and not row[0]:
                continue
            if row[0] in shadowed:
                continue
            (pinned if km.get("pin_bottom") else rows).append(row)
        if rows:
            grouped.setdefault(category_for(ext), []).extend(rows)

    base = []
    for category in CATEGORY_ORDER:
        base.extend(grouped.pop(category, ()))
    for category in sorted(grouped):
        base.extend(grouped[category])
    base.extend(row for row in (_row(context, km) for km in extra_hint_entries(context, 'GENERAL'))
                if row[0] not in shadowed)
    base.extend(pinned)
    if base:
        groups.append((base, False))
    return groups


def _draw_shortcut_overlay_callback():
    from .keymap_editor import rows_reused
    with rows_reused():
        _draw_shortcut_overlay(bpy.context)


def _draw_shortcut_overlay(context):
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    from ...core.facade import CoreFacade
    if not CoreFacade.is_editing_weights():
        return

    prefs = getattr(context.window_manager, "superskin_controller_prefs", None)
    if prefs is not None and not prefs.show_shortcut_hint:
        return

    groups = _collect_groups(context)
    if not groups:
        return

    font_id = 0
    blf.size(font_id, _LINE_SIZE)
    key_width = max(
        (blf.dimensions(font_id, drawn)[0]
         for rows in [r for r, _c in groups] + [_base_rows(context)]
         for _raw, drawn, _label in rows),
        default=0.0,
    )

    x_key, bottom_y = CoreFacade.hud_bottom_left()
    x_label = x_key + key_width + _COLUMN_GAP

    total_height = sum(
        _LINE_HEIGHT * len(rows) for rows, _c in groups
    ) + _GROUP_GAP * (len(groups) - 1)

    y = bottom_y + total_height - _LINE_HEIGHT

    for rows, is_context in groups:
        label_color = _ACTIVE_LABEL_COLOR if is_context else _LABEL_COLOR
        for _raw, drawn, label in rows:
            x_row = x_key + key_width - blf.dimensions(font_id, drawn)[0]
            _draw_text(font_id, x_row, y, drawn, _KEY_COLOR)
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
