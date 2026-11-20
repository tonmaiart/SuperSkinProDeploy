
from contextlib import contextmanager


DOMAIN_CATEGORIES = {
    "weight_apply": "Edit Weight",
    "deform_layer_viewer": "Modes",
    "bone_picker": "Modes",
    "overlay_color": "Modes",
    "controller": "Modes",
}

CATEGORY_ORDER = ("Edit Weight", "Modes")


def category_for(ext) -> str:
    return DOMAIN_CATEGORIES.get(ext.get_id()) or ext.get_section_title() or ext.get_id()


def domain_rank(ext) -> int:
    order = list(DOMAIN_CATEGORIES)
    ext_id = ext.get_id()
    return order.index(ext_id) if ext_id in order else len(order)


_MOUSE_KEY_LABELS = {
    'LEFTMOUSE': "LMB",
    'RIGHTMOUSE': "RMB",
    'MIDDLEMOUSE': "MMB",
}


def format_binding(kmi, sep="+") -> str:
    if kmi.type == 'NONE':
        return ""
    parts = []
    if kmi.alt == 1:
        parts.append("Alt")
    if kmi.ctrl == 1:
        parts.append("Ctrl")
    if kmi.shift == 1:
        parts.append("Shift")
    if kmi.oskey == 1:
        parts.append("Cmd")

    key_label = _MOUSE_KEY_LABELS.get(kmi.type)
    if key_label is None:
        try:
            key_label = kmi.bl_rna.properties['type'].enum_items[kmi.type].name
        except (KeyError, TypeError):
            key_label = kmi.type
    parts.append(key_label)
    return sep.join(parts)


_FIXED_TOKEN_NAMES = {"lmb": "Left Mouse", "rmb": "Right Mouse", "mmb": "Middle Mouse"}


def fixed_key_text(key: str) -> str:
    return " ".join(_FIXED_TOKEN_NAMES.get(token.strip().lower(), token.strip()) for token in key.split("+") if token)


SHORTCUT_TABS = (
    ('GENERAL', "General"),
    ('BRUSH', "Brush Mode"),
    ('VERTEX', "Vertex Mode"),
)

_TEMPLATE_ROWS = (
    {
        "tab": 'GENERAL', "group": "List", "label": "Select All Rows",
        "targets": [("User Interface", "superskin.template_list_select_all", 0)], "key": "",
    },
)

_KEYMAP_ITEM_GROUPS = {
    "overlay_color": "Common",
    "deform_layer_viewer": "Common",
    "bone_picker": "Bones",
    "controller": "Bones",
}

EXPORT_FORMAT = "superskinpro.shortcuts"
EXPORT_VERSION = 1

_MODIFIER_FIELDS = ("alt", "ctrl", "shift", "oskey")
_BINDING_FIELDS = ("map_type", "type", "value", "key_modifier") + _MODIFIER_FIELDS


def _addon_rank(km, kmi):
    same = sorted((it for it in km.keymap_items if it.idname == kmi.idname), key=lambda it: it.id)
    return next(i for i, it in enumerate(same) if it.id == kmi.id)


_reuse = None


@contextmanager
def rows_reused():
    global _reuse
    _reuse = []
    try:
        yield
    finally:
        _reuse = None


def shortcut_rows():
    if _reuse is None:
        return _build_shortcut_rows()
    if not _reuse:
        _reuse.append(_build_shortcut_rows())
    return _reuse[0]


def _build_shortcut_rows():
    from ..registry.register_api import UnifiedRegistry

    rows = list(_TEMPLATE_ROWS)
    for ext in UnifiedRegistry.get_all():
        declared = ext.get_shortcut_rows()
        rows.extend(declared)
        covered = {(target[0], target[1]) for row in declared for target in row["targets"]}
        group = _KEYMAP_ITEM_GROUPS.get(ext.get_id()) or category_for(ext)
        by_label = {}
        for km, kmi, label in ext.get_keymap_items():
            target = (km.name, kmi.idname, _addon_rank(km, kmi))
            if target[:2] in covered:
                continue
            row = by_label.get(label)
            if row is None:
                row = by_label[label] = {
                    "tab": 'GENERAL', "group": group, "label": label, "targets": [], "key": "",
                }
                rows.append(row)
            row["targets"].append(target)
    return rows


_LEADING_GROUPS = ("Common",)
_TRAILING_GROUPS = ("List",)


def grouped_rows(rows, tab):
    groups = {}
    for row in rows:
        if row["tab"] == tab:
            groups.setdefault(row.get("group") or "Other", []).append(row)
    return sorted(groups.items(),
                  key=lambda item: (item[0] not in _LEADING_GROUPS) + (item[0] in _TRAILING_GROUPS))


def resolve_target(context, target):
    km_name, idname, rank = target
    user_km = context.window_manager.keyconfigs.user.keymaps.get(km_name)
    if user_km is None:
        return None
    candidates = sorted((it for it in user_km.keymap_items if it.idname == idname), key=lambda it: it.id)
    return candidates[rank] if rank < len(candidates) else None


