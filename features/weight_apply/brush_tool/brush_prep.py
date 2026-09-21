"""Stroke preparation cache for the Weight Brush."""

import time

import bpy
from bpy.app.handlers import persistent

from ....core.facade import CoreFacade
from .brush_logic import build_bvh, polygon_hide_flags

_IDLE_BEFORE_WARM = 0.2
_POST_WRITE_GRACE = 0.5
_GEN_KEY = "__ssp_deform_gen"
_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"

_bundle = None
_grace_until = 0.0
_last_move = 0.0
_warm_pending = False


class _Bundle:
    __slots__ = ("obj_name", "layer_idx", "gen", "vert_count", "fingerprint",
                 "ctx", "bvh", "posed_coords", "pose_stale")

    def __init__(self, obj_name, layer_idx, gen, vert_count, fingerprint, ctx, bvh, posed_coords):
        self.obj_name = obj_name
        self.layer_idx = layer_idx
        self.gen = gen
        self.vert_count = vert_count
        self.fingerprint = fingerprint
        self.ctx = ctx
        self.bvh = bvh
        self.posed_coords = posed_coords
        self.pose_stale = False


def _fingerprint(obj):
    return hash((
        tuple((k, v) for k, v in obj.items() if isinstance(v, str)),
        polygon_hide_flags(obj.data).tobytes(),
    ))


def _matches(facade, bundle):
    obj = facade.get_obj()
    return (
        obj.name == bundle.obj_name
        and len(obj.data.vertices) == bundle.vert_count
        and obj.get(_GEN_KEY, 0) == bundle.gen
        and facade.get_active_layer_index() == bundle.layer_idx
        and _fingerprint(obj) == bundle.fingerprint
    )


def invalidate():
    global _bundle
    b, _bundle = _bundle, None
    if b is not None:
        CoreFacade.drop_live_state(b.obj_name)


def take(facade):
    """Hand the prepared bundle to a starting stroke, or None when there is no
    current one. The stroke owns it until it calls store()."""
    global _bundle
    b, _bundle = _bundle, None
    if b is None:
        return None
    if not _matches(facade, b):
        CoreFacade.drop_live_state(b.obj_name)
        return None
    return b


def refresh_cheap_fields(ctx, facade):
    """Re-read the ctx entries that can change without touching stored layer
    data (active bone, mask mode, locks, selection)."""
    ctx["is_mask"] = facade.is_mask_context()
    ctx["active_vg_id"] = facade.get_active_vg_id()
    ctx["locks_id"] = facade.get_locks_by_id()
    ctx["selected"] = []
    ctx["_nearest_bones_cache"] = {}


def store(facade, ctx, bvh, posed_coords):
    """Keep a finished stroke's state for the next one. Call after the
    stroke's storage commit."""
    global _bundle, _grace_until
    obj = facade.get_obj()
    _grace_until = time.monotonic() + _POST_WRITE_GRACE
    _bundle = _Bundle(
        obj.name, facade.get_active_layer_index(), obj.get(_GEN_KEY, 0),
        len(obj.data.vertices), _fingerprint(obj), ctx, bvh, posed_coords,
    )


def selected_vertex_set(mesh):
    import numpy as np
    sel = np.zeros(len(mesh.vertices), dtype=bool)
    mesh.vertices.foreach_get("select", sel)
    return frozenset(np.flatnonzero(sel).tolist()) or None


def _warm(context):
    global _bundle, _grace_until
    from .brush_ops import SUPERSKIN_OT_weight_brush
    if SUPERSKIN_OT_weight_brush._stroke_active:
        return
    obj = context.active_object
    if obj is None or obj.type != 'MESH' or obj.mode != 'WEIGHT_PAINT':
        return
    if not (CoreFacade.is_system_activated() and CoreFacade.is_editing_weights()):
        return

    facade = CoreFacade(context)
    if _bundle is not None and _matches(facade, _bundle):
        if _bundle.pose_stale:
            _mesh, _bundle.bvh, _bundle.posed_coords = build_bvh(context, obj)
            _bundle.pose_stale = False
            _grace_until = time.monotonic() + _POST_WRITE_GRACE
        return
    invalidate()

    from ..weight_apply_feature import WeightApplyFeature
    ctx = WeightApplyFeature().snapshot_context(facade, need_selected=False)
    _mesh, bvh, posed_coords = build_bvh(context, obj)
    facade.get_cached_mesh_neighbors()
    facade.warm_live_state()

    _grace_until = time.monotonic() + _POST_WRITE_GRACE
    _bundle = _Bundle(
        obj.name, facade.get_active_layer_index(), obj.get(_GEN_KEY, 0),
        len(obj.data.vertices), _fingerprint(obj), ctx, bvh, posed_coords,
    )


def _warm_tick():
    global _warm_pending
    if time.monotonic() - _last_move < _IDLE_BEFORE_WARM:
        return _IDLE_BEFORE_WARM
    _warm_pending = False
    try:
        _warm(bpy.context)
    except Exception:
        invalidate()
    return None


def _schedule_warm():
    global _warm_pending
    if not _warm_pending:
        _warm_pending = True
        bpy.app.timers.register(_warm_tick, first_interval=_IDLE_BEFORE_WARM)


def note_hover():
    """Called on every hover update; schedules a warm-up for the next idle
    moment when no current bundle exists."""
    global _last_move
    _last_move = time.monotonic()
    if _bundle is None or _bundle.pose_stale:
        _schedule_warm()


def _rewarm_if_brush_tool_active():
    """Re-arm the idle warm-up right after the bundle was discarded, so an undo or a weight
    edit followed by an."""
    global _last_move
    try:
        ctx = bpy.context
        tool = ctx.workspace.tools.from_space_view3d_mode(ctx.mode, create=False)
    except Exception:
        return
    if tool is not None and tool.idname == _BRUSH_TOOL_IDNAME:
        _last_move = time.monotonic()
        _schedule_warm()


@persistent
def _on_depsgraph_update(scene, depsgraph):
    if _bundle is None or time.monotonic() < _grace_until:
        return
    from .brush_ops import SUPERSKIN_OT_weight_brush
    if SUPERSKIN_OT_weight_brush._stroke_active:
        return
    pose_only = (bpy.types.Object, bpy.types.Armature)
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Mesh):
            invalidate()
            _rewarm_if_brush_tool_active()
            return
        if isinstance(update.id, pose_only):
            _bundle.pose_stale = True


@persistent
def _on_reset(*_args):
    invalidate()
    _rewarm_if_brush_tool_active()


_HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
    (bpy.app.handlers.undo_post, _on_reset),
    (bpy.app.handlers.redo_post, _on_reset),
    (bpy.app.handlers.load_post, _on_reset),
)


def register():
    for handler_list, fn in _HANDLERS:
        if fn not in handler_list:
            handler_list.append(fn)


def unregister():
    invalidate()
    for handler_list, fn in _HANDLERS:
        while fn in handler_list:
            handler_list.remove(fn)
