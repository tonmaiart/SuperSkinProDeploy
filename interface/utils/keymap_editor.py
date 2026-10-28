

def _resolve_user_kmi(context, km, kmi, rank=0):
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


def resolve_live_key_text(context, source_label, default=None):
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

