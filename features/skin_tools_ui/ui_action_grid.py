"""Combined action grid -- SKINNING-tab widget code.

Merges the action buttons that used to be drawn as four separate sections
(``auto_block``, ``mirror``, ``clipboard``, ``in_mesh_transfer``) into a
single 2-column grid (3 rows), per explicit user request (2026-09-14): Auto
Block Weight, Mirror Weight, Copy, Paste, Mark Source, Transfer, in that
order.
Each domain keeps its own ``execute()``/CoreFacade logic and PropertyGroups
in its own package -- only this drawing code lives here, alongside the
other migrated ``ui_<name>.py`` modules. See
``docs/domains/skin_tools_ui.md``.

``ui_auto_block.py``, ``ui_clipboard.py`` and ``ui_in_mesh_transfer.py``
(the previous per-domain widget modules for three of these four domains)
are deleted -- their content is folded into this single grid. ``ui_mirror.py``
is kept: it still owns registering ``SUPERSKIN_UL_mirror_sr`` /
``SUPERSKIN_PT_mirror_options`` (the gear-icon options popover reused
below), it just no longer has its own ``draw_section()``.
"""

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

    grid.operator(
        "mesh.auto_assign_closest_unlocked_bone", text="Auto Block Weight",
    )

    mirror_cell = grid.row(align=True)
    mirror_cell.operator("object.mirror_weights", text="Mirror Weight")
    mirror_cell.popover(
        SUPERSKIN_PT_mirror_options.bl_idname, text="", icon='PREFERENCES',
    )

    grid.operator("object.ssp_copy_weight_single", text="Copy", icon='COPYDOWN')
    grid.operator("object.ssp_paste_weight_replace", text="Paste", icon='PASTEDOWN')

    grid.operator(
        "mesh.ssp_inmesh_mark_source", text="Mark Source",
        depress=marked_count > 0,
    )

    transfer_cell = grid.row(align=True)
    transfer_cell.enabled = marked_count > 0
    transfer_cell.operator("mesh.ssp_inmesh_transfer", text="Transfer")
