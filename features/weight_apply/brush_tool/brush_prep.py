
import time

import bpy
from bpy.app.handlers import persistent

from ....core.facade import CoreFacade
from ..gc_pause import collection_paused
from .brush_logic import (build_brush_graph, build_bvh, drop_surface_cache, mesh_topology,
                          polygon_hide_flags, keep_volume_steps)

_IDLE_BEFORE_WARM = 0.2
_POST_WRITE_GRACE = 0.5
_GEN_KEY = CoreFacade.DEFORM_GEN_KEY
_BRUSH_TOOL_IDNAME = "superskin.weight_brush_tool"

_WARM_STEP_GAP = 0.01

_bundle = None
_grace_until = 0.0
_last_move = 0.0
_warm_pending = False
_warm_job = None
_warm_job_obj = None
_warm_job_pose_stale = False
_warm_eager = False
_shape_gen = 0


class _Bundle:
    __slots__ = ("obj_name", "uids", "layer_idx", "gen", "vert_count", "fingerprint",
                 "ctx", "bvh", "posed_coords", "pose_stale", "expect_own_update")

    def __init__(self, obj, layer_idx, gen, vert_count, fingerprint, ctx, bvh, posed_coords):
        self.obj_name = obj.name
        self.uids = _uids(obj)
        self.layer_idx = layer_idx
        self.gen = gen
        self.vert_count = vert_count
        self.fingerprint = fingerprint
        self.ctx = ctx
        self.bvh = bvh
        self.posed_coords = posed_coords
        self.pose_stale = False
        self.expect_own_update = False


def _fingerprint(obj):
    return hash((
        tuple((k, v) for k, v in obj.items() if isinstance(v, str)),
        polygon_hide_flags(obj.data).tobytes(),
    ))


def _uids(obj):
    return obj.session_uid, obj.data.session_uid


