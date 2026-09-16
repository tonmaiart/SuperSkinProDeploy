"""ToolSocketFeature — Unified Component Architecture implementation for the
tool socket domain.

Renders a single non-collapsible section (``locked_expanded = True``, same
mechanism as ``clipboard``/``weight_transfer`` -- always shown, no header
toggle) at the very bottom of both the LAYER and SKINNING tabs
(``priority = 9999``, a reserved sort-last sentinel -- see docs/domains/tool_socket.md).
There is no separate "More Tools:" caption (``show_section_label = False``)
and no plain-text label at all -- instead the header row and the currently
selected tool's own content are drawn into one shared ``layout.column(align=True)``
(2026-09-09, per explicit user feedback -- a ``layout.box()`` wrapper was
tried the same day and reverted: a solid box background around a
single-button tool like Weight Export read as a "double box" since Blender
already draws each button/dropdown with its own bordered look, which felt
heavy and closed-in; a plain aligned column merges the header and
whatever the plugged tool draws directly beneath it into one flush stack
without adding a background of its own). The header row itself has gone
through several presentations, all the same day and round of feedback,
landing (2026-09-09, latest) on the exact shape every OTHER locked_expanded
domain's own header uses (compare against ``weight_apply``'s plain
"Apply:" caption, drawn by ``widget_preferences.py``'s
``_draw_section_header()``): the *currently selected* tool's own
``section_title``, with the same ``":"`` suffix, as a plain
``layout.label()`` on the LEFT, and the dropdown -- still the exact same
searchable ``EnumProperty`` combo underneath, just an ``icon_only=True``
square button, ``icon='TOOL_SETTINGS'`` -- pushed to the far RIGHT, instead
of leading the row the way it did in the immediately preceding revision.
Fixed-ratio ``split(factor=0.85)`` (not a plain row + auto-expand) -- the
same idiom ``_draw_section_header()`` itself uses for its own "title left,
link button right" shape; ``row.separator_spacer()`` was tried there for
right-alignment and reverted for miscomputing the N-panel's required width,
so this codebase avoids it everywhere in favor of ``split()`` instead. The
whole header row carries ``scale_y = 1.3`` (an earlier same-day follow-up
request, to make the dropdown button noticeably taller than Blender's
default row height) applied once on the outer row before the split, so both
zones stay the same height. When ``_LINK_BUTTON_ENABLED`` and the selected
tool's ``link`` are both set (the flag is currently ``False``, so in
practice this never fires today), an INFO button is drawn into the SAME
right-hand zone, after the dropdown, so it ends up at the very right edge
with the dropdown just to its left -- re-evaluated every redraw since
``selected`` is recomputed above each time, not a fixed link for the socket
itself. Dropdown
items are the ``section_title`` of every
``UnifiedFeatureExtension`` registered under the sibling tab keys
``'LAYER_SOCKET'`` / ``'SKINNING_SOCKET'`` (never ``'LAYER'``/``'SKINNING'``
directly, so a plugged tool never double-renders in the normal tab loop), and
whichever one is currently selected draws directly beneath the header row,
inside the same aligned column.

This domain owns no dispatch actions and no persisted settings -- the
dropdown selection is session-only WindowManager state (``SKIP_SAVE``),
same category as most other UI-state PropertyGroups in this codebase. See
docs/domains/tool_socket.md for the full plug-in contract a new tool must
follow to appear in the socket.
"""

import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...interface.utils.icons import get_help_icon_id
from ...core.facade import CoreFacade

_socket_items_cache_layer = []
_socket_items_cache_skinning = []


def _get_socket_items_layer(self, context):
    """Dynamic ``EnumProperty`` items callback for the LAYER tab's dropdown.

    Cached in a module-level list rather than returned as a fresh throwaway
    list, since Blender does not keep the strings inside a dynamic
    EnumProperty items() return value alive on its own -- a module-level
    reference is the standard workaround for the resulting use-after-free
    crash risk (mirrors debug_console_feature.py's ``_get_deck_items()``).
    """
    global _socket_items_cache_layer
    _socket_items_cache_layer = [
        (e.get_id(), e.get_section_title(), "")
        for e in UnifiedRegistry.get_by_tab('LAYER_SOCKET')
    ]
    return _socket_items_cache_layer


