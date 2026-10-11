
from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import (
    ui_weight_apply, ui_mirror, ui_action_grid,
)

_TAB_KEY = "SKINNING"

_DRAW_BODY_BY_DOMAIN = {
    "weight_apply": ui_weight_apply.draw_section,
}

_GRID_DOMAIN_ID = "weight_toolkit"



class SkinToolsUIFeature(UnifiedFeatureExtension):
    """Structural extension owning the SKINNING-tab ("Edit weight mode UI") section dispatch loop."""


    domain_id = "skin_tools_ui"
    actions = []
    section_title = "Skin Tools"
    draw_tab = ""


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}


    def draw_section(self, layout, context) -> None:
        extensions = UnifiedRegistry.get_by_tab(_TAB_KEY)

        for ext in extensions:
            if not ext.is_collapsible():
                ext.draw_section_for_tab(layout, context, _TAB_KEY)
                break

        for ext in extensions:
            if not ext.is_collapsible():
                continue
            domain_id = ext.get_id()
            if domain_id == _GRID_DOMAIN_ID:
                layout.separator(factor=0.2)
                ui_action_grid.draw_section(layout, context)
                continue
            layout.separator(factor=0.2)
            UnifiedRegistry.draw_collapsible_section(
                layout, context, ext, _TAB_KEY,
                draw_body=_DRAW_BODY_BY_DOMAIN.get(domain_id),
            )


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



def register():
    UnifiedRegistry.register(SkinToolsUIFeature())


def unregister():
    UnifiedRegistry.unregister("skin_tools_ui")
