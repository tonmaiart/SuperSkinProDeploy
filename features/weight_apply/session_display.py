
import bpy
import gpu
import numpy as np
from bpy.app.handlers import persistent

from ...core.facade import CoreFacade
from ...interface.utils import viewport_overrides
from ...interface.utils.gpu_utils import declare_depth_write

_RAMP_SIZE = 1024
_DEFAULT_WIRE_COLOR = (0.212231, 0.212231, 0.212231, 1.0)
_WIRE_BIAS_PX = 1.5
_NATIVE_SURFACE_OWNER = "weight_apply.native_surface"
_NATIVE_WIRE_OWNER = "weight_apply.native_surface_wire"
_native_hidden_for: set = set()

_DEFAULT_WEIGHT_STOPS = [
    (0.0, (0.0, 0.0, 1.0)), (0.25, (0.0, 1.0, 1.0)), (0.5, (0.0, 1.0, 0.0)),
    (0.75, (1.0, 1.0, 0.0)), (1.0, (1.0, 0.0, 0.0)),
]

_VERT_SRC = """
void main() {
  vc = col;
  gl_Position = mvp * vec4(pos, 1.0);
}
"""
_FRAG_SRC = """
void main() {
  frag = vec4(vc.rgb, 1.0);
}
"""
_FAR_VERT_SRC = """
void main() {
  gl_Position = mvp * vec4(pos, 1.0);
}
"""
_FAR_FRAG_SRC = """
void main() {
  frag = vec4(0.0);
  gl_FragDepth = 1.0;
}
"""
_WIRE_VERT_SRC = """
void main() {
  vec3 ray = eye.xyz - pos * eye.w;
  float len = length(ray) * length(nor);
  float facing = len > 0.0 ? abs(dot(ray, nor)) / len : 1.0;
  gl_Position = mvp * vec4(pos + ray * (lift / max(facing, 0.2)), 1.0);
}
"""
_WIRE_FRAG_SRC = """
void main() {
  frag = color;
}
"""

_shaders = {}
_ramps = {"weight": None, "mask": None}
_wire_color = _DEFAULT_WIRE_COLOR
_bone_colors = None
_state = None
_handle = None
_preview = None
_coo_cache = None
_stroke_value_key = None


def _surface_shader():
    if "surface" not in _shaders:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', 'mvp')
        info.vertex_in(0, 'VEC3', 'pos')
        info.vertex_in(1, 'VEC4', 'col')
        iface = gpu.types.GPUStageInterfaceInfo("ssp_session_surface_iface")
        iface.smooth('VEC4', 'vc')
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', 'frag')
        info.vertex_source(_VERT_SRC)
        info.fragment_source(_FRAG_SRC)
        _shaders["surface"] = gpu.shader.create_from_info(info)
    return _shaders["surface"]


def _far_shader():
    if "far" not in _shaders:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', 'mvp')
        info.vertex_in(0, 'VEC3', 'pos')
        info.fragment_out(0, 'VEC4', 'frag')
        declare_depth_write(info)
        info.vertex_source(_FAR_VERT_SRC)
        info.fragment_source(_FAR_FRAG_SRC)
        _shaders["far"] = gpu.shader.create_from_info(info)
    return _shaders["far"]


def _wire_shader():
    if "wire" not in _shaders:
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', 'mvp')
        info.push_constant('VEC4', 'eye')
        info.push_constant('VEC4', 'color')
        info.push_constant('FLOAT', 'lift')
        info.vertex_in(0, 'VEC3', 'pos')
        info.vertex_in(1, 'VEC3', 'nor')
        info.fragment_out(0, 'VEC4', 'frag')
        info.vertex_source(_WIRE_VERT_SRC)
        info.fragment_source(_WIRE_FRAG_SRC)
        _shaders["wire"] = gpu.shader.create_from_info(info)
    return _shaders["wire"]



def _srgb_encode(c):
    c = np.maximum(c, 0.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1.0 / 2.4) - 0.055)