def _matches(facade, bundle):
    obj = facade.get_obj()
    return (
        _uids(obj) == bundle.uids
        and obj.name == bundle.obj_name
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
    global _bundle
    _abort_warm_job()
    b, _bundle = _bundle, None
    if b is None:
        return None
    if not _matches(facade, b):
        CoreFacade.drop_live_state(b.obj_name)
        return None
    return b


def refresh_cheap_fields(ctx, facade):
    ctx["is_mask"] = facade.is_mask_context()
    ctx["active_vg_id"] = facade.get_active_vg_id()
    ctx["locks_id"] = facade.get_locks_by_id()
    ctx["selected"] = []
    ctx["_scale_targets_cache"] = {}
    ctx.pop("_posed_cache", None)


def store(facade, ctx, bvh, posed_coords):
    global _bundle, _grace_until, _shape_gen
    obj = facade.get_obj()
    if bvh is None:
        _shape_gen += 1
    _grace_until = time.monotonic() + _POST_WRITE_GRACE
    _bundle = _Bundle(
        obj, facade.get_active_layer_index(), obj.get(_GEN_KEY, 0),
        len(obj.data.vertices), _fingerprint(obj), ctx, bvh, posed_coords,
    )
    _bundle.pose_stale = bvh is None
    _bundle.expect_own_update = True


def visible_vertex_set(mesh):
    import numpy as np
    from .. import mesh_flags
    hidden = mesh_flags.vertex_hide(mesh)
    if not hidden.any():
        return None
    return frozenset(np.flatnonzero(~hidden).tolist())


def _keep_volume_steps(facade, ctx, obj, posed_coords):
    from .brush_ops import _keeps_volume, get_brush_prefs, local_brush_radius
    p = get_brush_prefs()
    if posed_coords is None or not _keeps_volume(p):
        return
    try:
        coords = ctx.get("_coords_cache")
        if coords is None:
            coords = ctx["_coords_cache"] = facade.get_vertex_coordinates()
        for _step in keep_volume_steps(facade, coords, posed_coords, obj.data,
                                       local_brush_radius(obj, p.brush_radius)):
            yield
    except Exception as exc:
        facade.debug_log("feature_domains", f"weight brush keep volume warm failed: {exc!r}")


def _warm_mirror(facade):
    from .brush_ops import mirror_axis
    from ...weight_toolkit.public_api import mirror_paint_map
    axis = mirror_axis()
    if axis is None:
        return False
    try:
        mirror_paint_map(facade, axis)
    except Exception as exc:
        facade.debug_log("feature_domains", f"weight brush mirror map warm failed: {exc!r}")
    return True


def _first_dab_steps(facade, ctx, obj, posed_coords):
    import numpy.ma
    from ..weight_apply_feature import WeightApplyFeature
    steps = (
        facade.get_cached_mesh_neighbors,
        lambda: WeightApplyFeature().warm_mesh_context(facade, ctx),
        lambda: mesh_topology(obj.data).vert_corners(),
        lambda: build_brush_graph(facade, posed_coords) if posed_coords is not None else None,
    )
    for step in steps:
        try:
            step()
        except Exception as exc:
            facade.debug_log("feature_domains", f"weight brush first-dab warm failed: {exc!r}")
            return
        yield


def _warm_steps(context):
    global _bundle, _grace_until
    obj = context.active_object
    if not CoreFacade.in_session(obj) or not CoreFacade.is_editing_weights():
        return
    facade = CoreFacade(context)
    if _warm_mirror(facade):
        yield
    if _bundle is not None and _matches(facade, _bundle):
        bundle = _bundle
        if not bundle.pose_stale:
            return
        _mesh, bundle.bvh, bundle.posed_coords = build_bvh(context, obj)
        bundle.pose_stale = False
        yield
        yield from _first_dab_steps(facade, bundle.ctx, obj, bundle.posed_coords)
        yield from _keep_volume_steps(facade, bundle.ctx, obj, bundle.posed_coords)
        _grace_until = time.monotonic() + _POST_WRITE_GRACE
        return
    invalidate()

    from ..weight_apply_feature import WeightApplyFeature
    key = (facade.get_active_layer_index(), obj.get(_GEN_KEY, 0), len(obj.data.vertices),
           _fingerprint(obj))
    ctx = WeightApplyFeature().snapshot_context(facade, need_selected=False)
    _mesh, bvh, posed_coords = build_bvh(context, obj)
    yield
    facade.warm_live_state()
    yield
    yield from _first_dab_steps(facade, ctx, obj, posed_coords)
    yield from _keep_volume_steps(facade, ctx, obj, posed_coords)

    _grace_until = time.monotonic() + _POST_WRITE_GRACE
    _bundle = _Bundle(obj, *key, ctx, bvh, posed_coords)
    _bundle.pose_stale = _warm_job_pose_stale


@collection_paused()
def _warm(context):
    global _warm_job_pose_stale
    from .brush_ops import SUPERSKIN_OT_weight_brush
    if SUPERSKIN_OT_weight_brush._stroke_active:
        return
    _warm_job_pose_stale = False
    for _step in _warm_steps(context):
        pass


def _abort_warm_job():
    global _warm_job, _warm_eager
    _warm_job = None
    _warm_eager = False


def _advance_warm_job() -> bool:
    global _warm_job, _warm_job_obj, _warm_job_pose_stale, _grace_until
    from .brush_ops import SUPERSKIN_OT_weight_brush
    context = bpy.context
    obj = context.active_object
    if SUPERSKIN_OT_weight_brush._stroke_active:
        _warm_job = None
        return False
    if _warm_job is None:
        _warm_job = _warm_steps(context)
        _warm_job_obj = _uids(obj) if obj is not None else None
        _warm_job_pose_stale = False
    elif obj is None or _uids(obj) != _warm_job_obj or not CoreFacade.in_session(obj):
        _warm_job = None
        return False
    _grace_until = time.monotonic() + _POST_WRITE_GRACE
    try:
        with collection_paused():
            next(_warm_job)
    except StopIteration:
        _warm_job = None
        return False
    except Exception:
        _warm_job = None
        invalidate()
        return False
    _grace_until = time.monotonic() + _POST_WRITE_GRACE
    return True


def _bone_picker_running() -> bool:
    try:
        from ...bone_picker.public_api import is_bone_picker_active
        return is_bone_picker_active()
    except Exception:
        return False


def _warm_tick():
    global _warm_pending, _warm_eager
    if not _brush_tool_active():
        _warm_pending = False
        _warm_eager = False
        return None
    if _bone_picker_running() or (
            not _warm_eager and time.monotonic() - _last_move < _IDLE_BEFORE_WARM):
        return _IDLE_BEFORE_WARM
    if _advance_warm_job():
        return _WARM_STEP_GAP
    _warm_pending = False
    _warm_eager = False
    return None


def _schedule_warm(first_interval=_IDLE_BEFORE_WARM):
    global _warm_pending
    if not _warm_pending:
        _warm_pending = True
        bpy.app.timers.register(_warm_tick, first_interval=first_interval)


def get_shape_gen() -> int:
    return _shape_gen


def note_hover():
    global _last_move
    _last_move = time.monotonic()
    if _bundle is None or _bundle.pose_stale:
        _schedule_warm()


def warm_soon():
    global _warm_eager
    if _brush_tool_active():
        _warm_eager = True
        _schedule_warm(_WARM_STEP_GAP)


def _brush_tool_active() -> bool:
    try:
        ctx = bpy.context
        tool = ctx.workspace.tools.from_space_view3d_mode(ctx.mode, create=False)
    except Exception:
        return False
    return tool is not None and tool.idname == _BRUSH_TOOL_IDNAME


def _rewarm_if_brush_tool_active():
    global _last_move
    if _brush_tool_active():
        _last_move = time.monotonic()
        _schedule_warm()


@persistent
def _on_depsgraph_update(scene, depsgraph):
    global _shape_gen
    pose_only = (bpy.types.Object, bpy.types.Armature)
    shaping = (bpy.types.Object, bpy.types.Armature, bpy.types.Mesh, bpy.types.Key)
    for update in depsgraph.updates:
        if isinstance(update.id, shaping):
            _shape_gen += 1

    if time.monotonic() < _grace_until:
        return
    from .brush_ops import SUPERSKIN_OT_weight_brush
    if SUPERSKIN_OT_weight_brush._stroke_active:
        return
    if _warm_job is not None:
        _note_update_during_warm(depsgraph, pose_only)
    if _bundle is None:
        return
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Mesh):
            if _bundle.expect_own_update:
                _bundle.expect_own_update = False
                return
            invalidate()
            _rewarm_if_brush_tool_active()
            return
        if isinstance(update.id, pose_only):
            _bundle.pose_stale = True


