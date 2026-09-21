"""Vertex select operators for the Weight Select tool: click, all/none/invert, linked, hover."""

import heapq

import bpy
import numpy as np

from . import pick, select_draw

_keymaps = []
_path_anchor = {}


def _poll_weight_paint_mesh(context):
    obj = context.active_object
    return (obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
            and context.region is not None and context.region_data is not None)


def _read_selection(mesh):
    selected = np.empty(len(mesh.vertices), dtype=np.bool_)
    mesh.vertices.foreach_get("select", selected)
    return selected


def _write_selection(mesh, selected):
    mesh.vertices.foreach_set("select", selected)
    mesh.update()


def _connected_vertices(mesh, seeds):
    """Every vertex reachable from the `seeds` indices through mesh edges."""
    count = len(mesh.vertices)
    edge_count = len(mesh.edges)
    verts = np.empty(edge_count * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", verts)
    verts = verts.reshape(edge_count, 2)
    src = np.concatenate([verts[:, 0], verts[:, 1]])
    dst = np.concatenate([verts[:, 1], verts[:, 0]])
    order = np.argsort(src, kind='stable')
    dst = dst[order]
    starts = np.concatenate([[0], np.cumsum(np.bincount(src, minlength=count))])

    visited = np.zeros(count, dtype=np.bool_)
    visited[seeds] = True
    frontier = [int(v) for v in np.atleast_1d(seeds)]
    while frontier:
        nxt = []
        for v in frontier:
            for n in dst[starts[v]:starts[v + 1]]:
                if not visited[n]:
                    visited[n] = True
                    nxt.append(int(n))
        frontier = nxt
    return visited


def _shortest_path(mesh, source, target):
    """Vertex indices along the shortest edge-length path, or None when unconnected."""
    edge_count = len(mesh.edges)
    verts = np.empty(edge_count * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", verts)
    verts = verts.reshape(edge_count, 2)
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    lengths = np.linalg.norm(co[verts[:, 0]] - co[verts[:, 1]], axis=1)
    adjacency = {}
    for (a, b), w in zip(verts.tolist(), lengths.tolist()):
        adjacency.setdefault(a, []).append((b, w))
        adjacency.setdefault(b, []).append((a, w))

    dist = {source: 0.0}
    prev = {}
    heap = [(0.0, source)]
    while heap:
        d, v = heapq.heappop(heap)
        if v == target:
            break
        if d > dist.get(v, float("inf")):
            continue
        for n, w in adjacency.get(v, ()):
            nd = d + w
            if nd < dist.get(n, float("inf")):
                dist[n] = nd
                prev[n] = v
                heapq.heappush(heap, (nd, n))
    if target not in dist:
        return None
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    return path


class SUPERSKIN_OT_weight_select_click(bpy.types.Operator):
    """Select the vertex under the cursor (Ctrl: add the shortest path from the last clicked vertex)"""
    bl_idname = "superskin.weight_select_click"
    bl_label = "Select Vertex"
    bl_options = {'REGISTER', 'UNDO'}

    toggle: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll_weight_paint_mesh(context)

    def invoke(self, context, event):
        self.toggle = event.shift
        mesh = context.active_object.data
        hit = pick.vertex_under_cursor(context, context.active_object, (event.mouse_region_x, event.mouse_region_y))
        selected = _read_selection(mesh)
        key = mesh.name
        anchor = _path_anchor.get(key)
        if hit is None:
            if self.toggle or event.ctrl:
                return {'CANCELLED'}
            selected[:] = False
            _path_anchor.pop(key, None)
        elif event.ctrl and anchor is not None and anchor < len(selected) and selected[anchor]:
            path = _shortest_path(mesh, anchor, hit)
            if path is None:
                self.report({'INFO'}, "No path between the vertices")
                return {'CANCELLED'}
            selected[path] = True
            _path_anchor[key] = hit
        elif self.toggle:
            selected[hit] = not selected[hit]
            _path_anchor[key] = hit
        else:
            if not event.ctrl:
                selected[:] = False
            selected[hit] = True
            _path_anchor[key] = hit
        _write_selection(mesh, selected)
        return {'FINISHED'}


class _SelectAllBase(bpy.types.Operator):
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'

    def _next(self, selected):
        raise NotImplementedError

    def execute(self, context):
        mesh = context.active_object.data
        _write_selection(mesh, self._next(_read_selection(mesh)))
        return {'FINISHED'}


class SUPERSKIN_OT_weight_select_all(_SelectAllBase):
    """Select all vertices"""
    bl_idname = "superskin.weight_select_all"
    bl_label = "Select All Vertices"

    def _next(self, selected):
        return np.ones_like(selected)


class SUPERSKIN_OT_weight_select_none(_SelectAllBase):
    """Deselect all vertices"""
    bl_idname = "superskin.weight_select_none"
    bl_label = "Deselect All Vertices"

    def _next(self, selected):
        return np.zeros_like(selected)


class SUPERSKIN_OT_weight_select_invert(_SelectAllBase):
    """Invert the vertex selection"""
    bl_idname = "superskin.weight_select_invert"
    bl_label = "Invert Vertex Selection"

    def _next(self, selected):
        return ~selected


class SUPERSKIN_OT_weight_select_linked(bpy.types.Operator):
    """Select every vertex connected to the one under the cursor"""
    bl_idname = "superskin.weight_select_linked"
    bl_label = "Select Linked"
    bl_options = {'REGISTER', 'UNDO'}

    extend: bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _poll_weight_paint_mesh(context)

    def invoke(self, context, event):
        self.extend = event.shift
        obj = context.active_object
        seed = pick.vertex_under_cursor(context, obj, (event.mouse_region_x, event.mouse_region_y))
        if seed is None:
            return {'CANCELLED'}
        mesh = obj.data
        linked = _connected_vertices(mesh, seed)
        _write_selection(mesh, (_read_selection(mesh) | linked) if self.extend else linked)
        return {'FINISHED'}


class SUPERSKIN_OT_weight_select_linked_selected(bpy.types.Operator):
    """Extend the selection to every vertex connected to it"""
    bl_idname = "superskin.weight_select_linked_selected"
    bl_label = "Select Linked (Selection)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'

    def execute(self, context):
        mesh = context.active_object.data
        selected = _read_selection(mesh)
        seeds = np.nonzero(selected)[0]
        if len(seeds) == 0:
            return {'CANCELLED'}
        _write_selection(mesh, _connected_vertices(mesh, seeds))
        return {'FINISHED'}


def _set_hidden(mesh, hidden):
    """Writes the vertex hide flag and mirrors it onto edges and faces touching a hidden vertex."""
    mesh.vertices.foreach_set("hide", hidden)
    edge_verts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_verts)
    mesh.edges.foreach_set("hide", hidden[edge_verts.reshape(-1, 2)].any(axis=1))
    loop_verts = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)
    starts = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    if len(starts):
        mesh.polygons.foreach_set("hide", np.logical_or.reduceat(hidden[loop_verts], starts))
    mesh.update()


