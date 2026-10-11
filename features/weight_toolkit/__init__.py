
from importlib import reload

from .mirror import logic as mirror_logic
from .mirror import ops as mirror_ops
from .auto_block_weight import logic as auto_block_logic
from .auto_block_weight import refine_logic as auto_block_refine_logic
from .auto_block_weight import result_cache as auto_block_result_cache
from .auto_block_weight import soften_logic as auto_block_soften_logic
from .auto_block_weight import ops as auto_block_ops
from .in_mesh_transfer import logic as in_mesh_transfer_logic
from .in_mesh_transfer import ops as in_mesh_transfer_ops
from .in_mesh_transfer import ui as in_mesh_transfer_ui
from .hammer import logic as hammer_logic
from .hammer import ops as hammer_ops
from .copy_vertex_weight import logic as copy_vertex_weight_logic
from .copy_vertex_weight import ops as copy_vertex_weight_ops
from .copy_vertex_weight import ui as copy_vertex_weight_ui
from .copy_weight import logic as copy_weight_logic
from .copy_weight import ops as copy_weight_ops
from .copy_weight import ui as copy_weight_ui
from .select_vertices import logic as select_vertices_logic
from .select_vertices import ops as select_vertices_ops
from .select_vertices import ui as select_vertices_ui
from .limit_total import logic as limit_total_logic
from .limit_total import ops as limit_total_ops
from .limit_total import ui as limit_total_ui
from . import ui_tips
from . import public_api
from . import weight_toolkit_feature

for mod in (mirror_logic, auto_block_logic, auto_block_refine_logic, auto_block_result_cache,
            auto_block_soften_logic,
            in_mesh_transfer_logic, hammer_logic,
            copy_vertex_weight_logic, copy_weight_logic, select_vertices_logic, limit_total_logic, ui_tips, public_api,
            mirror_ops, auto_block_ops, in_mesh_transfer_ops, in_mesh_transfer_ui,
            hammer_ops, copy_vertex_weight_ops, copy_vertex_weight_ui,
            copy_weight_ops, copy_weight_ui, select_vertices_ops, select_vertices_ui,
            limit_total_ops, limit_total_ui, weight_toolkit_feature):
    try:
        reload(mod)
    except Exception:
        pass


def register():
    mirror_ops.register()
    auto_block_ops.register()
    in_mesh_transfer_ops.register()
    in_mesh_transfer_ui.register()
    hammer_ops.register()
    copy_vertex_weight_ops.register()
    copy_vertex_weight_ui.register()
    copy_weight_ops.register()
    copy_weight_ui.register()
    select_vertices_ops.register()
    select_vertices_ui.register()
    limit_total_ops.register()
    limit_total_ui.register()
    weight_toolkit_feature.register()


def unregister():
    weight_toolkit_feature.unregister()
    limit_total_ui.unregister()
    limit_total_ops.unregister()
    select_vertices_ui.unregister()
    select_vertices_ops.unregister()
    copy_weight_ui.unregister()
    copy_weight_ops.unregister()
    copy_vertex_weight_ui.unregister()
    copy_vertex_weight_ops.unregister()
    hammer_ops.unregister()
    in_mesh_transfer_ui.unregister()
    in_mesh_transfer_ops.unregister()
    auto_block_ops.unregister()
    mirror_ops.unregister()
