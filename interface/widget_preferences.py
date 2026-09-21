"""SuperSkinPro N-panel sidebar — interface-split UI (no tab bar)."""

from .utils import icons as _icons

_LINK_BUTTONS_ENABLED = False

_DOCS_URL = "https://docs.superskinpro.com/"
_DISCORD_URL = "https://discord.gg/BCEJZBnTfM"
_TUTORIALS_URL = None


# =========================================================================
#  Collapsible section helper — shared chrome, reused by object_tools_ui /
#  skin_tools_ui (via UnifiedRegistry.draw_collapsible_section()) and by
#  the PREFERENCE-tab body (_draw_preferences(), further down this file)
# =========================================================================

def draw_collapsible_box_ext(layout, context, ext, tab_key, force_locked_expanded=False, draw_body=None):
    """Draw a section for a ``UnifiedFeatureExtension`` under *tab_key*."""
    body_fn = draw_body if draw_body is not None else (
        lambda body_layout, body_context: ext.draw_section_for_tab(body_layout, body_context, tab_key)
    )

    if force_locked_expanded or ext.is_locked_expanded():
        if ext.is_section_label_shown():
            _draw_section_header(layout, ext, suffix=":")
        body_fn(layout, context)
        return

    domain_id = ext.get_id()
    panel_id = f"superskin_{domain_id}_section"
    header, body = layout.panel(panel_id, default_closed=not ext.is_expanded_by_default())
    if ext.is_section_label_shown():
        _draw_section_header(header, ext, right_align=True)
    if body is not None:
        body_fn(body, context)


def _draw_section_header(container, ext, *, suffix="", right_align=False):
    """Draw ``ext.get_section_title()`` (plus optional *suffix*) and the link/info button."""
    if right_align:
        container.label(text=f"{ext.get_section_title()}{suffix}")
        _draw_link_button(container, ext)
        return

    if not (_LINK_BUTTONS_ENABLED and ext.get_link()):
        title_row = container.row(align=True)
        title_row.alignment = 'LEFT'
        title_row.label(text=f"{ext.get_section_title()}{suffix}")
        return

    split = container.split(factor=0.85)
    title_zone = split.row(align=True)
    title_zone.alignment = 'LEFT'
    title_zone.label(text=f"{ext.get_section_title()}{suffix}")
    link_zone = split.row(align=True)
    link_zone.alignment = 'RIGHT'
    _draw_link_button(link_zone, ext)


def _draw_link_button(layout, ext):
    """Draw an icon button opening ``ext.get_link()`` if set, using the custom help icon
    (``assets/icon_help.png``, loaded by ``interface.utils.icons``) in place of a built-in icon."""
    if not _LINK_BUTTONS_ENABLED:
        return
    link = ext.get_link()
    if not link:
        return
    icon_id = _icons.get_help_icon_id()
    op_layout = layout.operator(
        "wm.url_open", text="",
        **({"icon_value": icon_id} if icon_id else {"icon": 'INFO'}),
    )
    op_layout.url = link


# =========================================================================
#  Settings / How-to-use toggle row -- relocatable entry point
# =========================================================================

def draw_settings_toggle_row(layout, context, activated: bool):
    """A row holding the "Settings" popover trigger."""
    toggle_row = layout.row(align=True)
    toggle_row.alignment = 'RIGHT'
    toggle_row.scale_y = 1.4
    toggle_row.scale_x = 1.3
    settings_toggle = toggle_row.row(align=True)
    settings_toggle.enabled = activated
    settings_btn = settings_toggle.operator(
        "wm.call_panel", text="",
        icon='PREFERENCES' if activated else 'LOCKED',
    )
    settings_btn.name = "SUPERSKIN_PT_settings_popup"
    settings_btn.keep_open = True


def _draw_how_to_use_body(layout, context):
    """Quick Start Guide (from the ``builtin_tutorial`` domain), tutorials, docs and Discord
    buttons, drawn at the top of the Settings popup."""
    from .registry.register_api import UnifiedRegistry

    builtin_tutorial_ext = UnifiedRegistry.get_by_id("builtin_tutorial")
    if builtin_tutorial_ext is not None:
        builtin_tutorial_ext.draw_section(layout, context)

    tutorials_row = layout.row()
    tutorials_row.enabled = _TUTORIALS_URL is not None
    tutorials_row.operator(
        "wm.url_open",
        text="Quick Start Tutorials" if _TUTORIALS_URL else "Quick Start Tutorials (Coming Soon)",
        icon='PLAY',
    ).url = _TUTORIALS_URL or ""
    layout.operator("wm.url_open", text="Read Documentation", icon='URL').url = _DOCS_URL
    layout.operator("wm.url_open", text="Join Discord Server", icon='URL').url = _DISCORD_URL


# =========================================================================
#  Preference section entry point
# =========================================================================

def draw_preferences_body(layout, context):
    """Draw the How to use buttons, then the System/Customize section, in the Settings popup."""
    prefs = context.window_manager.superskin_prefs
    _draw_how_to_use_body(layout, context)
    layout.separator(factor=0.4)
    _draw_preferences(layout, context, prefs)


# =========================================================================
#  Preference body — visual customization + feature extensions + system/debug
# =========================================================================

def _draw_preferences(layout, context, prefs):
    """Preference panel body: feature extensions, system actions."""
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    for ext in UnifiedRegistry.get_by_tab('PREFERENCE'):
        if ext.get_id() in ("addon_updater", "activate", "support_report", "builtin_tutorial"):
            continue
        layout.separator(factor=0.2)
        draw_collapsible_box_ext(layout, context, ext, 'PREFERENCE', force_locked_expanded=True)

    layout.separator(factor=0.4)
    layout.label(text="System Actions:")
    shortcuts_btn = layout.operator(
        "wm.call_panel", text="Edit Shortcuts", icon='PREFERENCES',
    )
    shortcuts_btn.name = "SUPERSKIN_PT_shortcuts_editor"
    shortcuts_btn.keep_open = True
    support_report_ext = UnifiedRegistry.get_by_id("support_report")
    if support_report_ext is not None:
        support_report_ext.draw_section(layout, context)
    layout.operator("superskin.reset_license_activation", text="Reset All Activate", icon='TRASH')
    if any(ext.supports_developer_override() for ext in UnifiedRegistry.get_all()):
        layout.operator("superskin.override_dev_defaults", text="Save As Default", icon='FILE_TICK')


# =========================================================================
#  Dev Debugger panel body — DEV_DEBUG-tab extensions (debug_console, profiler)
# =========================================================================

def draw_dev_debugger_body(layout, context):
    """Draw every ``draw_tab = "DEV_DEBUG"`` extension's section, in order."""
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    extensions = UnifiedRegistry.get_by_tab('DEV_DEBUG')
    for i, ext in enumerate(extensions):
        if i > 0:
            layout.separator(factor=0.2)
        draw_collapsible_box_ext(layout, context, ext, 'DEV_DEBUG')
