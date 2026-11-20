
from .ui import draw_layer_list, click_layer_row
from .object_selector import get_effective_mesh, get_effective_armature
from .object_selector import _mesh_state_icon_kwargs as mesh_state_icon_kwargs
from .fill_logic import fill_transparent
import bpy

from ..deform_bone_viewer.ui import click_bone_row

__all__ = [
    "draw_layer_list", "get_effective_mesh", "get_effective_armature", "fill_transparent",
    "mesh_state_icon_kwargs", "select_influence", "select_layer_channel",
]


def select_influence(context, obj, layer_index, bone_name=None) -> bool:
    if "ss_layers_meta" not in obj.data:
        return False
    from ....core.facade import CoreFacade
    if CoreFacade(context).get_active_layer_index() != layer_index:
        click_layer_row(context, obj, layer_index)
    if bone_name is None:
        return select_layer_channel(context, obj, layer_index, True)
    group = obj.vertex_groups.get(bone_name)
    if group is None:
        return False
    click_bone_row(context, obj, group.index)
    return True


def select_layer_channel(context, obj, layer_index, is_mask) -> bool:
    if "ss_layers_meta" not in obj.data:
        return False
    from ....core.facade import CoreFacade
    if CoreFacade(context).get_active_layer_index() != layer_index:
        click_layer_row(context, obj, layer_index)
    if bool(obj.superskin_storage.active_is_mask) != is_mask:
        bpy.ops.superskin.toggle_mask_mode(target='MASK' if is_mask else 'WEIGHT')
    return bool(obj.superskin_storage.active_is_mask) == is_mask
