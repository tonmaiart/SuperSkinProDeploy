
import bpy

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DOCS_URL = "https://tonmaiart.github.io/superskinpro-docs/installation/"



class ActivateFeature(UnifiedFeatureExtension):
    """Unified extension for the consolidated Activate/Status UI."""


    domain_id = "activate"
    actions = []
    section_title = "Activate"
    draw_tab = "PREFERENCE"


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}


    def draw_activate_prompt(self, layout, context) -> None:
        if CoreFacade.is_system_activated():
            return
        prefs = context.window_manager.superskin_prefs
        lic = prefs.license

        box = layout.box()
        box.label(text="Please Activate License to continue!")

        key_row = box.row(align=True)
        key_row.prop(lic, "license_key", text="")
        activate_sub = key_row.row(align=True)
        activate_sub.scale_x = 0.7
        key_row.scale_y=1.4

        activate_button_row = box.row(align=True)
        activate_button_row.scale_y = 1.4
        activate_button_row.operator("superskin.activate_license", text="Activate Key")

        help_row = box.row(align=True)
        help_row.scale_y=1.2
        help_row.operator("wm.url_open", text="What is an activate key", icon='QUESTION').url = _DOCS_URL

    def draw_section(self, layout, context) -> None:
        self.draw_activate_prompt(layout, context)


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



def register():
    UnifiedRegistry.register(ActivateFeature())


def unregister():
    UnifiedRegistry.unregister("activate")
