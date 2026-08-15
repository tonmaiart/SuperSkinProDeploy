"""In-Mesh Transfer — draws its UI section."""

from . import logic


def draw_section(layout, context):
    obj = context.active_object
    mesh_name = obj.data.name if obj and obj.type == 'MESH' else None
    marked_count = logic.marked_source_count_for_mesh(mesh_name)

    row = layout.row(align=True)
    row.scale_y = 1.2
    mark_text = f"Mark Source ({marked_count})" if marked_count else "Mark Source"
    mark_icon = 'CHECKMARK' if marked_count else 'INFO'
    row.operator("mesh.ssp_inmesh_mark_source", text=mark_text, icon=mark_icon)

    sub = row.row(align=True)
    sub.scale_y = 1.2
    sub.enabled = marked_count > 0
    sub.operator("mesh.ssp_inmesh_transfer", text="Transfer")
