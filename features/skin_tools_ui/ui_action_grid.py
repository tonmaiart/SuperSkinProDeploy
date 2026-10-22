

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

    grid.operator("superskin.open_mirror_options", text="Mirror...")

    grid.operator("superskin.open_toolkit_popover", text="Copy Vertex...").panel = "SUPERSKIN_PT_copy_vertex_options"

    grid.operator("superskin.open_copy_weight_options", text="Copy Weight...")

    grid.operator("mesh.ssp_inmesh_hammer", text="Hammer")

    grid.operator("superskin.open_toolkit_popover", text="Self Transfer...").panel = "SUPERSKIN_PT_self_transfer_options"

    grid.operator("superskin.open_toolkit_popover", text="Limit Total...").panel = "SUPERSKIN_PT_limit_total_options"

    grid.operator("superskin.open_toolkit_popover", text="Selection...").panel = "SUPERSKIN_PT_select_vertices_options"
