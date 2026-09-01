"""Shared in-panel keyboard-shortcut rebind widget.

Used by ``interface/widget_preferences.py``'s settings popover to draw one
click-to-rebind row per SuperSkinPro keymap item, gathered across every
registered domain via ``UnifiedFeatureExtension.get_keymap_items()``.

Each domain's own ``keymap.py`` registers its default bindings on the
*addon* keyconfig (``wm.keyconfigs.addon``). Rebinding a shortcut only
takes effect (and persists) on the separate, live *user* keyconfig
(``wm.keyconfigs.user``), which Blender merges from the addon defaults at
startup -- so every row here must resolve its addon-keyconfig item to the
matching user-keyconfig item before drawing anything editable.

That resolution does NOT compare ``KeyMapItem.id`` at all -- confirmed
empirically (this session, against this addon's own real, persisted
``userpref.blend``) that addon-side ``.id`` is NOT stable across a Blender
restart: the *addon* keyconfig's copy of the shared, real ``'Mesh'``
keymap (space_type='EMPTY' -- SuperSkinPro's shortcuts live here, not a
private custom-named keymap, because only Blender's own recognized
keymap names get activated by its built-in mode/context polling) starts
its ``.id`` counter fresh at 1 every single process launch, while the
*user* keyconfig's already-merged copy of the same items keeps whatever
``.id`` values got saved to ``userpref.blend`` the last time Blender's
preferences were saved -- observed as a constant, session-specific
offset (e.g. addon id 9 vs. saved user id 102) that guarantees every
``.id``-based lookup misses after a restart, even though nothing is
actually wrong with either keymap. Blender does not rewrite an
already-merged user item's ``.id`` to match a freshly restarted addon
counter, since doing so could silently discard a real rebind history.
See ``docs/bug-history`` for this session's diagnosis (the value shown
in-panel before this fix, and the "list goes empty" regression from an
interim id-based attempt at fixing it).

Instead, resolution matches purely on ``idname`` plus *rank*: the 0-based
position of this item among every item sharing the same ``idname`` in
this keymap, ordered by ascending ``.id`` -- which recovers creation
order on both sides (ids are assigned monotonically at each
``keymap_items.new()`` call) without depending on the specific numbers
matching across configs. No built-in Blender operator shares any of
SuperSkinPro's own idnames (``superskin.*``, ``object.mw_*``), so a
same-idname group is always exactly this addon's own siblings for that
binding (e.g. ``vertex_selector``'s Wheel-Up/Wheel-Down Grow/Shrink
bindings, both ``superskin.grow_shrink_selection`` -- Wheel-Up is always
registered first in that domain's own ``keymap.py``, so it's always
rank 0 on both the addon and the user side).
"""


def _resolve_user_kmi(context, km, kmi, rank=0):
    """Return the live, editable ``wm.keyconfigs.user`` counterpart of
    *kmi* (registered on the addon keyconfig by some domain's
    ``keymap.py``), or ``None`` if it can't be found (e.g. mid-reload).

    *rank* is the 0-based position of *kmi* among every addon-side item
    sharing its ``idname`` (in the order ``draw_keymap_section()``
    encounters them, which mirrors each domain's own creation-call
    order) -- see this module's docstring for why identity is tracked
    this way instead of via ``.id`` equality."""
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


# Category grouping -- the single source of truth for "which category does
# this domain's shortcuts belong to", shared by SUPERSKIN_PT_shortcuts_editor
# (interface/ops_preferences.py, groups the click-to-rebind list) AND
# interface/utils/shortcut_overlay.py (groups the viewport HUD) so the two
# never drift apart into different groupings for the same domain. Keyed by
# domain_id (``UnifiedFeatureExtension.get_id()``); a domain missing from
# this table falls back to its own section title/id as a category of one, so
# a newly added domain's shortcuts still show up instead of silently
# disappearing from either view.
DOMAIN_CATEGORIES = {
    "circle_tool_adjust": "Vertex Selection",
    "vertex_selector": "Vertex Selection",
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
    """Return every registered domain's ``get_keymap_items()`` triples,
    bucketed by :data:`category_for`, in a deterministic order (fixed
    :data:`CATEGORY_ORDER` first, then any unmapped category
    alphabetically) -- mirrors
    ``interface/utils/shortcut_overlay.py``'s ``_collect_domains()``
    "normal mode" grouping exactly, so ``SUPERSKIN_PT_shortcuts_editor``
    (interface/ops_preferences.py) shows the same category headers, in
    the same order, as the viewport HUD."""
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


# Live key-text formatting -- the single routine that turns a KeyMapItem's
# CURRENT binding into the "Alt+LMB" / "Alt+3" / "Shift+R" style text used
# throughout this addon. Both the click-to-rebind rows below (which read the
# binding straight off the live bpy.types.KeyMapItem via kmi.prop(...), so
# they need no separate formatting) and, more importantly,
# interface/utils/shortcut_overlay.py's viewport HUD (whose entries used to
# be hand-typed literals with no connection to the real, possibly-rebound
# keymap) call this so a rebind shows up identically -- and immediately --
# in both places instead of the HUD silently going stale.
_MOUSE_KEY_LABELS = {
    'LEFTMOUSE': "LMB",
    'RIGHTMOUSE': "RMB",
    'MIDDLEMOUSE': "MMB",
}


def format_binding(kmi, sep="+") -> str:
    """Human-readable "Alt+Shift+RMB"-style text for *kmi*'s current
    ``type``/modifier state. *sep* defaults to a bare "+" (used by
    ``resolve_live_key_text()``'s viewport-HUD callers); the click-to-rebind
    button in ``draw_keymap_section()`` below passes ``" + "`` instead, to
    read as spaced-out button text rather than a compact HUD hint."""
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
    """Return :func:`format_binding` of the live, currently-bound
    ``KeyMapItem`` whose registration label (as returned by some
    domain's ``keymap.py::get_registered_keymap_items()``) equals
    *source_label*, searched across every registered domain's
    ``get_keymap_items()`` -- or *default* if no such item exists, or it
    can't be resolved right now (e.g. mid-reload).

    This is how a ``shortcut_overlay.py`` HUD entry stays in sync with a
    rebind made through ``SUPERSKIN_PT_shortcuts_editor``: both read the
    same live ``wm.keyconfigs.user`` item, via the same rank-based
    resolution as :func:`draw_keymap_section`, rather than the HUD
    keeping its own hand-typed copy of the default text."""
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
    """Draw one rebind row per resolvable ``(km, kmi, label)`` triple in
    *items* -- a plain label, a click-to-rebind button
    (``superskin.rebind_shortcut``, see ``interface/ops_preferences.py``),
    and a per-item "restore to default" icon that only appears once that
    item has actually been rebound.

    The rebind button is a custom modal operator rather than Blender's own
    native ``kmi.prop("type", full_event=True)`` widget -- the native
    widget's displayed text ("Alt 1") is drawn entirely inside Blender's C
    UI code and can't be reformatted from Python, so getting this addon's
    "Alt + 1" spaced-"+"-separated style (``format_binding(sep=" + ")``)
    requires reimplementing the click-then-press-a-key capture as our own
    modal operator instead.

    Does not draw a "reset all" action itself -- see
    ``superskin.reset_all_shortcuts`` (``interface/ops_preferences.py``)
    for the bulk-reset operator, wired up separately by the caller.
    """
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
