"""SuperSkinPro Dev Debugger — a standalone second sidebar panel.

Docked in the same N-panel tab as the main "Super Skin Pro" panel
(``VIEW3D_PT_superskin_main``, see ``panel_main.py``), stacked directly
below it (``bl_order`` one higher than the main panel's) via the same
``bl_category``. Hosts every ``draw_tab = "DEV_DEBUG"`` extension --
today that's just ``debug_console`` and ``profiler``, both moved out of
the "Settings" popup (see ``widget_preferences.draw_dev_debugger_body()``
for the full history of that move).

Deliberately has **no** ``poll()`` override and is **not** gated on
``CoreFacade.is_system_activated()`` -- unlike the "Settings" button,
which stays disabled/LOCKED until activation. Debug Console and Profiler
both exist specifically to keep working when the rest of the addon can't
(no license, no active mesh -- see each domain's own "Why no dispatch
actions" doc section), so hiding this panel behind the same activation
gate would defeat that purpose. Collapsed by default (``DEFAULT_CLOSED``)
since it's a dev/diagnostic tool, not part of the normal workflow.
"""

import bpy

from .. import ADDON_NAME
from .widget_preferences import draw_dev_debugger_body


class VIEW3D_PT_superskin_dev_debugger(bpy.types.Panel):
    bl_idname = "VIEW3D_PT_superskin_dev_debugger"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    # Same tab as the main panel (see panel_main.py) so this renders as a
    # second, independently-collapsible box stacked below it.
    bl_category = ADDON_NAME
    bl_label = "Dev Debugger"
    bl_options = {'DEFAULT_CLOSED'}
    # One above the main panel's own bl_order (1000000) so this always
    # sorts directly underneath it, regardless of registration order.
    bl_order = 1000001

    def draw(self, context):
        draw_dev_debugger_body(self.layout, context)


def register():
    bpy.utils.register_class(VIEW3D_PT_superskin_dev_debugger)


def unregister():
    bpy.utils.unregister_class(VIEW3D_PT_superskin_dev_debugger)
