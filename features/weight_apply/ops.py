
import threading
import time

import bpy
from ...core.facade import CoreFacade
from . import draw

_ACTION_GATEWAY_TAGS = {
    "add": "add_logic", "scale": "scale_logic",
    "smooth": "smooth_logic", "sharpen": "sharpen_logic",
}

_GESTURE_LABELS = {
    "add": "Add Weight",
    "scale": "Scale Weight",
    "smooth": "Smooth Weight",
    "sharpen": "Sharpen Weight",
}

_ACTION_FIELD_LABELS = {
    "add": "Add",
    "scale": "Scale",
    "smooth": "Smooth",
    "sharpen": "Sharpen",
}

ACTION_TO_GESTURE_PAIR = {
    "add": "add_scale",
    "scale": "add_scale",
    "smooth": "smooth_sharpen",
    "sharpen": "smooth_sharpen",
}

def _probe_post_return(key: str, size: int) -> None:
    if not CoreFacade.is_profiler_enabled():
        return
    ticks = []
    t0 = time.perf_counter()

    def _tick():
        ticks.append(time.perf_counter())
        label = "undo_and_handlers" if len(ticks) == 1 else "redraw"
        with CoreFacade.profile_section(f"{key}.{label}", size=size) as s:
            s.t0 = t0
        return 0.0 if len(ticks) < 2 else None

    bpy.app.timers.register(_tick, first_interval=0.0)


_GESTURE_DRAG_THRESHOLD = 4
_GESTURE_HOLD_DELAY = 0.25
_GESTURE_DRAG_SENSITIVITY = 1.0 / 300.0
_GESTURE_APPLY_INTERVAL = 1.0 / 60.0

_GESTURE_EASE_IN_RANGE = 0.2
_GESTURE_EASE_IN_MIN_FACTOR = 0.3

_GESTURE_INPUT_INTERVAL = 1.0 / 60.0

_GRID_COARSE_STEP = 0.01
_GRID_FINE_STEP = 0.001
_GRID_FINE_HALF_RANGE = _GRID_COARSE_STEP
_FINE_MODE_DIVISOR = 5.0
_PRECOMPUTE_COARSE_LOOKAHEAD = 3
_PRECOMPUTE_MAX_QUEUED = 5
_PRECOMPUTE_POLL_INTERVAL = 0.01
_ENABLE_PRECOMPUTE_WORKER = True


def _quantize(drag_value, fine):
    return round(drag_value * _grid_n(fine))


def _grid_n(fine):
    return round(1.0 / _GRID_FINE_STEP) if fine else round(1.0 / _GRID_COARSE_STEP)


def _schedule_loop_select(window, area, region, location):
    def _run():
        try:
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.superskin.weight_select_loop('EXEC_DEFAULT', mode='SET', location=location)
        except (RuntimeError, ReferenceError, TypeError):
            pass
        return None

    bpy.app.timers.register(_run, first_interval=0.0)


def _bundle_reuse_enabled():
    from .brush_tool import BRUSH_ENABLED
    return BRUSH_ENABLED


_COMBINED_RESOLVERS = {
    "add_scale": (("add", lambda v: v), ("scale", lambda v: 1.0 + v)),
    "smooth_sharpen": (("smooth", lambda v: v), ("sharpen", lambda v: -v)),
}


