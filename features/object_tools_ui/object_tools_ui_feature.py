
from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui_weight_transfer

_TAB_KEY = "LAYER"

_MERGED_ROW_DOMAIN_IDS = frozenset({"weight_transfer", "weight_export", "weight_import"})
_ROW_ANCHOR_DOMAIN_ID = "weight_export"



class ObjectToolsUIFeature(UnifiedFeatureExtension):
    """Structural extension owning the LAYER-tab ("Object mode UI") section dispatch loop."""


    domain_id = "object_tools_ui"
    actions = []
    section_title = "Object Tools"
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
            if domain_id in _MERGED_ROW_DOMAIN_IDS:
                if domain_id != _ROW_ANCHOR_DOMAIN_ID:
                    continue
                layout.separator(factor=0.2)
                ui_weight_transfer.draw_import_export_section(layout, context)
                continue
            layout.separator(factor=0.2)
            UnifiedRegistry.draw_collapsible_section(layout, context, ext, _TAB_KEY)


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



def register():
    UnifiedRegistry.register(ObjectToolsUIFeature())


def unregister():
    UnifiedRegistry.unregister("object_tools_ui")
