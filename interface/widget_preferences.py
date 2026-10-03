
import bpy

from .. import ADDON_VERSION
from .utils import icons as _icons

_LINK_BUTTONS_ENABLED = False
_QUICK_START_BUTTONS_ENABLED = False

_DOCS_URL = "https://tonmaiart.github.io/superskinpro-docs/"
_DISCORD_URL = "https://discord.gg/BCEJZBnTfM"
_TUTORIALS_URL = None
_GUMROAD_URL = "https://tomatactics.gumroad.com/l/superskinpro"



def draw_collapsible_box_ext(layout, context, ext, tab_key, force_locked_expanded=False, draw_body=None):
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



def draw_settings_toggle_row(layout, context, activated: bool):
    toggle_col = layout.column(align=True)
    toggle_col.scale_y = 1.4
    settings_toggle = toggle_col.column(align=True)
    settings_toggle.enabled = activated
    settings_toggle.menu(
        "SUPERSKIN_MT_settings", text="",
        icon='PREFERENCES' if activated else 'LOCKED',
    )


def _draw_how_to_use_body(layout, context):
    from .registry.register_api import UnifiedRegistry

    if _QUICK_START_BUTTONS_ENABLED:
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



def draw_preferences_body(layout, context):
    prefs = context.window_manager.superskin_prefs
    gumroad_row = layout.row()
    gumroad_row.enabled = _GUMROAD_URL is not None
    gumroad_row.operator("wm.url_open", text="Open Gumroad Link", icon='URL').url = _GUMROAD_URL or ""
    layout.separator()
    _draw_how_to_use_body(layout, context)
    layout.separator(factor=0.4)
    _draw_preferences(layout, context, prefs)



def _draw_preferences(layout, context, prefs):
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    for ext in UnifiedRegistry.get_by_tab('PREFERENCE'):
        if ext.get_id() in ("activate", "support_report", "builtin_tutorial"):
            continue
        ext.draw_section_for_tab(layout, context, 'PREFERENCE')

    layout.separator(factor=0.4)
    layout.operator(
        "superskin.open_keymap_prefs", text="Edit Shortcuts", icon='PREFERENCES',
    )
    support_report_ext = UnifiedRegistry.get_by_id("support_report")
    if support_report_ext is not None:
        support_report_ext.draw_section(layout, context)
    layout.operator("superskin.reset_license_activation", text="Remove Activate", icon='TRASH')
    if hasattr(bpy.types.WindowManager, "superskin_show_dev_debugger"):
        layout.prop(context.window_manager, "superskin_show_dev_debugger")
    if any(ext.supports_developer_override() for ext in UnifiedRegistry.get_all()):
        layout.operator("superskin.override_dev_defaults", text="Save As Default", icon='FILE_TICK')

    layout.separator(factor=0.4)
    layout.label(text=f"Super Skin Pro v{ADDON_VERSION}")



def draw_dev_debugger_body(layout, context):
    from .registry.register_api import UnifiedRegistry

    layout.use_property_decorate = False

    extensions = UnifiedRegistry.get_by_tab('DEV_DEBUG')
    for i, ext in enumerate(extensions):
        if i > 0:
            layout.separator(factor=0.2)
        draw_collapsible_box_ext(layout, context, ext, 'DEV_DEBUG')
