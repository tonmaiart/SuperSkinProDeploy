
import bpy

from .utils import keymap_editor

_PANEL_IDNAME = "SUPERSKIN_PT_settings"
_TAB_SPLIT = 0.2
_TAB_SCALE_Y = 1.4

SETTINGS_TABS = (
    ('HOME', "Home", "Welcome, help links and the add-on version", 'NONE', 0),
    ('SHORTCUTS', "Shortcuts", "Change, hide and export the add-on's shortcuts", 'NONE', 1),
    ('INTERFACE', "Interface", "Viewport display options", 'NONE', 2),
    ('THEMES', "Themes", "Colors of the brush cursor, weights, wireframe and bone HUD", 'NONE', 3),
)


class SUPERSKIN_PT_settings(bpy.types.Panel):
    bl_idname = _PANEL_IDNAME
    bl_label = "Super Skin Pro Settings"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_ui_units_x = 36

    def draw(self, context):
        from . import ops_shortcut_editor, widget_preferences

        wm = context.window_manager
        tab = wm.superskin_settings_tab
        bar = self.layout.row()
        bar.alignment = 'RIGHT'
        draw_close_button(bar)
        split = self.layout.split(factor=_TAB_SPLIT)
        left = split.column()
        tabs = left.column(align=True)
        tabs.scale_y = _TAB_SCALE_Y
        for key, name, _description, _icon, _number in SETTINGS_TABS:
            tabs.prop_enum(wm, "superskin_settings_tab", key, text=name)

        body = split.box()
        if tab == 'SHORTCUTS':
            ops_shortcut_editor.draw_shortcut_editor(body, context)
        elif tab == 'INTERFACE':
            widget_preferences.draw_interface_body(body, context)
        elif tab == 'THEMES':
            widget_preferences.draw_themes_body(body, context)
        else:
            widget_preferences.draw_home_body(body, context)


def draw_close_button(layout):
    sub = layout.row()
    sub.ui_units_x = _CLOSE_UNITS
    sub.template_popup_confirm("", text="", cancel_text="✕")


_POPUP_PAD_X = 1.0
_POPUP_PAD_Y = 0.7
_TAB_TOP = 0.7
_ANCHOR_FROM_LEFT = 6.1
_HOME_WALLPAPER = 9.75
_HOME_LINE = 1.0
_PANEL_HEADER = 2.3
_PANEL_PAD = 0.25
_SHORTCUT_HEADER = 1.4
_BAR_UNITS = 1.2
_CLOSE_UNITS = 1.0


def _tabs_units():
    return len(SETTINGS_TABS) * _TAB_SCALE_Y


def _home_units():
    from .widget_preferences import home_line_count
    return max(_HOME_WALLPAPER, home_line_count() * _HOME_LINE)


def _panels_units(panels, header):
    return header + sum(_PANEL_HEADER + (rows + _PANEL_PAD if is_open else 0.0) for rows, is_open in panels)


def _content_units(context, tab):
    if tab == 'HOME':
        return _home_units()
    if tab == 'INTERFACE':
        from .widget_preferences import interface_section_rows
        return _panels_units(interface_section_rows(context), 0.0)
    if tab == 'THEMES':
        from .widget_preferences import theme_section_rows
        return _panels_units(theme_section_rows(context), _SHORTCUT_HEADER)
    groups = keymap_editor.grouped_rows(keymap_editor.shortcut_rows(), context.window_manager.superskin_shortcut_tab)
    return _panels_units([(len(group_rows), True) for _group, group_rows in groups], _SHORTCUT_HEADER)


def _center_cursor(context, tab):
    window = context.window
    if window is None:
        return
    unit = 20.0 * (context.preferences.system.ui_scale or 1.0)
    width = (SUPERSKIN_PT_settings.bl_ui_units_x + _POPUP_PAD_X) * unit
    tabs_height = _tabs_units()
    height = (_BAR_UNITS + max(_content_units(context, tab), tabs_height) + _POPUP_PAD_Y) * unit
    index = next(i for i, item in enumerate(SETTINGS_TABS) if item[0] == tab)
    left = (window.width - width) / 2
    top = (window.height - height) / 2
    x = left + _ANCHOR_FROM_LEFT * unit
    y_from_top = top + (_TAB_TOP + _BAR_UNITS + (index + 0.5) * _TAB_SCALE_Y) * unit
    window.cursor_warp(int(x), int(window.height - y_from_top))


class SUPERSKIN_OT_open_settings(bpy.types.Operator):
    """Open the Super Skin Pro settings: help, shortcuts and interface options"""
    bl_idname = "superskin.open_settings"
    bl_label = "Super Skin Pro Settings"
    bl_options = {'INTERNAL'}

    tab: bpy.props.EnumProperty(items=[item[:3] for item in SETTINGS_TABS], default='HOME',
                                options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return self._open(context, (event.mouse_x, event.mouse_y))

    def execute(self, context):
        return self._open(context, None)

    def _open(self, context, cursor):
        from .utils.icons import ensure_splash_image

        ensure_splash_image()
        context.window_manager.keyconfigs.update()
        context.window_manager.superskin_settings_tab = self.tab
        _center_cursor(context, self.tab)
        result = bpy.ops.wm.call_panel(name=_PANEL_IDNAME, keep_open=True)
        if cursor is not None and context.window is not None:
            context.window.cursor_warp(*cursor)
        return result


_classes = (
    SUPERSKIN_PT_settings,
    SUPERSKIN_OT_open_settings,
)


def register():
    bpy.types.WindowManager.superskin_settings_tab = bpy.props.EnumProperty(
        name="", items=SETTINGS_TABS, default='HOME', options={'SKIP_SAVE'},
    )
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.superskin_settings_tab