class SUPERSKIN_OT_weight_hide_selected(_SelectAllBase):
    """Hide the selected vertices"""
    bl_idname = "superskin.weight_hide_selected"
    bl_label = "Hide Selected Vertices"

    def execute(self, context):
        mesh = context.active_object.data
        selected = _read_selection(mesh)
        if not selected.any():
            return {'CANCELLED'}
        hidden = np.empty(len(mesh.vertices), dtype=np.bool_)
        mesh.vertices.foreach_get("hide", hidden)
        mesh.vertices.foreach_set("select", np.zeros_like(selected))
        _set_hidden(mesh, hidden | selected)
        return {'FINISHED'}


class SUPERSKIN_OT_weight_reveal(_SelectAllBase):
    """Reveal all hidden vertices and select them"""
    bl_idname = "superskin.weight_reveal"
    bl_label = "Reveal Hidden Vertices"

    def execute(self, context):
        mesh = context.active_object.data
        hidden = np.empty(len(mesh.vertices), dtype=np.bool_)
        mesh.vertices.foreach_get("hide", hidden)
        if not hidden.any():
            return {'CANCELLED'}
        _set_hidden(mesh, np.zeros_like(hidden))
        mesh.vertices.foreach_set("select", hidden)
        mesh.update()
        return {'FINISHED'}


class SUPERSKIN_OT_weight_select_hover(bpy.types.Operator):
    """Track the vertex under the cursor so the overlay can highlight it"""
    bl_idname = "superskin.weight_select_hover"
    bl_label = "Vertex Hover"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        try:
            self._update(context, event)
        except Exception:
            select_draw.set_hover(None)
        return {'PASS_THROUGH'}

    @staticmethod
    def _update(context, event):
        obj = context.active_object
        region = context.region
        active = (
            obj is not None and obj.type == 'MESH' and obj.mode == 'WEIGHT_PAINT'
            and region is not None and region.type == 'WINDOW' and context.region_data is not None
            and pick.is_select_tool_active(context)
            and 0 <= event.mouse_region_x <= region.width and 0 <= event.mouse_region_y <= region.height
        )
        if not active:
            select_draw.set_hover(None, context)
            return
        select_draw.set_hover(pick.vertex_under_cursor(context, obj, (event.mouse_region_x, event.mouse_region_y)), context)


_classes = (
    SUPERSKIN_OT_weight_select_click,
    SUPERSKIN_OT_weight_select_all,
    SUPERSKIN_OT_weight_select_none,
    SUPERSKIN_OT_weight_select_invert,
    SUPERSKIN_OT_weight_select_linked,
    SUPERSKIN_OT_weight_select_linked_selected,
    SUPERSKIN_OT_weight_hide_selected,
    SUPERSKIN_OT_weight_reveal,
    SUPERSKIN_OT_weight_select_hover,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name='Weight Paint', space_type='EMPTY')
        _keymaps.append((km, km.keymap_items.new(
            "superskin.weight_select_hover", type='MOUSEMOVE', value='ANY', any=True)))


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    select_draw.set_hover(None)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
