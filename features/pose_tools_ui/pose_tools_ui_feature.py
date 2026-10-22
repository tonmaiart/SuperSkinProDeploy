
from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_VIEWER_DOMAIN_ID = "deform_layer_viewer"
_VIEWER_TAB_KEY = "SKINNING"



class PoseToolsUIFeature(UnifiedFeatureExtension):
    """Structural extension owning the POSE-tab ("Pose mode UI") section dispatch."""


    domain_id = "pose_tools_ui"
    actions = []
    section_title = "Pose Tools"
    draw_tab = ""


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        return {"status": "CANCELLED"}


    def draw_section(self, layout, context) -> None:
        viewer_ext = UnifiedRegistry.get_by_id(_VIEWER_DOMAIN_ID)
        if viewer_ext is None:
            return
        layout.separator(factor=0.2)
        UnifiedRegistry.draw_collapsible_section(layout, context, viewer_ext, _VIEWER_TAB_KEY)


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



def register():
    UnifiedRegistry.register(PoseToolsUIFeature())


def unregister():
    UnifiedRegistry.unregister("pose_tools_ui")