def sync_linked_targets(context, rows):
    for row in rows:
        primary = resolve_target(context, row["targets"][0]) if row["targets"] else None
        if primary is None:
            continue
        for target in row["targets"][1:]:
            linked = resolve_target(context, target)
            if linked is not None:
                write_binding(linked, {field: getattr(primary, field) for field in _BINDING_FIELDS})


def write_binding(kmi, binding):
    for field in _BINDING_FIELDS:
        if getattr(kmi, field) != binding[field]:
            setattr(kmi, field, binding[field])


def row_is_modified(context, row):
    return any(kmi is not None and kmi.is_user_modified
               for kmi in (resolve_target(context, target) for target in row["targets"]))


def reset_row(context, row):
    kcs = context.window_manager.keyconfigs
    for target in row["targets"]:
        if kcs.addon.keymaps.get(target[0]) is None:
            continue
        kmi = resolve_target(context, target)
        if kmi is not None and kmi.is_user_modified:
            kcs.user.keymaps[target[0]].restore_item_to_default(kmi)


def export_shortcuts(context):
    bindings = []
    for row in shortcut_rows():
        kmi = resolve_target(context, row["targets"][0]) if row["targets"] and not row.get("locked") else None
        if kmi is None:
            continue
        km_name, idname, rank = row["targets"][0]
        entry = {"label": row["label"], "keymap": km_name, "idname": idname, "rank": rank}
        entry.update({field: getattr(kmi, field) for field in _BINDING_FIELDS})
        bindings.append(entry)
    return {"format": EXPORT_FORMAT, "version": EXPORT_VERSION, "bindings": bindings}


def import_shortcuts(context, data):
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT or not isinstance(data.get("bindings"), list):
        raise ValueError("Not a SuperSkinPro shortcut file")
    if data.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported shortcut file version: {data.get('version')}")

    rows = shortcut_rows()
    known = {row["targets"][0] for row in rows if row["targets"] and not row.get("locked")}
    applied = skipped = 0
    for entry in data["bindings"]:
        try:
            target = (entry["keymap"], entry["idname"], entry["rank"])
            kmi = resolve_target(context, target) if target in known else None
            if kmi is None:
                skipped += 1
                continue
            write_binding(kmi, {field: entry[field] for field in _BINDING_FIELDS})
            applied += 1
        except (KeyError, TypeError, ValueError):
            skipped += 1
    sync_linked_targets(context, rows)
    return applied, skipped


def resolve_live_key_text(context, source_label, default=None):
    for row in shortcut_rows():
        if row["label"] != source_label or not row["targets"]:
            continue
        user_kmi = resolve_target(context, row["targets"][0])
        return format_binding(user_kmi) if user_kmi is not None else default
    return default



def hint_links(km) -> tuple:
    link = km.get("editor_label") or km.get("source_label")
    if not link:
        return ()
    return tuple(link) if isinstance(link, (tuple, list)) else (link,)


def _hint_entries():
    from ..registry.register_api import UnifiedRegistry
    for ext in UnifiedRegistry.get_all():
        for km in ext.get_keymaps():
            yield km
            for sub in km.get("sub_keymaps") or ():
                yield sub


def default_hint_labels() -> set:
    return {label for km in _hint_entries() if not km.get("hidden") for label in hint_links(km)}


def _hint_prefs(context):
    return getattr(context.window_manager, "superskin_controller_prefs", None)


_RENAMED_ROWS = {
    "Pick Bone (Hold)": "Bone Picker",
    "Weight Overlay Mode (Toggle)": "Toggle Multi Color Display",
    "Toggle Weight Brush Tool": "Switch Brush / Vertex Tool",
    "Toggle Mask Mode": "Toggle Edit Mask",
    "Add / Scale": "Add / Scale Weight",
    "Smooth / Sharpen": "Smooth / Sharpen Weight",
}


def hint_overrides(context) -> dict:
    prefs = _hint_prefs(context)
    if prefs is None:
        return {}
    import json
    try:
        overrides = json.loads(prefs.hint_overrides)
    except ValueError:
        return {}
    if not isinstance(overrides, dict):
        return {}
    for old, new in _RENAMED_ROWS.items():
        if old in overrides:
            overrides.setdefault(new, overrides.pop(old))
    return overrides


def is_shown_in_hint(context, label, defaults=None) -> bool:
    defaults = default_hint_labels() if defaults is None else defaults
    return bool(hint_overrides(context).get(label, label in defaults))


def set_shown_in_hint(context, label, shown) -> None:
    prefs = _hint_prefs(context)
    if prefs is None:
        return
    import json
    overrides = hint_overrides(context)
    if shown == (label in default_hint_labels()):
        overrides.pop(label, None)
    else:
        overrides[label] = bool(shown)
    prefs.hint_overrides = json.dumps(overrides)


def hint_entry_visible(context, km, defaults=None) -> bool:
    links = hint_links(km)
    return not links or any(is_shown_in_hint(context, label, defaults) for label in links)


def extra_hint_entries(context, tab) -> list:
    linked = {label for km in _hint_entries() for label in hint_links(km)}
    overrides = hint_overrides(context)
    return [
        {"key": row["key"], "label": row["label"], "source_label": row["label"]}
        for row in shortcut_rows()
        if row["tab"] == tab and overrides.get(row["label"]) and row["label"] not in linked
    ]
