"""Weight-apply operators — Add, Scale, Smooth, Sharpen."""

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
    """Record wall time from operator return to the first and second main-loop timer tick,
    capturing the undo push and the following draw/depsgraph pass that no in-operator timer can see."""
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


_GESTURE_DRAG_THRESHOLD = 4  # pixels before a click becomes a drag
_GESTURE_DRAG_SENSITIVITY = 1.0 / 300.0  # 300px drag spans 0 -> +-1.0
_GESTURE_APPLY_INTERVAL = 1.0 / 60.0  # caps apply+flatten ticks, independent of MOUSEMOVE rate

# Taper sensitivity near 0.0 so small, precise adjustments near the neutral
# start of a drag aren't lost to constant pixel-to-value sensitivity.
_GESTURE_EASE_IN_RANGE = 0.2
_GESTURE_EASE_IN_MIN_FACTOR = 0.3

_GESTURE_INPUT_INTERVAL = 1.0 / 60.0

_GRID_COARSE_STEP = 0.01
_GRID_FINE_STEP = 0.001
_GRID_FINE_HALF_RANGE = _GRID_COARSE_STEP  # one coarse step each side, in fine mode
_FINE_MODE_DIVISOR = 5.0
_PRECOMPUTE_COARSE_LOOKAHEAD = 3
_PRECOMPUTE_MAX_QUEUED = 5
_PRECOMPUTE_POLL_INTERVAL = 0.01
_ENABLE_PRECOMPUTE_WORKER = False


def _quantize(drag_value, fine):
    """Snap to an int grid bucket (not a float) so cache keys never depend on float rounding."""
    return round(drag_value * _grid_n(fine))


def _grid_n(fine):
    return round(1.0 / _GRID_FINE_STEP) if fine else round(1.0 / _GRID_COARSE_STEP)


_COMBINED_RESOLVERS = {
    "add_scale": (("add", lambda v: v), ("scale", lambda v: 1.0 + v)),
    "smooth_sharpen": (("smooth", lambda v: v), ("sharpen", lambda v: -v)),
}