class SUPERSKIN_OT_weight_gesture(bpy.types.Operator):
    """Apply Add, Scale, Smooth or Sharpen to the selected vertices"""
    bl_idname = "superskin.weight_gesture"
    bl_label = "Weight Apply"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.StringProperty(default="add_scale", options={'HIDDEN'})
    resolved_action: bpy.props.StringProperty(default="", options={'HIDDEN'})
    skip_under_brush: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})
    intensity: bpy.props.FloatProperty(
        name="Amount",
        description=(
            "Strength of the effect"
        ),
        default=0.0, min=0.0, soft_max=1.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (CoreFacade.is_editing_weights() and
                obj is not None and obj.type == 'MESH')

    @classmethod
    def description(cls, context, properties):
        action = properties.resolved_action
        if action == "add":
            if properties.intensity >= 1.0:
                return "Set the selected vertices to full weight"
            return "Add weight to the selected vertices"
        if action == "scale":
            if properties.intensity <= 0.0:
                return "Remove all weight from the selected vertices"
            return "Scale down the weight of the selected vertices"
        if action == "smooth":
            return "Smooth the weight of the selected vertices"
        if action == "sharpen":
            return "Sharpen the weight of the selected vertices"
        return cls.__doc__

    def _resolve(self, drag_value):
        if self.action == "add_scale":
            drag_value = max(-1.0, min(1.0, drag_value))
        positive, negative = _COMBINED_RESOLVERS[self.action]
        real_action, fn = positive if drag_value >= 0.0 else negative
        return real_action, fn(drag_value)

    def _ensure_baseline(self, context, reuse_prepared=False):
        if getattr(self, "_facade", None) is None:
            from ...core.facade import CoreFacade
            from .weight_apply_feature import WeightApplyFeature
            self._facade = CoreFacade(context)
            self._feature = WeightApplyFeature()
            self._prepared = None
            with CoreFacade.profile_section("weight_apply.snapshot"):
                if reuse_prepared and _bundle_reuse_enabled():
                    from .brush_tool import brush_prep
                    self._prepared = brush_prep.take(self._facade)
                if self._prepared is not None:
                    from .brush_tool import brush_prep
                    self._ctx = self._prepared.ctx
                    brush_prep.refresh_cheap_fields(self._ctx, self._facade)
                    self._ctx["selected"] = self._facade.get_selected_verts()
                else:
                    self._ctx = self._feature.snapshot_context(self._facade)
        return self._facade, self._feature, self._ctx

    def execute(self, context):
        one_shot = not getattr(self, "_modal_commit", False)
        if one_shot and getattr(self, "_executed_once", False):
            self._facade = None
        self._executed_once = True
        facade, feature, ctx = self._ensure_baseline(context, reuse_prepared=one_shot)
        real_action = self.resolved_action
        intensity = self.intensity
        if not real_action:
            real_action, intensity = self._resolve(0.0)
        if real_action in ("add", "scale"):
            intensity = max(0.0, min(1.0, intensity))
        else:
            intensity = max(0.0, intensity)
        context.scene.superskin_internal_transaction = True
        try:
            with CoreFacade.profile_section(f"weight_apply.{real_action}.execute_total",
                                            size=len(ctx["selected"])):
                if getattr(self, "_modal_commit", False):
                    result = self._commit_from_gesture(real_action, intensity)
                    self._final_result = result
                else:
                    result = feature.apply_action(real_action, facade, ctx, intensity)
                    self._keep_one_shot_result(result)
        finally:
            context.scene.superskin_internal_transaction = False
        _probe_post_return(f"weight_apply.{real_action}.post_return", len(ctx["selected"]))
        if result.get("status") == "CANCELLED":
            return {'CANCELLED'}
        from .weight_apply_feature import get_prefs
        p = get_prefs()
        p.last_action = real_action
        p.last_intensity = intensity
        return {'FINISHED'}

    def _keep_one_shot_result(self, result):
        if result.get("status") != "FINISHED" or not _bundle_reuse_enabled():
            return
        from .brush_tool import brush_prep
        ctx = dict(self._ctx)
        ctx["layer_int"] = result["layer_int"]
        ctx["mask_dict"] = result["mask_dict"]
        self._feature.carry_layer_store(ctx, result)
        prepared = self._prepared
        bvh = prepared.bvh if prepared is not None and not prepared.pose_stale else None
        posed_coords = prepared.posed_coords if bvh is not None else None
        try:
            brush_prep.store(self._facade, ctx, bvh, posed_coords)
        except Exception:
            brush_prep.invalidate()

    def draw(self, context):
        label = _ACTION_FIELD_LABELS.get(self.resolved_action, "Amount")
        split = self.layout.split(factor=0.3)
        split.label(text=label)
        split.prop(self, "intensity", text="", slider=True)

    def invoke(self, context, event):
        if self.skip_under_brush:
            from .brush_tool.brush_tool import _WEIGHT_BRUSH_TOOL_IDNAME, _get_active_tool_idname
            if _get_active_tool_idname(context) == _WEIGHT_BRUSH_TOOL_IDNAME:
                return {'PASS_THROUGH'}
        if not self.properties.is_property_set("action"):
            self.action = "smooth_sharpen" if event.type == 'RIGHTMOUSE' else "add_scale"
        self._ensure_baseline(context, reuse_prepared=True)

        self._trigger_type = event.type
        self._initial_x = event.mouse_x
        self._initial_y = event.mouse_y
        self._press_time = time.perf_counter()
        self._press_region_xy = (event.mouse_region_x, event.mouse_region_y)
        self._click_selects_loop = (self.action == "add_scale" and not event.ctrl
                                    and not event.shift and not event.oskey)
        self._armed = False
        self._is_dragging = False
        self._drag_value = 0.0
        self._last_input_time = 0.0

        self._action_data_cache = {}
        self._rust_gateways = {}
        self._compute_thread = None
        self._compute_result = None
        self._compute_error = None
        self._pending_action = None
        self._pending_intensity = None
        self._pending_action_data = None
        self._pending_key = None
        self._gesture_finished = False

        self._fine_mode = False
        self._fine_anchor_idx = None
        self._last_coarse_idx = None
        self._last_wanted_key = None
        self._last_applied_key = None
        self._compute_cache = {}
        self._in_flight_keys = set()
        self._precompute_wanted = []
        self._precompute_lock = threading.Lock()
        self._precompute_stop = threading.Event()
        self._rust_call_lock = threading.Lock()
        self._precompute_errors = []
        self._working = {}
        self._modal_commit = False
        self._final_result = None
        self._wrote_live = False

        (positive, _), (negative, _) = _COMBINED_RESOLVERS[self.action]
        self._ensure_action_data(positive)
        self._deferred_actions = [negative]

        self._facade.begin_live_stroke(keep_state=self._prepared is not None)
        self._live_stroke_open = True

        self._timer = context.window_manager.event_timer_add(
            _GESTURE_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)

        self._use_precompute = _ENABLE_PRECOMPUTE_WORKER
        if self._use_precompute:
            self._precompute_thread = threading.Thread(target=self._precompute_worker, daemon=True)
            self._precompute_thread.start()

        if not self._click_selects_loop:
            self._arm(context)
        return {'RUNNING_MODAL'}

    def _arm(self, context):
        self._armed = True
        context.window.cursor_warp(self._initial_x, self._initial_y)
        context.window.cursor_modal_set('NONE')
        real_action, intensity = self._resolve(0.0)
        draw.show(self.action)
        draw.update(real_action, intensity, 0.0)

    def _try_arm(self, context):
        if not self._armed and time.perf_counter() - self._press_time >= _GESTURE_HOLD_DELAY:
            self._arm(context)
        return self._armed

    def _remove_timer(self, context):
        context.window_manager.event_timer_remove(self._timer)

    def _end_live_stroke(self, keep=False):
        if not getattr(self, "_live_stroke_open", False):
            return
        self._live_stroke_open = False
        result = self._final_result
        finished = result is not None and result.get("status") == "FINISHED"
        keep = keep and _bundle_reuse_enabled() and (finished or not self._wrote_live)
        obj_name = self._facade.get_obj().name
        try:
            if keep:
                self._facade.end_live_stroke(keep_state=True)
            else:
                self._facade.end_live_stroke()
        except Exception as exc:
            self._facade.drop_live_state(obj_name)
            print(f"[SuperSkinPro] end_live_stroke failed: {exc}")
            return
        finally:
            self._action_data_cache = {}
            self._working = None
            self._final_result = None
        if not keep:
            return
        from .brush_tool import brush_prep
        ctx = dict(self._ctx)
        if finished:
            ctx["layer_int"] = result["layer_int"]
            ctx["mask_dict"] = result["mask_dict"]
            self._feature.carry_layer_store(ctx, result)
        prepared = self._prepared
        bvh = prepared.bvh if prepared is not None and not prepared.pose_stale else None
        posed_coords = prepared.posed_coords if bvh is not None else None
        try:
            brush_prep.store(self._facade, ctx, bvh, posed_coords)
        except Exception:
            self._facade.drop_live_state(obj_name)

    def cancel(self, context):
        self._gesture_finished = True
        self._precompute_stop.set()
        try:
            self._remove_timer(context)
        except Exception:
            pass
        self._end_live_stroke()

    def _ensure_action_data(self, real_action):
        if real_action not in self._action_data_cache:
            data = self._feature._prepare_action_data(real_action, self._facade, self._ctx)
            tag = _ACTION_GATEWAY_TAGS[real_action]
            gateway = self._ensure_rust_gateway(real_action)[tag]
            try:
                self._feature.build_session(real_action, data, gateway)
            except ValueError:
                raise
            except Exception as exc:
                self._facade.debug_log(
                    "feature_domains", f"weight_apply session build failed for {real_action}: {exc!r}",
                )
            self._action_data_cache[real_action] = data
        return self._action_data_cache[real_action]

    def _prepare_deferred(self):
        while self._deferred_actions:
            real_action = self._deferred_actions.pop()
            if real_action not in self._action_data_cache:
                self._ensure_action_data(real_action)
                return

    def _commit_from_gesture(self, real_action, intensity):
        action_data = self._ensure_action_data(real_action)
        compute_result = self._feature._dispatch_compute(
            real_action, action_data, intensity,
            rust_gateways=self._ensure_rust_gateway(real_action),
        )
        return self._feature._finish_write(
            real_action, self._facade, self._ctx, action_data, compute_result,
            working=self._working,
        )

    def _ensure_rust_gateway(self, real_action):
        tag = _ACTION_GATEWAY_TAGS[real_action]
        if tag not in self._rust_gateways:
            self._rust_gateways[tag] = CoreFacade.get_rust_gateway(tag)
        return {tag: self._rust_gateways[tag]}

    def _start_compute(self, real_action, intensity):
        action_data = self._ensure_action_data(real_action)
        rust_gateways = self._ensure_rust_gateway(real_action)
        self._pending_action = real_action
        self._pending_intensity = intensity
        self._pending_action_data = action_data

        def _worker():
            try:
                with self._rust_call_lock:
                    result = self._feature._dispatch_compute(
                        real_action, action_data, intensity, rust_gateways=rust_gateways,
                    )
            except Exception as exc:
                if not self._gesture_finished:
                    self._compute_error = exc
                return
            if not self._gesture_finished:
                self._compute_result = result

        self._compute_thread = threading.Thread(target=_worker, daemon=True)
        self._compute_thread.start()

    def _real_action_for_idx(self, idx):
        positive, negative = _COMBINED_RESOLVERS[self.action]
        return positive[0] if idx >= 0 else negative[0]

    def _is_forward_progress(self, key, current_key):
        if key is None or key == self._last_applied_key:
            return False
        last = self._last_applied_key
        last_value = last[1] / _grid_n(last[2]) if last is not None else 0.0
        current_value = current_key[1] / _grid_n(current_key[2])
        value = key[1] / _grid_n(key[2])
        return min(last_value, current_value) <= value <= max(last_value, current_value)

    def _intensity_for_key(self, real_action, idx, fine):
        drag_value = idx / _grid_n(fine)
        if self.action == "add_scale":
            drag_value = max(-1.0, min(1.0, drag_value))
        positive, negative = _COMBINED_RESOLVERS[self.action]
        fn = positive[1] if real_action == positive[0] else negative[1]
        return fn(drag_value)

    def _update_precompute_wanted(self, current_idx, fine):
        targets = []

        def _add(idx):
            key = (self._real_action_for_idx(idx), idx, fine)
            if key not in self._compute_cache and key not in targets:
                targets.append(key)

        if fine:
            fine_per_coarse = _grid_n(True) // _grid_n(False)
            anchor_fine_idx = self._fine_anchor_idx * fine_per_coarse
            half_span = round(_GRID_FINE_HALF_RANGE * _grid_n(True))
            offsets = sorted(range(-half_span, half_span + 1), key=lambda o: abs(current_idx - (anchor_fine_idx + o)))
            for off in offsets:
                _add(anchor_fine_idx + off)
        else:
            direction = 0
            if self._last_coarse_idx is not None:
                direction = current_idx - self._last_coarse_idx
            direction = 1 if direction > 0 else (-1 if direction < 0 else 0)
            _add(current_idx - 1)
            _add(current_idx + 1)
            if direction != 0:
                for step in range(2, _PRECOMPUTE_COARSE_LOOKAHEAD + 2):
                    _add(current_idx + direction * step)
            self._last_coarse_idx = current_idx

        if self.action == "add_scale":
            limit = _grid_n(fine)
            targets = [k for k in targets if -limit <= k[1] <= limit]

        with self._precompute_lock:
            self._precompute_wanted = targets[:_PRECOMPUTE_MAX_QUEUED]

    def _precompute_worker(self):
        while not self._precompute_stop.is_set():
            target = None
            with self._precompute_lock:
                for candidate in self._precompute_wanted:
                    if (candidate[0] in self._action_data_cache
                            and candidate not in self._compute_cache
                            and candidate not in self._in_flight_keys):
                        target = candidate
                        self._in_flight_keys.add(candidate)
                        break
            if target is None:
                self._precompute_stop.wait(_PRECOMPUTE_POLL_INTERVAL)
                continue

            real_action, idx, fine = target
            intensity = self._intensity_for_key(real_action, idx, fine)
            action_data = self._action_data_cache.get(real_action)
            if action_data is None:
                with self._precompute_lock:
                    self._in_flight_keys.discard(target)
                continue
            tag = _ACTION_GATEWAY_TAGS[real_action]
            rust_gateways = {tag: self._rust_gateways[tag]}
            try:
                with self._rust_call_lock:
                    result = self._feature._dispatch_compute(
                        real_action, action_data, intensity, rust_gateways=rust_gateways,
                    )
            except Exception as exc:
                if not self._precompute_stop.is_set():
                    self._precompute_errors.append((target, exc))
                with self._precompute_lock:
                    self._in_flight_keys.discard(target)
                continue

            if not self._precompute_stop.is_set() and not self._gesture_finished:
                self._compute_cache[target] = result
            with self._precompute_lock:
                self._in_flight_keys.discard(target)

    def _apply_result(self, context, real_action, action_data, intensity, result):
        context.scene.superskin_internal_transaction = True
        try:
            write_result = self._feature._finish_write(
                real_action, self._facade, self._ctx, action_data, result,
                working=self._working,
            )
        finally:
            context.scene.superskin_internal_transaction = False
        if write_result.get("status") != "CANCELLED":
            self._wrote_live = True
            mode_suffix = " (Slow)" if self._fine_mode else ""
            context.area.header_text_set(
                f"{_GESTURE_LABELS.get(real_action, real_action)}: {intensity:.2f}" + mode_suffix
            )

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            if not self._try_arm(context):
                return {'RUNNING_MODAL'}
            now = time.perf_counter()
            if now - self._last_input_time < _GESTURE_INPUT_INTERVAL:
                return {'RUNNING_MODAL'}
            self._last_input_time = now

            ctrl_now = event.ctrl
            if ctrl_now != self._fine_mode:
                self._fine_mode = ctrl_now
                self._fine_anchor_idx = _quantize(self._drag_value, fine=False) if ctrl_now else None

            delta = event.mouse_x - self._initial_x
            if not self._is_dragging and abs(delta) > _GESTURE_DRAG_THRESHOLD:
                self._is_dragging = True
            if self._is_dragging:
                divisor = _FINE_MODE_DIVISOR if self._fine_mode else 1.0
                ease_factor = 1.0
                if abs(self._drag_value) < _GESTURE_EASE_IN_RANGE:
                    t = abs(self._drag_value) / _GESTURE_EASE_IN_RANGE
                    ease_factor = _GESTURE_EASE_IN_MIN_FACTOR + (1.0 - _GESTURE_EASE_IN_MIN_FACTOR) * t
                sensitivity = _GESTURE_DRAG_SENSITIVITY * ease_factor / divisor
                new_value = self._drag_value + delta * sensitivity
                if self.action == "add_scale":
                    new_value = max(-1.0, min(1.0, new_value))
                self._drag_value = new_value
                context.window.cursor_warp(self._initial_x, self._initial_y)

                current_idx = _quantize(self._drag_value, self._fine_mode)
                if self._use_precompute:
                    wanted_key = (self._fine_mode, current_idx)
                    if wanted_key != self._last_wanted_key:
                        self._update_precompute_wanted(current_idx, self._fine_mode)
                        self._last_wanted_key = wanted_key
                real_action = self._real_action_for_idx(current_idx)
                intensity = self._intensity_for_key(real_action, current_idx, self._fine_mode)

                draw.update(
                    real_action, intensity, current_idx / _grid_n(self._fine_mode),
                    fine_mode=self._fine_mode,
                )

        elif event.type == 'TIMER':
            self._try_arm(context)
            if self._precompute_errors:
                errors, self._precompute_errors = self._precompute_errors, []
                for target, exc in errors:
                    self._facade.debug_log(
                        "feature_domains",
                        f"weight_apply gesture precompute failed for {target!r}: {exc!r}",
                    )

            did_work = False
            if self._compute_thread is not None and not self._compute_thread.is_alive():
                did_work = True
                self._compute_thread.join()
                result = self._compute_result
                error = self._compute_error
                pending_key = self._pending_key
                pending_action = self._pending_action
                pending_action_data = self._pending_action_data
                pending_intensity = self._pending_intensity
                self._compute_thread = None
                self._compute_result = None
                self._compute_error = None
                self._pending_key = None
                with self._precompute_lock:
                    self._in_flight_keys.discard(pending_key)

                if error is not None:
                    self._facade.debug_log(
                        "feature_domains",
                        f"weight_apply gesture background compute failed: {error!r}",
                    )
                elif result is not None:
                    self._compute_cache[pending_key] = result
                    current_idx = _quantize(self._drag_value, self._fine_mode)
                    current_key = (self._real_action_for_idx(current_idx), current_idx, self._fine_mode)
                    if pending_key == current_key or self._is_forward_progress(pending_key, current_key):
                        self._apply_result(context, pending_action, pending_action_data, pending_intensity, result)
                        self._last_applied_key = pending_key

            if self._is_dragging:
                current_idx = _quantize(self._drag_value, self._fine_mode)
                real_action = self._real_action_for_idx(current_idx)
                key = (real_action, current_idx, self._fine_mode)
                if key != self._last_applied_key:
                    cached = self._compute_cache.get(key)
                    intensity = self._intensity_for_key(real_action, current_idx, self._fine_mode)
                    if cached is not None:
                        action_data = self._ensure_action_data(real_action)
                        self._apply_result(context, real_action, action_data, intensity, cached)
                        self._last_applied_key = key
                        did_work = True
                    elif self._compute_thread is None and key not in self._in_flight_keys:
                        self._pending_key = key
                        with self._precompute_lock:
                            self._in_flight_keys.add(key)
                        self._start_compute(real_action, intensity)
                        did_work = True

            if not did_work:
                self._prepare_deferred()

        elif event.type == self._trigger_type and event.value == 'RELEASE':
            self._gesture_finished = True
            self._precompute_stop.set()
            self._compute_cache.clear()
            with self._precompute_lock:
                self._precompute_wanted.clear()
                self._in_flight_keys.clear()
            self._remove_timer(context)
            context.window.cursor_modal_restore()
            context.area.header_text_set(None)
            draw.hide()
            if not self._is_dragging:
                self._end_live_stroke(keep=True)
                if self._click_selects_loop and not self._armed:
                    _schedule_loop_select(context.window, context.area, context.region,
                                          self._press_region_xy)
                return {'CANCELLED'}
            real_action, intensity = self._resolve(self._drag_value)
            self.resolved_action = real_action
            self.intensity = intensity
            self._modal_commit = True
            committed = False
            try:
                ret = self.execute(context)
                committed = True
                return ret
            finally:
                self._modal_commit = False
                self._end_live_stroke(keep=committed)

        return {'RUNNING_MODAL'}



class SUPERSKIN_OT_repeat_last_weight_apply(bpy.types.Operator):
    """Repeat the last Add, Scale, Smooth or Sharpen"""
    bl_idname = "superskin.repeat_last_weight_apply"
    bl_label = "Repeat Last Weight Apply"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not (CoreFacade.is_editing_weights() and
                obj is not None and obj.type == 'MESH'):
            return False
        from .weight_apply_feature import get_prefs
        return bool(get_prefs().last_action)

    def execute(self, context):
        from .weight_apply_feature import get_prefs
        p = get_prefs()
        return bpy.ops.superskin.weight_gesture(
            'EXEC_DEFAULT', resolved_action=p.last_action, intensity=p.last_intensity,
        )



_classes = (
    SUPERSKIN_OT_weight_gesture,
    SUPERSKIN_OT_repeat_last_weight_apply,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