def _srgb_decode(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lut(stops):
    stops = sorted(stops or _DEFAULT_WEIGHT_STOPS, key=lambda stop: stop[0])
    xs = np.array([p for p, _c in stops], dtype=np.float64)
    cs = np.array([(*c[:3], c[3] if len(c) > 3 else 1.0) for _p, c in stops], dtype=np.float64)
    cs[:, :3] = _srgb_encode(cs[:, :3])
    t = np.linspace(0.0, 1.0, _RAMP_SIZE)
    lut = np.stack([np.interp(t, xs, cs[:, k]) for k in range(4)], axis=1)
    lut[:, :3] = _srgb_decode(lut[:, :3])
    return lut.astype(np.float32)


def set_ramps(weight_stops, mask_stops) -> None:
    _ramps["weight"] = _lut(weight_stops)
    _ramps["mask"] = _lut(mask_stops)
    if _state is not None:
        _state.value_key = None
        _request_refresh()


def set_wire_color(rgba) -> None:
    global _wire_color
    _wire_color = tuple(float(c) for c in rgba[:4])
    if _state is not None:
        CoreFacade.tag_redraw_areas()


def set_bone_colors(obj_name, rgba) -> None:
    global _bone_colors
    _bone_colors = None if rgba is None else (obj_name, np.asarray(rgba, dtype=np.float32)
                                              .reshape(-1, 4).copy())
    if _state is not None:
        _state.value_key = None
    _request_refresh()


def patch_bone_colors(obj_name, verts, rgba) -> None:
    if _bone_colors is None or _bone_colors[0] != obj_name:
        return
    verts = np.asarray(verts, dtype=np.int64)
    _bone_colors[1][verts] = np.asarray(rgba, dtype=np.float32).reshape(-1, 4)
    if _state is not None and _state.mode == 'BONES':
        _state.col[verts] = _bone_colors[1][verts]
        _state.upload()


def _ramp_index(values):
    idx = np.rint(np.clip(values, 0.0, 1.0) * (_RAMP_SIZE - 1)).astype(np.int64)
    np.maximum(idx, 1, out=idx, where=values > CoreFacade.WEIGHT_FLOOR)
    return idx



def _session_object(context=None):
    context = context or bpy.context
    obj = getattr(context, "active_object", None)
    return obj if CoreFacade.is_skinning_session(context) else None


def _shows_mask(obj) -> bool:
    storage = getattr(obj, "superskin_storage", None)
    return bool(storage is not None and storage.active_is_mask)


def displayed_bone_name(obj) -> str:
    if _preview is not None and obj is not None and _preview[0] == obj.name:
        return _preview[1]
    return CoreFacade.active_vg_name_of(obj)


def set_preview_bone(obj, bone_name) -> None:
    global _preview
    preview = (obj.name, bone_name) if obj is not None and bone_name else None
    if preview == _preview:
        return
    _preview = preview
    _refresh_timer()


def _value_key(obj):
    return (obj.get(CoreFacade.DEFORM_GEN_KEY, 0), CoreFacade.temp_layer_index(obj),
            displayed_bone_name(obj), _shows_mask(obj),
            _bone_colors is not None and _bone_colors[0] == obj.name)


def _topology_key(obj):
    from .mesh_flags import polygon_hide
    mesh = obj.data
    hidden = polygon_hide(mesh)
    any_hidden = hidden is not None and bool(hidden.any())
    return (mesh.session_uid, len(mesh.vertices), len(mesh.polygons),
            hash(hidden.tobytes()) if hidden is not None else 0, any_hidden)


def _layer_coo(obj):
    global _coo_cache
    key = (obj.name, obj.data.session_uid, len(obj.data.vertices),
           obj.get(CoreFacade.DEFORM_GEN_KEY, 0), CoreFacade.temp_layer_index(obj))
    if _coo_cache is None or _coo_cache[0] != key:
        vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(CoreFacade(bpy.context).read_active_layer_rows())
        _coo_cache = (key, np.asarray(vert_ids, dtype=np.int64), np.asarray(bone_ids),
                      np.asarray(weights, dtype=np.float32))
    return _coo_cache[1:]


def _weight_column(obj, n):
    bone = displayed_bone_name(obj)
    col = np.zeros(n, dtype=np.float32)
    none = np.zeros(0, dtype=np.int64)
    if not bone:
        return col, none
    _bone_to_id, id_to_bone = CoreFacade(bpy.context).get_unified_mapping()
    bone_id = next((b for b, name in id_to_bone.items() if name == bone), None)
    if bone_id is None:
        return col, none
    vert_ids, bone_ids, weights = _layer_coo(obj)
    mine = bone_ids == bone_id
    verts = vert_ids[mine]
    col[verts] = weights[mine]
    return col, verts


def _mask_column(obj, n):
    facade = CoreFacade(bpy.context)
    idx = facade.get_active_layer_index()
    default = next((float(m.get("mask_default", 0.0)) for m in facade.get_meta_list()
                    if m.get("index") == idx), 0.0)
    if _state is not None:
        _state.mask_default = default
    col = np.full(n, default, dtype=np.float32)
    mask = facade.get_active_mask_dict()
    if mask:
        verts = np.fromiter(mask.keys(), dtype=np.int64, count=len(mask))
        col[verts] = np.fromiter(mask.values(), dtype=np.float64, count=len(mask))
    return col


def _triangles_and_edges(mesh):
    from .brush_tool.brush_logic import mesh_topology
    from .mesh_flags import edge_vertices, polygon_hide
    tris, polys = mesh_topology(mesh).triangles(mesh)
    tris = tris.reshape(-1, 3).astype(np.int32)
    hidden = polygon_hide(mesh)
    edges = edge_vertices(mesh).astype(np.int32)
    if hidden is not None and hidden.any():
        tris = tris[~hidden[polys]]
        shown = np.zeros(len(mesh.vertices), dtype=bool)
        shown[tris.ravel()] = True
        edges = edges[shown[edges[:, 0]] & shown[edges[:, 1]]]
    return tris, edges


def _evaluated_positions(obj, depsgraph):
    from .vertex_tool.common import evaluated_local_coords
    n = len(obj.data.vertices)
    pos = np.asarray(evaluated_local_coords(obj, depsgraph), dtype=np.float32).reshape(-1, 3)
    evaluated = obj.evaluated_get(depsgraph).data
    nor = None
    if len(evaluated.vertices) == n:
        nor = np.empty(n * 3, dtype=np.float32)
        evaluated.vertex_normals.foreach_get("vector", nor)
        nor = nor.reshape(n, 3)
    else:
        nor = np.empty(n * 3, dtype=np.float32)
        obj.data.vertex_normals.foreach_get("vector", nor)
        nor = nor.reshape(n, 3)
    return pos, nor


class _Display:
    def __init__(self, obj):
        self.name = obj.name
        self.n = len(obj.data.vertices)
        self.pos = None
        self.nor = None
        self.val = None
        self.col = None
        self.mode = None
        self.mask_default = 0.0
        self.value_key = None
        self.topology_key = None
        self.positions_stale = True
        self.tri_ibo = None
        self.edge_ibo = None
        self.batch = None
        self.wire = None
        self.dirty = False
        self.nor_vbo = None
        self.nor_source = None
        self._fmt = None

    def ready(self) -> bool:
        return self.batch is not None or self.dirty

    def upload(self):
        self.dirty = True

    def flush(self):
        if not self.dirty:
            return
        self.dirty = False
        if bpy.app.background or self.pos is None or self.col is None or self.tri_ibo is None:
            return
        if self._fmt is None:
            fmt = gpu.types.GPUVertFormat()
            fmt.attr_add(id="pos", comp_type='F32', len=3, fetch_mode='FLOAT')
            fmt.attr_add(id="col", comp_type='F32', len=4, fetch_mode='FLOAT')
            nor_fmt = gpu.types.GPUVertFormat()
            nor_fmt.attr_add(id="nor", comp_type='F32', len=3, fetch_mode='FLOAT')
            self._fmt = (fmt, nor_fmt)
        if self.nor_vbo is None or self.nor_source is not self.nor:
            self.nor_vbo = gpu.types.GPUVertBuf(self._fmt[1], self.n)
            self.nor_vbo.attr_fill("nor", self.nor)
            self.nor_source = self.nor
        vbo = gpu.types.GPUVertBuf(self._fmt[0], self.n)
        vbo.attr_fill("pos", self.pos)
        vbo.attr_fill("col", self.col)
        self.batch = gpu.types.GPUBatch(type='TRIS', buf=vbo, elem=self.tri_ibo)
        self.batch.vertbuf_add(self.nor_vbo)
        self.wire = gpu.types.GPUBatch(type='LINES', buf=vbo, elem=self.edge_ibo)
        self.wire.vertbuf_add(self.nor_vbo)

    def recolor_sparse(self, verts):
        lut = _ramps["weight"]
        if lut is None:
            lut = _ramps["weight"] = _lut(None)
        if self.col is None or self.col.shape != (self.n, 4):
            self.col = np.empty((self.n, 4), dtype=np.float32)
        self.col[:] = lut[0]
        self.col[verts] = lut[_ramp_index(self.val[verts])]

    def recolor(self, verts=None):
        if self.mode == 'BONES':
            if verts is None:
                self.col = _bone_colors[1].copy()
            else:
                self.col[verts] = _bone_colors[1][verts]
            return
        key = "mask" if self.mode == 'MASK' else "weight"
        lut = _ramps[key]
        if lut is None:
            lut = _ramps[key] = _lut(None)
        if verts is None or self.col is None:
            self.col = lut[_ramp_index(self.val)]
        else:
            self.col[verts] = lut[_ramp_index(self.val[verts])]


def _refresh(context=None):
    global _state
    obj = _session_object(context)
    if obj is None:
        _state = None
        return
    if _state is None or _state.name != obj.name or _state.n != len(obj.data.vertices):
        _state = _Display(obj)
    state = _state
    changed = False
    topo = _topology_key(obj)
    if topo != state.topology_key:
        tris, edges = _triangles_and_edges(obj.data)
        if not bpy.app.background:
            state.tri_ibo = gpu.types.GPUIndexBuf(type='TRIS', seq=tris)
            state.edge_ibo = gpu.types.GPUIndexBuf(type='LINES', seq=edges)
        state.topology_key = topo
        _set_native_surface_hidden(obj, "hidden", topo[4])
        changed = True
    stroke = CoreFacade.is_addon_stroke_active(obj.name)
    if state.positions_stale and not (stroke and _skin is not None):
        state.pos, state.nor = _evaluated_positions(obj, bpy.context.evaluated_depsgraph_get())
        state.positions_stale = False
        changed = True
    key = _value_key(obj)
    if key != state.value_key and not stroke:
        if key[4]:
            state.mode = 'BONES'
        else:
            state.mode = 'MASK' if key[3] else 'WEIGHT'
        if state.mode == 'WEIGHT':
            state.val, weighted = _weight_column(obj, state.n)
            state.recolor_sparse(weighted)
        else:
            if state.mode == 'MASK':
                state.val = _mask_column(obj, state.n)
            state.recolor()
        state.value_key = key
        changed = True
    if changed:
        state.upload()
        CoreFacade.tag_redraw_areas()
    if ("session" in _native_hidden_for) != _draws_whole_mesh(obj):
        _set_native_surface_hidden(obj, "session", "session" not in _native_hidden_for)


def _refresh_timer():
    try:
        _refresh()
    except Exception as exc:
        CoreFacade.debug_log("feature_domains", f"session display refresh failed: {exc!r}")
        show_native_surface()
    return None


def _request_refresh():
    if not bpy.app.timers.is_registered(_refresh_timer):
        bpy.app.timers.register(_refresh_timer, first_interval=0.0)


def refresh_now(context=None) -> None:
    _refresh(context)



def _on_live_write(obj, layer_int, id_to_bone, dirty_verts) -> None:
    state = _state
    if state is None or obj is None or obj.name != state.name or state.mode != 'WEIGHT':
        return
    bone = CoreFacade.active_vg_name_of(obj)
    bone_id = next((b for b, name in id_to_bone.items() if name == bone), None)
    verts = np.fromiter(dirty_verts, dtype=np.int64, count=len(dirty_verts))
    verts.sort()
    values = np.zeros(len(verts), dtype=np.float32)
    if bone_id is not None and len(verts):
        coo_of = getattr(layer_int, "coo_of", None)
        if coo_of is not None:
            vert_ids, bone_ids, weights = coo_of(verts.tolist(), ascending=True)
        else:
            vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(
                {v: layer_int.get(v, {}) for v in verts.tolist()})
        mine = np.asarray(bone_ids) == bone_id
        values[np.searchsorted(verts, np.asarray(vert_ids, dtype=np.int64)[mine])] = \
            np.asarray(weights, dtype=np.float64)[mine]
    state.val[verts] = values
    state.recolor(verts)
    state.upload()
    CoreFacade.tag_redraw_areas()


def on_live_mask(obj, mask_dict, dirty_verts) -> None:
    state = _state
    if state is None or obj is None or obj.name != state.name or state.mode != 'MASK':
        return
    if mask_dict is None:
        return
    verts = np.fromiter(dirty_verts, dtype=np.int64, count=len(dirty_verts))
    default = state.mask_default
    state.val[verts] = np.array([mask_dict.get(v, default) for v in verts.tolist()],
                                dtype=np.float32)
    state.recolor(verts)
    state.upload()
    CoreFacade.tag_redraw_areas()



_skin = None
_drawn_gen = 0


def stroke_positions(obj):
    state = _state
    if _skin is None or state is None or state.pos is None or state.name != obj.name:
        return None
    if state.n != len(obj.data.vertices):
        return None
    return state.pos


def drawn_generation() -> int:
    return _drawn_gen


def _drawn_changed() -> None:
    global _drawn_gen
    _drawn_gen += 1


def begin_stroke(context) -> bool:
    global _skin, _stroke_value_key
    _skin = None
    _stroke_value_key = None
    obj = _session_object(context)
    if obj is None:
        return False
    _refresh(context)
    state = _state
    _stroke_value_key = state.value_key if state is not None else None
    if state is None or state.pos is None:
        return False
    from .brush_tool.brush_logic import rest_positions
    from . import stroke_skin
    md = stroke_skin.primary_armature(obj)
    if md is not None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        base = stroke_skin.armature_input(obj, md, rest_positions(obj.data), depsgraph)
        _skin = stroke_skin.StrokeSkin(obj, md, base, state.pos.astype(np.float64))
        _drawn_changed()
    _set_native_surface_hidden(obj, "stroke", True)
    return True


def _draws_whole_mesh(obj) -> bool:
    try:
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    except (AttributeError, ReferenceError, RuntimeError):
        return False
    mesh = obj.data
    return len(evaluated.vertices) == len(mesh.vertices) and len(evaluated.polygons) == len(mesh.polygons)


def _set_native_surface_hidden(obj, reason: str, hidden: bool) -> None:
    if hidden:
        _native_hidden_for.add(reason)
    else:
        _native_hidden_for.discard(reason)
    if not _native_hidden_for:
        show_native_surface()
        return
    surface = {"display_type": 'WIRE'}
    if "session" in _native_hidden_for:
        surface[viewport_overrides.SELECTED] = False
    viewport_overrides.apply(_NATIVE_SURFACE_OWNER, obj, surface)
    viewport_overrides.apply_view3d(_NATIVE_WIRE_OWNER, {"overlay.wireframe_threshold": 0.0,
                                                         "overlay.wireframe_opacity": 0.0})


def show_native_surface() -> None:
    _native_hidden_for.clear()
    viewport_overrides.restore(_NATIVE_SURFACE_OWNER)
    viewport_overrides.restore(_NATIVE_WIRE_OWNER)


def move_dab(rows) -> None:
    state, skin = _state, _skin
    if state is None or skin is None or rows is None:
        return
    verts, deform, _temp = rows
    verts = np.asarray(verts, dtype=np.int64)
    if not len(verts):
        return
    state.pos[verts] = skin.positions(verts, *deform)
    _drawn_changed()
    state.upload()
    CoreFacade.tag_redraw_areas()


def end_stroke(committed=True) -> None:
    global _skin, _stroke_value_key
    if _skin is not None:
        _drawn_changed()
    _skin = None
    before, _stroke_value_key = _stroke_value_key, None
    obj = _session_object()
    if obj is not None:
        _set_native_surface_hidden(obj, "stroke", False)
    else:
        show_native_surface()
    state = _state
    if state is not None:
        state.positions_stale = True
        if committed and obj is not None and before is not None and state.value_key == before:
            key = _value_key(obj)
            if key[1:] == before[1:]:
                state.value_key = key
    _request_refresh()



def _draw():
    state = _state
    if state is None:
        return
    context = bpy.context
    obj = context.active_object
    if obj is None or obj.name != state.name or not CoreFacade.is_skinning_session(context):
        return
    if state.value_key != _value_key(obj) or state.topology_key is None:
        _request_refresh()
    state.flush()
    if state.batch is None:
        return
    rv3d = context.region_data
    region = context.region
    if rv3d is None or region is None:
        return
    mw = obj.matrix_world
    mvp = rv3d.perspective_matrix @ mw
    space = context.space_data
    show_wire = space is not None and getattr(space.overlay, "show_paint_wire", True)
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('ALWAYS')
    gpu.state.depth_mask_set(True)
    gpu.state.color_mask_set(False, False, False, False)
    far = _far_shader()
    far.bind()
    far.uniform_float("mvp", mvp)
    state.batch.draw(far)
    if show_wire:
        state.wire.draw(far)
    gpu.state.color_mask_set(True, True, True, True)
    gpu.state.depth_test_set('LESS_EQUAL')
    shader = _surface_shader()
    shader.bind()
    shader.uniform_float("mvp", mvp)
    state.batch.draw(shader)
    if show_wire:
        winmat = rv3d.window_matrix
        view_inv = rv3d.view_matrix.inverted()
        to_obj = mw.inverted_safe()
        wire = _wire_shader()
        wire.bind()
        if winmat[3][3] == 0.0:
            eye = (*(to_obj @ view_inv.translation), 1.0)
        else:
            eye = (*(to_obj.to_3x3() @ view_inv.col[2].xyz.normalized()), 0.0)
        wire.uniform_float("mvp", mvp)
        wire.uniform_float("eye", eye)
        wire.uniform_float("color", _wire_color)
        wire.uniform_float("lift", _WIRE_BIAS_PX * 2.0 / (winmat[1][1] * max(region.height, 1)))
        gpu.state.depth_mask_set(False)
        gpu.state.blend_set('ALPHA')
        state.wire.draw(wire)
        gpu.state.blend_set('NONE')
    gpu.state.depth_mask_set(False)
    gpu.state.depth_test_set('NONE')


@persistent
def _on_depsgraph_update(scene, depsgraph):
    state = _state
    obj = bpy.context.active_object if bpy.context else None
    if obj is None or not CoreFacade.in_session(obj):
        return
    if state is None or state.name != obj.name:
        _request_refresh()
        return
    for update in depsgraph.updates:
        original = getattr(update.id, "original", None)
        if original == obj or original == obj.data:
            if update.is_updated_geometry or update.is_updated_transform:
                state.positions_stale = True
                _request_refresh()
            return


@persistent
def _on_frame_change(scene, depsgraph=None):
    state = _state
    if state is None:
        return
    obj = bpy.context.active_object if bpy.context else None
    if obj is None or obj.name != state.name or not CoreFacade.in_session(obj):
        return
    state.positions_stale = True
    try:
        _refresh()
    except Exception as exc:
        CoreFacade.debug_log("feature_domains", f"session display frame refresh failed: {exc!r}")


@persistent
def _on_undo(*_):
    global _coo_cache
    _coo_cache = None
    if _state is not None:
        _state.positions_stale = True
        _state.value_key = None
        _state.topology_key = None
    _request_refresh()


_HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
    (bpy.app.handlers.frame_change_post, _on_frame_change),
    (bpy.app.handlers.undo_post, _on_undo),
    (bpy.app.handlers.redo_post, _on_undo),
    (bpy.app.handlers.load_post, _on_undo),
)


def drop() -> None:
    global _state, _skin, _preview, _coo_cache
    _state = None
    _skin = None
    _preview = None
    _coo_cache = None
    show_native_surface()


def register():
    global _handle
    from . import live_feed
    if _handle is None and not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')
    for handlers, fn in _HANDLERS:
        if fn not in handlers:
            handlers.append(fn)
    live_feed.add_listener(_on_live_write)
    live_feed.add_mask_listener(on_live_mask)


def unregister():
    global _handle
    from . import live_feed
    live_feed.remove_listener(_on_live_write)
    live_feed.remove_mask_listener(on_live_mask)
    for handlers, fn in _HANDLERS:
        while fn in handlers:
            handlers.remove(fn)
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    drop()