def _get_socket_items_skinning(self, context):
    """Dynamic ``EnumProperty`` items callback for the SKINNING tab's dropdown."""
    global _socket_items_cache_skinning
    _socket_items_cache_skinning = [
        (e.get_id(), e.get_section_title(), "")
        for e in UnifiedRegistry.get_by_tab('SKINNING_SOCKET')
    ]
    return _socket_items_cache_skinning


_ENUM_PROP_BY_TAB = {
    'LAYER': "superskin_tool_socket_active_layer",
    'SKINNING': "superskin_tool_socket_active_skinning",
}

# Temporary local kill-switch for the socket's own INFO button, mirroring
# `interface/widget_preferences.py`'s `_LINK_BUTTONS_ENABLED` -- kept as its
# own constant here (not imported from widget_preferences.py) since this
# domain's closed-subsystem contract with interface/ only allows going
# through the public Registry API, never that module's private internals.
# Set back to True to restore.
_LINK_BUTTON_ENABLED = False


def draw_tool_socket_section(layout, context, tab_key: str) -> None:
    """Draw the socket's header row (currently selected tool's title +
    dropdown) and the selected tool's own content beneath it, for *tab_key*
    ('LAYER' or 'SKINNING').

    Moved out of ``ToolSocketFeature.draw_section_for_tab()`` (2026-09-14,
    see docs/domains/skin_tools_ui.md) into this plain module function so
    both ``features/object_tools_ui/`` and ``features/skin_tools_ui/`` can
    call it directly -- exposed via this domain's ``public_api.py`` since
    those packages no longer live inside ``tool_socket``. Draws with NO
    collapsible-box chrome of its own (``locked_expanded=True`` +
    ``show_section_label=False`` meant this was already true before the
    move -- the caller places this at a fixed point in its own loop rather
    than through ``UnifiedRegistry.draw_collapsible_section()``).
    """
    socket_tab = f"{tab_key}_SOCKET"
    tools = UnifiedRegistry.get_by_tab(socket_tab)
    if not tools:
        return

    prop_name = _ENUM_PROP_BY_TAB[tab_key]
    wm = context.window_manager
    selected = UnifiedRegistry.get_by_id(getattr(wm, prop_name, "")) or tools[0]

    # The dropdown row and the selected tool's own content share one
    # aligned column (2026-09-09, per explicit user feedback -- a
    # layout.box() wrapper was tried and reverted the same day: a solid
    # box background around a single-button tool like Weight Export
    # read as a "double box" and felt heavy/closed-in, since Blender
    # already draws each button/dropdown with its own bordered look).
    # align=True merges the dropdown and whatever the plugged tool
    # draws directly beneath it into one flush stack, with no
    # background of its own.
    col = layout.column(align=True)

    # INFO button reflects whichever tool is CURRENTLY selected in the
    # dropdown, not a fixed link for the socket itself -- re-evaluated
    # every redraw since `selected` is recomputed above each time.
    link = selected.get_link()
    show_info_button = _LINK_BUTTON_ENABLED and bool(link)

    # Header row styled like every OTHER domain's own locked_expanded
    # section header (2026-09-09, per explicit user request comparing
    # this directly against weight_apply's plain "Apply:" caption) --
    # the CURRENTLY selected tool's section_title, with the same ":"
    # suffix widget_preferences.py's _draw_section_header() appends for
    # every other locked_expanded section, sits on the LEFT; the
    # dropdown -- still the same searchable EnumProperty combo
    # underneath, just an icon_only=True square button -- is pushed to
    # the far RIGHT instead of leading the row. Fixed-ratio split (not a
    # plain row + auto-expand) -- the same idiom _draw_section_header()
    # itself uses for its own "title left, link button right" shape;
    # row.separator_spacer() was tried there for right-alignment and
    # reverted for miscomputing the N-panel's required width, so this
    # codebase avoids it everywhere in favor of split() instead.
    header_row = col.row(align=False)
    header_row.scale_y = 1.3  # noticeably taller than Blender's default row height, per an earlier explicit user request
    split = header_row.split(factor=0.85)

    label_zone = split.row(align=False)
    label_zone.label(text=f"{selected.get_section_title()}:")

    right_zone = split.row(align=True)
    right_zone.alignment = 'RIGHT'
    # icon='TOOL_SETTINGS' (2026-09-09, per explicit user request) --
    # icon='DOWNARROW_HLT' was tried first and dropped for reading as a
    # redundant "this is a dropdown" arrow (see deform_bone_viewer's own
    # icon-only "Clipboard Bone/Layer Weight" menu buttons for the look
    # being matched); a plain no-icon button was tried next but read too
    # blank. TOOL_SETTINGS is a generic "tools" glyph rather than an
    # arrow, giving the button a meaningful icon without implying it's
    # specifically a dropdown. The dynamic items built in
    # _get_socket_items_layer()/_get_socket_items_skinning() still carry
    # no per-item icon of their own, so this is a fixed override, not
    # the current selection's own icon.
    right_zone.prop(wm, prop_name, text="", icon_only=True, icon='TOOL_SETTINGS')

    if show_info_button:
        # Same custom-icon + normal-emboss button as every other tool
        # section's info button (widget_preferences.py's
        # _draw_link_button() template) -- interface.utils.icons is a
        # plain helper module, not panel/widget internals, so importing
        # it here doesn't violate docs/core-interfaces/interface.md's
        # closed-subsystem invariant (same precedent as
        # interface.utils.utils elsewhere). Drawn after the dropdown so
        # it sits at the very right edge, dropdown just to its left.
        icon_id = get_help_icon_id()
        icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'INFO'}
        right_zone.operator("wm.url_open", text="", **icon_kwargs).url = link

    # Forward the REAL tab_key ('LAYER'/'SKINNING'), not the
    # _SOCKET-suffixed lookup key -- a plugged tool's
    # draw_section_for_tab() must see exactly what a top-level domain
    # sees. The _SOCKET suffix exists only to keep
    # UnifiedRegistry.get_by_tab('LAYER'/'SKINNING') from also
    # double-rendering these tools directly in the main tab loop.
    selected.draw_section_for_tab(col, context, tab_key)


