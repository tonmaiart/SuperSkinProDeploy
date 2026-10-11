
from .ui_mirror import SUPERSKIN_PT_mirror_options


def draw_section(layout, context) -> None:
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

    grid.operator("wm.call_panel", text="Copy Vertex...").name = "SUPERSKIN_PT_copy_vertex_options"

    grid.operator("superskin.open_copy_weight_options", text="Copy Weight...")

    grid.operator("mesh.ssp_inmesh_hammer", text="Hammer")

    grid.operator("wm.call_panel", text="Self Transfer...").name = "SUPERSKIN_PT_self_transfer_options"

    grid.operator("wm.call_panel", text="Limit Total...").name = "SUPERSKIN_PT_limit_total_options"

    grid.operator("wm.call_panel", text="Selection...").name = "SUPERSKIN_PT_select_vertices_options"
