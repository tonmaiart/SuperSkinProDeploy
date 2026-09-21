"""Combined action grid -- SKINNING-tab widget code."""

from ..in_mesh_transfer.public_api import marked_source_count_for_mesh
from .ui_mirror import SUPERSKIN_PT_mirror_options


def draw_section(layout, context) -> None:
    obj = context.active_object
    mesh_name = obj.data.name if obj and obj.type == 'MESH' else None
    marked_count = marked_source_count_for_mesh(mesh_name)

    grid = layout.grid_flow(
        row_major=True, columns=2, even_columns=True, even_rows=True, align=True,
    )
    grid.scale_y = 1.2

    block_cell = grid.row(align=True)
    block_cell.enabled = not getattr(context.scene, "superskin_is_mask_mode", False)
    block_cell.operator(
        "mesh.auto_assign_closest_unlocked_bone", text="Block Weight",
    )

    grid.operator("wm.call_panel", text="Mirror...").name = SUPERSKIN_PT_mirror_options.bl_idname

    grid.operator("object.ssp_copy_weight_single", text="Copy Vertex")
    grid.operator("object.ssp_paste_weight_replace", text="Paste Vertex")

    grid.operator(
        "mesh.ssp_inmesh_mark_source", text="Mark Source",
        depress=marked_count > 0,
    )

    transfer_cell = grid.row(align=True)
    transfer_cell.enabled = marked_count > 0
    transfer_cell.operator("mesh.ssp_inmesh_transfer", text="Transfer")

    grid.operator("mesh.ssp_inmesh_hammer", text="Hammer")
