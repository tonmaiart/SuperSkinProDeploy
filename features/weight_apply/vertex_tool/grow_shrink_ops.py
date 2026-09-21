"""Grow/Shrink Selection operator: one hop-count step per wheel notch."""

import bpy


# ==============================================================================
# Grow/Shrink Selection
# ==============================================================================

def _adjacency(mesh):
    adj = [[] for _ in range(len(mesh.vertices))]
    for e in mesh.edges:
        a, b = e.vertices
        adj[a].append(b)
        adj[b].append(a)
    return adj


def _selected_islands(selected, adj):
    """Group selected vertex indices into topologically-disjoint islands,
    connected only through edges whose both endpoints are selected."""
    visited = set()
    islands = []
    for start in sorted(selected):
        if start in visited:
            continue
        stack = [start]
        visited.add(start)
        island = set()
        while stack:
            v = stack.pop()
            island.add(v)
            for other in adj[v]:
                if other in selected and other not in visited:
                    visited.add(other)
                    stack.append(other)
        islands.append(island)
    return islands


def _apply_selection(mesh, selected):
    mesh.vertices.foreach_set("select", [i in selected for i in range(len(mesh.vertices))])
    mesh.update()


class SUPERSKIN_OT_grow_shrink_selection(bpy.types.Operator):
    """One Grow or Shrink step per scroll-wheel notch over the mesh's vertex selection,
    computed on plain mesh data so it works in Weight Paint Mode."""
    bl_idname = "superskin.grow_shrink_selection"
    bl_label = "Grow/Shrink Selection"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.IntProperty(
        name="Direction", default=1,
        description="+1 grows the selection, -1 shrinks it",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and context.mode == 'PAINT_WEIGHT'

    def execute(self, context):
        mesh = context.active_object.data
        selected = {v.index for v in mesh.vertices if v.select}
        if not selected:
            return {'CANCELLED'}
        adj = _adjacency(mesh)

        if self.direction > 0:
            grown = set(selected)
            for v in selected:
                grown.update(adj[v])
            _apply_selection(mesh, grown)
            return {'FINISHED'}

        boundary = {v for v in selected if any(n not in selected for n in adj[v])}
        if boundary != selected:
            _apply_selection(mesh, selected - boundary)
            return {'FINISHED'}

        islands = _selected_islands(selected, adj)
        if len(islands) <= 1:
            return {'CANCELLED'}
        peelable = [i for i in islands if len(i) > 1]
        if not peelable:
            return {'CANCELLED'}
        _apply_selection(mesh, selected - peelable[0])
        return {'FINISHED'}


class SUPERSKIN_OT_grow_selection(SUPERSKIN_OT_grow_shrink_selection):
    """Grow the vertex selection by one step"""
    bl_idname = "superskin.grow_selection"
    bl_label = "Grow Selection"

    def execute(self, context):
        self.direction = 1
        return super().execute(context)


class SUPERSKIN_OT_shrink_selection(SUPERSKIN_OT_grow_shrink_selection):
    """Shrink the vertex selection by one step"""
    bl_idname = "superskin.shrink_selection"
    bl_label = "Shrink Selection"

    def execute(self, context):
        self.direction = -1
        return super().execute(context)


_classes = (SUPERSKIN_OT_grow_shrink_selection, SUPERSKIN_OT_grow_selection, SUPERSKIN_OT_shrink_selection)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
