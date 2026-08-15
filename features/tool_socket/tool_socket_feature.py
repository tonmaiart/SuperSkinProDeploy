"""ToolSocketFeature — Unified Component Architecture implementation for the
tool socket domain.

Renders a single non-collapsible section (``locked_expanded = True``, same
mechanism as ``clipboard``/``weight_transfer`` -- always shown, no header
toggle) at the very bottom of both the LAYER and SKINNING tabs
(``priority = 9999``, a reserved sort-last sentinel -- see README.md). There
is no separate "More Tools:" caption (``show_section_label = False``) and no
plain-text label at all -- instead the header row is split into two zones
(``layout.split(factor=0.85)``): a left zone holding just the dropdown
(``alignment = 'LEFT'`` + a fixed, compact ``ui_units_x`` instead of
stretching across the whole zone), and a right zone (``alignment = 'RIGHT'``)
holding an icon-only ``INFO`` button flush against the section's right edge,
opening the *currently selected* tool's own ``link`` (re-evaluated every
redraw -- not a fixed link for the socket itself). Dropdown items are the
``section_title`` of every
``UnifiedFeatureExtension`` registered under the sibling tab keys
``'LAYER_SOCKET'`` / ``'SKINNING_SOCKET'`` (never ``'LAYER'``/``'SKINNING'``
directly, so a plugged tool never double-renders in the normal tab loop), and
whichever one is currently selected draws directly beneath the dropdown.

This domain owns no dispatch actions and no persisted settings -- the
dropdown selection is session-only WindowManager state, same category as
``features/clipboard``'s ``active_tab``. See README.md for the full
plug-in contract a new tool must follow to appear in the socket.
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
        socket_tab = f"{tab_key}_SOCKET"
        tools = UnifiedRegistry.get_by_tab(socket_tab)
        if not tools:
            layout.label(text="No additional tools", icon='INFO')
            return

        prop_name = _ENUM_PROP_BY_TAB[tab_key]
        wm = context.window_manager
        selected = UnifiedRegistry.get_by_id(getattr(wm, prop_name, "")) or tools[0]

        # One row, alignment = 'LEFT' -- same treatment as every other tool
        # section's title+info-button pairing now
        # (widget_preferences.py's _draw_section_header()): caps the row's
        # rendered content at its natural size and packs it to the left,
        # leaving unused space to the right instead of stretching into it.
        # Previously this was two zones via split(factor=0.85) with the
        # right zone's alignment = 'RIGHT', which deliberately pushed the
        # INFO button flush against the section's right edge -- moved to
        # hug the dropdown directly instead, for visual consistency with
        # every other section's info button.
        #
        # The dropdown itself still needs its own bounded ui_units_x nested
        # sub-row -- alignment = 'LEFT' alone doesn't stop a `.prop()`
        # combo box from stretching to fill available width the way it
        # would stop a plain `.label()`, so without this the dropdown would
        # still consume the whole row and push the icon to the far edge
        # regardless of the outer row's alignment.
        row = layout.row(align=True)
        row.alignment = 'LEFT'

        dropdown_zone = row.row(align=True)
        dropdown_zone.ui_units_x = 6  # bounded, compact width instead of stretching
        dropdown_zone.prop(wm, prop_name, text="")

        # INFO button reflects whichever tool is CURRENTLY selected in the
        # dropdown, not a fixed link for the socket itself -- re-evaluated
        # every redraw since `selected` is recomputed above each time.
        # Same custom-icon + normal-emboss button as every other tool
        # section's info button (widget_preferences.py's
        # _draw_link_button() template) -- interface.utils.icons is a
        # plain helper module, not panel/widget internals, so importing it
        # here doesn't violate interface/README.md's closed-subsystem
        # invariant (same precedent as interface.utils.utils elsewhere).
        link = selected.get_link()
        if link:
            icon_id = get_help_icon_id()
            icon_kwargs = {"icon_value": icon_id} if icon_id else {"icon": 'INFO'}
            row.operator("wm.url_open", text="", **icon_kwargs).url = link

        # Forward the REAL tab_key ('LAYER'/'SKINNING'), not the
        # _SOCKET-suffixed lookup key -- a plugged tool's
        # draw_section_for_tab() must see exactly what a top-level domain
        # sees. The _SOCKET suffix exists only to keep
        # UnifiedRegistry.get_by_tab('LAYER'/'SKINNING') from also
        # double-rendering these tools directly in the main tab loop.
        selected.draw_section_for_tab(layout, context, tab_key)

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