class SUPERSKIN_OT_weight_gesture(bpy.types.Operator):
    """Runs Add/Scale/Smooth/Sharpen via two entry points sharing one code path: a plain panel-
    button click."""
    bl_idname = "superskin.weight_gesture"
    bl_label = "Weight Apply"
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.StringProperty(default="add_scale", options={'HIDDEN'})
    resolved_action: bpy.props.StringProperty(default="", options={'HIDDEN'})
    intensity: bpy.props.FloatProperty(
        name="Amount",
        description=(
            "Applied intensity, in the same [0, 1] range as this action's own "
            "Add/Scale/Smooth/Sharpen slider (Smooth/Sharpen may read above "
            "1.0 -- see WeightApplyFeature._run_compound_passes())"
        ),
        default=0.0, min=0.0, soft_max=1.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (CoreFacade.is_system_activated() and CoreFacade.is_editing_weights() and
                obj is not None and obj.type == 'MESH')

    def _resolve(self, drag_value):
        """Map this gesture's signed drag value to a (real_action, intensity) pair."""
        if self.action == "add_scale":
            drag_value = max(-1.0, min(1.0, drag_value))
        positive, negative = _COMBINED_RESOLVERS[self.action]
        real_action, fn = positive if drag_value >= 0.0 else negative
        return real_action, fn(drag_value)

    def _ensure_baseline(self, context):
        """Lazily build the facade/feature/snapshot baseline. Set by `invoke()` for the modal
        instance."""
        if getattr(self, "_facade", None) is None:
            from ...core.facade import CoreFacade
            from .weight_apply_feature import WeightApplyFeature
            self._facade = CoreFacade(context)
            self._feature = WeightApplyFeature()
            with CoreFacade.profile_section("weight_apply.snapshot"):
                self._ctx = self._feature.snapshot_context(self._facade)
        return self._facade, self._feature, self._ctx

    def execute(self, context):
        """Apply `self.resolved_action` at `self.intensity` once."""
        facade, feature, ctx = self._ensure_baseline(context)
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
                result = feature.apply_action(real_action, facade, ctx, intensity)
        finally:
            context.scene.superskin_internal_transaction = False
        _probe_post_return(f"weight_apply.{real_action}.post_return", len(ctx["selected"]))
        if result.get("status") == "CANCELLED":
            return {'CANCELLED'}
        # Lets Shift+R (SUPERSKIN_OT_repeat_last_weight_apply) replay
        # whatever actually applied most recently.
        from .weight_apply_feature import get_prefs
        p = get_prefs()
        p.last_action = real_action
        p.last_intensity = intensity
        return {'FINISHED'}

    def draw(self, context):
        """Redo-panel body layout, matching ui.py's own button+bare-slider
        row look instead of Blender's default merged label+slider."""
        label = _ACTION_FIELD_LABELS.get(self.resolved_action, "Amount")
        split = self.layout.split(factor=0.3)
        split.label(text=label)
        split.prop(self, "intensity", text="", slider=True)

    def invoke(self, context, event):
        # A keymap that fails to forward `action` would otherwise fall back to
        # the property default (Add/Scale) even on the right mouse button.
        if not self.properties.is_property_set("action"):
            self.action = "smooth_sharpen" if event.type == 'RIGHTMOUSE' else "add_scale"
        self._ensure_baseline(context)

        self._trigger_type = event.type
        self._initial_x = event.mouse_x
        self._initial_y = event.mouse_y
        self._is_dragging = False
        self._drag_value = 0.0
        self._last_input_time = 0.0  # 0.0 so the first MOUSEMOVE is never throttled

        self._action_data_cache = {}
        self._rust_gateways = {}  # gateway tag -> RustWeightEngine, built on main thread
        self._compute_thread = None
        self._compute_result = None
        self._compute_error = None
        self._pending_action = None
        self._pending_intensity = None
        self._pending_action_data = None
        self._pending_key = None
        # Checked by the worker thread before it writes a result, so a
        # result finishing after RELEASE is discarded instead of picked up.
        self._gesture_finished = False

        # Grid pre-computation state. Cache keys are (real_action, idx, fine).
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

        for _real_action, _ in _COMBINED_RESOLVERS[self.action]:
            self._ensure_action_data(_real_action)
            self._ensure_rust_gateway(_real_action)

        self._facade.begin_live_stroke()
        self._live_stroke_open = True

        context.window.cursor_modal_set('NONE')
        self._timer = context.window_manager.event_timer_add(
            _GESTURE_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)

        if _ENABLE_PRECOMPUTE_WORKER:
            self._precompute_thread = threading.Thread(target=self._precompute_worker, daemon=True)
            self._precompute_thread.start()

        real_action, intensity = self._resolve(0.0)
        draw.show(self.action)
        draw.update(real_action, intensity, 0.0)
        return {'RUNNING_MODAL'}

    def _remove_timer(self, context):
        context.window_manager.event_timer_remove(self._timer)

    def _end_live_stroke(self):
        """Commit the live stroke once; on failure drop the per-stroke state."""
        if not getattr(self, "_live_stroke_open", False):
            return
        self._live_stroke_open = False
        try:
            self._facade.end_live_stroke()
        except Exception as exc:
            self._facade.drop_live_state(self._facade.get_obj().name)
            print(f"[SuperSkinPro] end_live_stroke failed: {exc}")

    def cancel(self, context):
        """Blender calls this when it force-terminates the modal outside RELEASE."""
        self._gesture_finished = True
        self._precompute_stop.set()
        try:
            self._remove_timer(context)
        except Exception:
            pass
        self._end_live_stroke()

    def _ensure_action_data(self, real_action):
        """Lazily build+cache prepared action data for the lifetime of this
        gesture. Main thread only."""
        if real_action not in self._action_data_cache:
            self._action_data_cache[real_action] = self._feature._prepare_action_data(
                real_action, self._facade, self._ctx,
            )
        return self._action_data_cache[real_action]

    def _ensure_rust_gateway(self, real_action):
        """Lazily build+cache a RustWeightEngine gateway. Main thread only —
        gateway construction reads license prefs via bpy.context."""
        tag = _ACTION_GATEWAY_TAGS[real_action]
        if tag not in self._rust_gateways:
            self._rust_gateways[tag] = CoreFacade.get_rust_gateway(tag)
        return {tag: self._rust_gateways[tag]}

    def _start_compute(self, real_action, intensity):
        """Spawn a background thread running the bpy-free compute for (real_action, intensity)."""
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
        """Which real action a signed grid index resolves to, mirroring
        `_resolve()`'s sign rule."""
        positive, negative = _COMBINED_RESOLVERS[self.action]
        return positive[0] if idx >= 0 else negative[0]

    def _intensity_for_key(self, real_action, idx, fine):
        drag_value = idx / _grid_n(fine)
        if self.action == "add_scale":
            drag_value = max(-1.0, min(1.0, drag_value))
        positive, negative = _COMBINED_RESOLVERS[self.action]
        fn = positive[1] if real_action == positive[0] else negative[1]
        return fn(drag_value)

    def _update_precompute_wanted(self, current_idx, fine):
        """Recompute which grid buckets the background worker should prioritize next. Pure
        bookkeeping."""
        targets = []

        def _add(idx):
            key = (self._real_action_for_idx(idx), idx, fine)
            if key not in self._compute_cache and key not in targets:
                targets.append(key)

        if fine:
            # Buckets spanning +/- _GRID_FINE_HALF_RANGE around the coarse
            # anchor locked in when Ctrl was pressed.
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
        """Persistent background worker: pops the highest-priority not-yet-cached target and
        runs the bpy-free compute for it."""
        while not self._precompute_stop.is_set():
            target = None
            with self._precompute_lock:
                for candidate in self._precompute_wanted:
                    if candidate not in self._compute_cache and candidate not in self._in_flight_keys:
                        target = candidate
                        self._in_flight_keys.add(candidate)
                        break
            if target is None:
                self._precompute_stop.wait(_PRECOMPUTE_POLL_INTERVAL)
                continue

            real_action, idx, fine = target
            intensity = self._intensity_for_key(real_action, idx, fine)
            action_data = self._action_data_cache[real_action]
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

            # RELEASE may have cleared self._compute_cache while this was in
            # flight (compound passes can take real time) — re-check before writing.
            if not self._precompute_stop.is_set() and not self._gesture_finished:
                self._compute_cache[target] = result
            with self._precompute_lock:
                self._in_flight_keys.discard(target)

    def _apply_result(self, context, real_action, action_data, intensity, result):
        """Write a (possibly cached) compute result to the mesh. Main thread only."""
        context.scene.superskin_internal_transaction = True
        try:
            write_result = self._feature._finish_write(
                real_action, self._facade, self._ctx, action_data, result,
            )
        finally:
            context.scene.superskin_internal_transaction = False
        if write_result.get("status") != "CANCELLED":
            mode_suffix = " (Slow)" if self._fine_mode else ""
            context.area.header_text_set(
                f"{_GESTURE_LABELS.get(real_action, real_action)}: {intensity:.2f}" + mode_suffix
            )

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
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
                # cursor_warp resets the mouse to _initial_x every frame, so
                # delta must accumulate onto the running value, not replace it.
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
                if _ENABLE_PRECOMPUTE_WORKER:
                    wanted_key = (self._fine_mode, current_idx)
                    if wanted_key != self._last_wanted_key:
                        self._update_precompute_wanted(current_idx, self._fine_mode)
                        self._last_wanted_key = wanted_key
                real_action = self._real_action_for_idx(current_idx)
                intensity = self._intensity_for_key(real_action, current_idx, self._fine_mode)

                # Pass the quantized value so the HUD jumps stop-to-stop,
                # matching what the TIMER branch actually applies.
                draw.update(
                    real_action, intensity, current_idx / _grid_n(self._fine_mode),
                    fine_mode=self._fine_mode,
                )

        elif event.type == 'TIMER':
            if self._precompute_errors:
                errors, self._precompute_errors = self._precompute_errors, []
                for target, exc in errors:
                    self._facade.debug_log(
                        "feature_domains",
                        f"weight_apply gesture precompute failed for {target!r}: {exc!r}",
                    )

            # Pick up a finished fallback compute, if any.
            if self._compute_thread is not None and not self._compute_thread.is_alive():
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
                    if pending_key == current_key:
                        self._apply_result(context, pending_action, pending_action_data, pending_intensity, result)
                        self._last_applied_key = pending_key

            # Apply the current grid bucket: a cache hit writes directly; a
            # miss falls back to the single-flight background compute.
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
                    elif self._compute_thread is None and key not in self._in_flight_keys:
                        self._pending_key = key
                        with self._precompute_lock:
                            self._in_flight_keys.add(key)
                        self._start_compute(real_action, intensity)

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
                self._end_live_stroke()
                return {'CANCELLED'}
            # Apply once more, synchronously, so the committed result
            # matches the last-seen mouse position regardless of timer timing.
            real_action, intensity = self._resolve(self._drag_value)
            self.resolved_action = real_action
            self.intensity = intensity
            try:
                return self.execute(context)
            finally:
                self._end_live_stroke()

        return {'RUNNING_MODAL'}


# ── Repeat last (Shift+R) ──────────────────────────────────────────────────

class SUPERSKIN_OT_repeat_last_weight_apply(bpy.types.Operator):
    """Replays the most recently applied Add/Scale/Smooth/Sharpen action at its exact same
    intensity."""
    bl_idname = "superskin.repeat_last_weight_apply"
    bl_label = "Repeat Last Weight Apply"
    # Blender suppresses a nested operator's own undo push, so 'UNDO' must be
    # set here (the outermost operator) or Shift+R applies with no undo step.
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not (CoreFacade.is_system_activated() and CoreFacade.is_editing_weights() and
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


# ── Registration ──────────────────────────────────────────────────────────

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