def _note_update_during_warm(depsgraph, pose_only):
    global _warm_job_pose_stale
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Mesh):
            _abort_warm_job()
            _rewarm_if_brush_tool_active()
            return
        if isinstance(update.id, pose_only):
            _warm_job_pose_stale = True


def _drop_mesh_caches():
    from ..weight_apply_feature import drop_mesh_context
    drop_surface_cache()
    drop_mesh_context()


@persistent
def _on_reset(*_args):
    _abort_warm_job()
    invalidate()
    _drop_mesh_caches()
    _rewarm_if_brush_tool_active()


@persistent
def _on_undo(*_args):
    global _shape_gen
    _shape_gen += 1
    _abort_warm_job()
    invalidate()
    _rewarm_if_brush_tool_active()


_HANDLERS = (
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
    (bpy.app.handlers.undo_post, _on_undo),
    (bpy.app.handlers.redo_post, _on_undo),
    (bpy.app.handlers.load_post, _on_reset),
)


def register():
    for handler_list, fn in _HANDLERS:
        if fn not in handler_list:
            handler_list.append(fn)


def unregister():
    _abort_warm_job()
    invalidate()
    _drop_mesh_caches()
    for handler_list, fn in _HANDLERS:
        while fn in handler_list:
            handler_list.remove(fn)
