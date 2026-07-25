"""DebugConsoleFeature — Unified Component Architecture implementation for the
debug console domain.

Presents ``core_subsystems/debug_logging``'s runtime log buffer as a live,
scrollable panel in the PREFERENCE tab. See ``README.md`` for the full
architecture and the rationale for registering zero dispatch actions.

Owns:
  - SSPrefDebugConsole PropertyGroup (search string, the CollectionProperty
    backing the scrollable log list, and the per-domain visibility collection)
  - SSPrefDebugLogEntry PropertyGroup (one row's worth of log data)
  - SSPrefDebugDomainVisibility PropertyGroup (one registered domain_id's
    show/hide state, for the "Feature Domains" category's sub-filter)
  - SUPERSKIN_UL_debug_console_log (UIList — gives the log view a real,
    native scrollbar via template_list(), with Blender's own filter row
    suppressed since this domain draws its own search box instead)
  - SUPERSKIN_PT_debug_console_categories (popover Panel — the per-category
    visibility toggles, collapsed into one icon-only button to save space)
  - SUPERSKIN_PT_debug_console_feature_domains (nested popover Panel — the
    "Feature Domains" category's per-domain sub-filter)
  - UI layout: draw_section()
  - A bpy.app.timers periodic redraw so the log view updates live while expanded
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_REDRAW_INTERVAL = 0.5
_LIST_ROWS = 4
_FEATURE_DOMAINS_CATEGORY = "feature_domains"
_NATIVE_DECK = "__native__"

_DEBUG_CATEGORIES = CoreFacade.get_debug_categories()

_timer_active = False
_deck_items_cache = []


def _get_deck_items(self, context):
    """Dynamic ``EnumProperty`` items callback for the Debug Deck dropdown.

    First entry is always the built-in "Native" category-tagged log. Every
    entry after that is an ad hoc ("adhoc:"-prefixed) category currently
    present in the log buffer -- see CoreFacade.get_adhoc_debug_categories()
    and CLAUDE.md's "Ad Hoc Debug Deck Exception". No registration step is
    needed for a new deck to appear here: it shows up the moment any code
    calls ``debug_log("adhoc:<name>", ...)`` and drops out again once its
    entries age out of the (200-line, F3-reset) buffer.

    The result is cached in a module-level list rather than returned as a
    fresh throwaway list, since Blender does not keep the strings inside a
    dynamic EnumProperty items() return value alive on its own -- a
    module-level reference is the standard workaround for the resulting
    use-after-free/crash risk.
    """
    global _deck_items_cache
    items = [(_NATIVE_DECK, "Native", "Built-in, category-tagged debug log")]
    for category in CoreFacade.get_adhoc_debug_categories():
        deck_name = category.split(":", 1)[1]
        items.append((category, deck_name, f"Ad hoc debug deck: {deck_name}"))
    _deck_items_cache = items
    return _deck_items_cache


def _tick_redraw():
    """Periodic bpy.app.timers callback — tag_redraw() while expanded, else idle."""
    if not _timer_active:
        return None
    wm = bpy.context.window_manager
    if getattr(wm, "superskin_debug_console_expanded", False):
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    return _REDRAW_INTERVAL


def _visible_categories(context) -> set:
    """Category names whose checkbox is currently checked (display filter only)."""
    debug_toggles = context.window_manager.superskin_prefs.debug
    return {cat for cat in _DEBUG_CATEGORIES if getattr(debug_toggles, cat, True)}


def _sync_domain_visibility_collection(dc) -> None:
    """Add an entry (default visible) for every registered domain_id not yet
    tracked. Never removes/clears existing entries, so a user's toggle state
    survives across redraws -- unlike ``log_items``, this collection is real
    persistent-for-the-session state, not a rebuilt-every-draw mirror."""
    known = {entry.name for entry in dc.domain_visibility}
    for ext in UnifiedRegistry.get_all():
        domain_id = ext.get_id()
        if domain_id not in known:
            entry = dc.domain_visibility.add()
            entry.name = domain_id
            entry.visible = True


def _guess_domain_for_message(message: str, domain_ids_by_len_desc: list) -> str | None:
    """Best-effort match of a "feature_domains" message to a domain_id, based
    on the ``f"{domain_id}.execute() ..."`` / ``f"{domain_id}: ..."`` prefix
    convention already used by weight_apply/weight_transfer's debug_log()
    calls. Longest-id-first avoids a short id false-matching as a prefix of
    a longer one. Returns None (never hidden) if no known id matches, so an
    unrecognized message is never silently dropped by the per-domain filter.
    """
    for domain_id in domain_ids_by_len_desc:
        if message.startswith(domain_id + ".") or message.startswith(domain_id + ":"):
            return domain_id
    return None


def get_visible_entries(context) -> list[dict]:
    """Buffered log entries matching the current deck + visibility filter + search text.

    Shared by draw_section() (for the on-screen list) and ops.py's Copy Log
    operator, so the two can never disagree about what "currently filtered"
    means.

    When an ad hoc deck is selected (``dc.active_deck != _NATIVE_DECK``),
    this returns only that exact category's entries -- the registered-
    category visibility checkboxes and the per-domain sub-filter only apply
    to the Native deck, since ad hoc categories have no checkbox of their own.
    """
    dc = context.window_manager.superskin_debug_console_prefs
    _sync_domain_visibility_collection(dc)

    if dc.active_deck != _NATIVE_DECK:
        return CoreFacade.get_debug_logs(dc.active_deck, dc.search_query)

    visible_categories = _visible_categories(context)
    domain_visible = {entry.name: entry.visible for entry in dc.domain_visibility}
    domain_ids_by_len_desc = sorted(domain_visible, key=len, reverse=True)

    entries = CoreFacade.get_debug_logs(None, dc.search_query)
    result = []
    for e in entries:
        if e["category"] not in visible_categories:
            continue
        if e["category"] == _FEATURE_DOMAINS_CATEGORY:
            domain = _guess_domain_for_message(e["message"], domain_ids_by_len_desc)
            if domain is not None and not domain_visible.get(domain, True):
                continue
        result.append(e)
    return result


# ==============================================================================
# Property Groups
# ==============================================================================

class SSPrefDebugLogEntry(bpy.types.PropertyGroup):
    """One row of the scrollable log list (mirrors a DebugLogService buffer entry)."""
    timestamp: bpy.props.StringProperty()
    category: bpy.props.StringProperty()
    message: bpy.props.StringProperty()


class SSPrefDebugDomainVisibility(bpy.types.PropertyGroup):
    """One registered domain_id's show/hide state (``.name`` holds the domain_id).

    Only consulted for entries tagged category == "feature_domains" -- see
    ``_guess_domain_for_message()``.
    """
    visible: bpy.props.BoolProperty(default=True)


class SSPrefDebugConsole(bpy.types.PropertyGroup):
    """Transient UI-only state for the debug console: search text, the list
    collection template_list() reads from, and per-domain visibility."""

    search_query: bpy.props.StringProperty(
        name="Search",
        description="Only show log entries whose message contains this text",
        default="",
    )
    active_deck: bpy.props.EnumProperty(
        name="Debug Deck",
        description="Native shows the built-in category-tagged log; every other "
                    "entry is an ad hoc debug deck currently present in the buffer",
        items=_get_deck_items,
    )
    log_items: bpy.props.CollectionProperty(type=SSPrefDebugLogEntry)
    log_active_index: bpy.props.IntProperty(default=0)
    domain_visibility: bpy.props.CollectionProperty(type=SSPrefDebugDomainVisibility)


# ==============================================================================
# UIList — gives the log view a real scrollbar via template_list()
# ==============================================================================

class SUPERSKIN_UL_debug_console_log(bpy.types.UIList):
    """Read-only scrollable list of debug log entries.

    Suppresses Blender's own built-in filter row the same way
    ``interface.template_ui.SuperSkinListMixin`` does (``use_filter_show =
    False`` + a no-op ``draw_filter()``) -- this domain already draws its own
    search box, so the built-in one would be a confusing second search UI.
    The mixin itself isn't used here since its other hooks (``domain()``,
    ``get_item_key()``, row-click operator wiring, ...) are built for
    selectable bone/layer rows and don't fit a read-only log viewer.
    """
    bl_idname = "SUPERSKIN_UL_debug_console_log"

    use_filter_show = False

    def draw_filter(self, context, layout):
        pass

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=f"[{item.timestamp}][{item.category.upper()}] {item.message}")


# ==============================================================================
# Popover Panels — per-category (and per-domain) visibility toggles
# ==============================================================================

class SUPERSKIN_PT_debug_console_feature_domains(bpy.types.Panel):
    """Nested popover — per-domain sub-filter for the "Feature Domains"
    category, opened from a button on that category's row in the parent
    popover. Lets a specific domain's dispatch-log lines (weight_apply,
    mirror, clipboard, ...) be hidden individually instead of only as a
    whole category."""
    bl_idname = "SUPERSKIN_PT_debug_console_feature_domains"
    bl_label = "Feature Domains"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- UI is the N-panel sidebar region itself, so a Panel
    # registered against it (with no bl_category restricting which tab it
    # docks under) gets auto-listed as its own separate docked panel (under
    # a default "Misc" tab) in ADDITION to being invocable via
    # layout.popover(). HEADER-region panels are only ever drawn when
    # explicitly invoked (popover()/menu), never auto-docked -- the same
    # convention Blender's own built-in popovers use (e.g.
    # VIEW3D_PT_shading_lighting).
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        layout.use_property_decorate = False
        dc = context.window_manager.superskin_debug_console_prefs
        _sync_domain_visibility_collection(dc)

        col = layout.column(align=True)
        for entry in dc.domain_visibility:
            col.prop(
                entry, "visible",
                text=entry.name.replace("_", " ").title(),
                icon='HIDE_OFF' if entry.visible else 'HIDE_ON',
                toggle=True,
            )


class SUPERSKIN_PT_debug_console_categories(bpy.types.Panel):
    """Popover content for the "Visible Categories" button — one eye-icon
    toggle per category, does not auto-close between clicks like a Menu
    would, so multiple categories can be toggled in one pass."""
    bl_idname = "SUPERSKIN_PT_debug_console_categories"
    bl_label = "Visible Categories"
    bl_space_type = 'VIEW_3D'
    # HEADER, not UI -- see SUPERSKIN_PT_debug_console_feature_domains above
    # for why.
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        layout.use_property_decorate = False
        debug_toggles = context.window_manager.superskin_prefs.debug
        col = layout.column(align=True)
        for cat in _DEBUG_CATEGORIES:
            visible = getattr(debug_toggles, cat)
            if cat == _FEATURE_DOMAINS_CATEGORY:
                row = col.row(align=True)
                row.prop(
                    debug_toggles, cat,
                    text=cat.replace("_", " ").title(),
                    icon='HIDE_OFF' if visible else 'HIDE_ON',
                    toggle=True,
                )
                row.popover(
                    SUPERSKIN_PT_debug_console_feature_domains.bl_idname,
                    text="", icon='DOWNARROW_HLT',
                )
            else:
                col.prop(
                    debug_toggles, cat,
                    text=cat.replace("_", " ").title(),
                    icon='HIDE_OFF' if visible else 'HIDE_ON',
                    toggle=True,
                )


# ==============================================================================
# DebugConsoleFeature — UnifiedFeatureExtension
# ==============================================================================

class DebugConsoleFeature(UnifiedFeatureExtension):
    """Unified extension for the Debug Console domain."""

    # ── Configuration (class attributes) ──────────────────────────────────

    domain_id = "debug_console"
    actions = []
    section_title = "Debug Console"
    draw_tab = "PREFERENCE"

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        # No actions registered — see README.md "Why no dispatch actions".
        return {"status": "CANCELLED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        dc = context.window_manager.superskin_debug_console_prefs
        layout.use_property_decorate = False

        layout.prop(dc, "active_deck", text="")

        debug_toggles = context.window_manager.superskin_prefs.debug
        all_visible = all(getattr(debug_toggles, cat) for cat in _DEBUG_CATEGORIES)

        row = layout.row(align=True)
        if dc.active_deck == _NATIVE_DECK:
            row.popover(
                SUPERSKIN_PT_debug_console_categories.bl_idname,
                text="", icon='HIDE_OFF' if all_visible else 'HIDE_ON',
            )
        row.prop(dc, "search_query", text="", icon='VIEWZOOM')
        row.operator("superskin.copy_debug_log", text="", icon='COPYDOWN')
        row.operator("superskin.clear_debug_log", text="", icon='TRASH')

        entries = get_visible_entries(context)
        dc.log_items.clear()
        for entry in entries:
            row_item = dc.log_items.add()
            row_item.timestamp = entry["timestamp"]
            row_item.category = entry["category"]
            row_item.message = entry["message"]
        dc.log_active_index = max(0, len(dc.log_items) - 1)

        layout.template_list(
            SUPERSKIN_UL_debug_console_log.bl_idname, "",
            dc, "log_items", dc, "log_active_index",
            rows=_LIST_ROWS, maxrows=_LIST_ROWS,
        )


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

_classes = (
    SSPrefDebugLogEntry,
    SSPrefDebugDomainVisibility,
    SSPrefDebugConsole,
    SUPERSKIN_UL_debug_console_log,
    SUPERSKIN_PT_debug_console_feature_domains,
    SUPERSKIN_PT_debug_console_categories,
)


def register():
    """Register PropertyGroups/UIList/Panels, the extension, and start the redraw timer."""
    global _timer_active
    for cls in _classes:
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.superskin_debug_console_prefs = bpy.props.PointerProperty(
        type=SSPrefDebugConsole, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(DebugConsoleFeature())

    _timer_active = True
    if not bpy.app.timers.is_registered(_tick_redraw):
        bpy.app.timers.register(_tick_redraw, first_interval=_REDRAW_INTERVAL)


def unregister():
    """Stop the redraw timer and unregister everything this domain registered."""
    global _timer_active
    _timer_active = False
    if bpy.app.timers.is_registered(_tick_redraw):
        bpy.app.timers.unregister(_tick_redraw)

    UnifiedRegistry.unregister("debug_console")
    try:
        del bpy.types.WindowManager.superskin_debug_console_prefs
    except Exception:
        pass
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