class ToolSocketFeature(UnifiedFeatureExtension):
    domain_id = "tool_socket"
    actions = []
    section_title = "More Tools"
    draw_tab = ('LAYER', 'SKINNING')
    priority = 9999
    collapsible = True
    locked_expanded = True  # non-collapsible, same mechanism as clipboard/weight_transfer -- always shown, no header toggle
    show_section_label = False  # no separate "More Tools:" caption -- the dropdown itself is the header

    def execute(self, action, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}

    def draw_section(self, layout, context) -> None:
        # Never called directly -- this extension always renders through
        # draw_section_for_tab() since draw_tab is multi-tab.
        pass

    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        """UI moved to the module-level draw_tool_socket_section() above,
        called directly (unwrapped -- no collapsible-box chrome, see that
        function's docstring) by object_tools_ui/skin_tools_ui via this
        domain's public_api.py. This method is a stub -- see
        docs/domains/skin_tools_ui.md."""
        pass

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


def register():
    if not hasattr(bpy.types.WindowManager, "superskin_tool_socket_active_layer"):
        bpy.types.WindowManager.superskin_tool_socket_active_layer = bpy.props.EnumProperty(
            name="",  # dynamic-items enums render `name` as a disabled header row
            # at the top of the dropdown popup -- leave blank so no "Others"
            # (or any other) caption appears above the real, selectable items.
            description="Which extra tool is currently shown in the Layer tab's socket",
            items=_get_socket_items_layer,
        )
    if not hasattr(bpy.types.WindowManager, "superskin_tool_socket_active_skinning"):
        bpy.types.WindowManager.superskin_tool_socket_active_skinning = bpy.props.EnumProperty(
            name="",
            description="Which extra tool is currently shown in the Skinning tab's socket",
            items=_get_socket_items_skinning,
        )
    UnifiedRegistry.register(ToolSocketFeature())


def unregister():
    UnifiedRegistry.unregister("tool_socket")
    for attr in ("superskin_tool_socket_active_skinning", "superskin_tool_socket_active_layer"):
        if hasattr(bpy.types.WindowManager, attr):
            try:
                delattr(bpy.types.WindowManager, attr)
            except Exception:
                pass
