"""Shared in-panel keyboard-shortcut rebind widget."""


def _resolve_user_kmi(context, km, kmi, rank=0):
    """Return the live, editable ``wm.keyconfigs.user`` counterpart of *kmi* (registered on the
    addon keyconfig by."""
    user_kc = context.window_manager.keyconfigs.user
    user_km = user_kc.keymaps.get(km.name)
    if user_km is None:
        return None
    candidates = sorted(
        (it for it in user_km.keymap_items if it.idname == kmi.idname),
        key=lambda it: it.id,
    )
    if rank < len(candidates):
        return candidates[rank]
    return None


DOMAIN_CATEGORIES = {
    "deform_bone_viewer": "Vertex Selection",
    "weight_apply": "Edit Weight",
    "overlay_color": "Display",
    "bone_picker": "Misc",
    "controller": "Misc",
}

CATEGORY_ORDER = ("Vertex Selection", "Edit Weight", "Display", "Misc")


def category_for(ext) -> str:
    return DOMAIN_CATEGORIES.get(ext.get_id()) or ext.get_section_title() or ext.get_id()


def grouped_keymap_items_by_category() -> list[tuple[str, list]]:
    """Return every registered domain's ``get_keymap_items()`` triples, bucketed by
    :data:`category_for`, in a."""
    from ..registry.register_api import UnifiedRegistry

    exts = sorted(UnifiedRegistry.get_all(), key=lambda e: (e.get_priority(), e.get_id()))
    grouped: dict[str, list] = {}
    for ext in exts:
        items = ext.get_keymap_items()
        if items:
            grouped.setdefault(category_for(ext), []).extend(items)

    ordered = []
    for category in CATEGORY_ORDER:
        items = grouped.pop(category, None)
        if items:
            ordered.append((category, items))
    for category in sorted(grouped):
        ordered.append((category, grouped[category]))
    return ordered


_MOUSE_KEY_LABELS = {
    'LEFTMOUSE': "LMB",
    'RIGHTMOUSE': "RMB",
    'MIDDLEMOUSE': "MMB",
}


def format_binding(kmi, sep="+") -> str:
    """Human-readable "Alt+Shift+RMB"-style text for *kmi*'s current ``type``/modifier state."""
    parts = []
    if kmi.alt:
        parts.append("Alt")
    if kmi.ctrl:
        parts.append("Ctrl")
    if kmi.shift:
        parts.append("Shift")
    if kmi.oskey:
        parts.append("Cmd")

    key_label = _MOUSE_KEY_LABELS.get(kmi.type)
    if key_label is None:
        try:
            key_label = kmi.bl_rna.properties['type'].enum_items[kmi.type].name
        except (KeyError, TypeError):
            key_label = kmi.type
    parts.append(key_label)
    return sep.join(parts)


def resolve_live_key_text(context, source_label, default=None):
    """Return :func:`format_binding` of the live, currently-bound ``KeyMapItem`` whose
    registration label (as."""
    from ..registry.register_api import UnifiedRegistry

    ranks = {}
    for ext in UnifiedRegistry.get_all():
        for km, kmi, label in ext.get_keymap_items():
            key = (km.name, kmi.idname)
            rank = ranks.get(key, 0)
            ranks[key] = rank + 1
            if label != source_label:
                continue
            user_kmi = _resolve_user_kmi(context, km, kmi, rank)
            return format_binding(user_kmi) if user_kmi is not None else default
    return default


def draw_keymap_section(layout, context, items) -> None:
    """Draw one rebind row per resolvable ``(km, kmi, label)`` triple in *items*."""
    ranks = {}
    for km, kmi, label in items:
        key = (km.name, kmi.idname)
        rank = ranks.get(key, 0)
        ranks[key] = rank + 1

        user_kmi = _resolve_user_kmi(context, km, kmi, rank)
        if user_kmi is None:
            continue

        row = layout.row(align=True)
        row.label(text=label)
        rebind = row.operator(
            "superskin.rebind_shortcut",
            text=format_binding(user_kmi, sep=" + "),
        )
        rebind.km_name = km.name
        rebind.kmi_id = user_kmi.id
        if user_kmi.is_user_modified:
            row.operator(
                "preferences.keyitem_restore", text="", icon='BACK',
            ).item_id = user_kmi.id
