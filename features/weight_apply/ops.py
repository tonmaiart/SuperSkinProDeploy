"""Weight-apply operators — Add, Scale, Smooth, Sharpen.

The four actions have no standalone per-action operator classes of their
own -- both the panel row buttons (ui.py) and the Alt-drag gesture invoke
the SAME `superskin.weight_gesture` operator below, just with different
entry points (`EXEC_DEFAULT` from a button click vs `invoke()`/`modal()`
from a drag). This keeps exactly one code path, one redo-panel contract,
and one bl_label for every way of running Add/Scale/Smooth/Sharpen."""

import bpy
from ...core.facade import CoreFacade
from . import draw


# ── Gesture shortcut (Alt-click Add/Scale + Smooth/Sharpen, hold-only) ────
# Also the operator every panel row button in ui.py invokes directly for a
# plain click -- see the module docstring above.

_GESTURE_LABELS = {
    "add": "Add Weight",
    "scale": "Scale Weight",
    "smooth": "Smooth Weight",
    "sharpen": "Sharpen Weight",
}

# Short field labels for the redo-panel body (draw() below) -- matches the
# short button text ui.py's panel rows already use ("Add"/"Scale"/...)
# rather than the fuller _GESTURE_LABELS names, since the panel's own
# title bar ("Weight Apply") already establishes the general context.
_ACTION_FIELD_LABELS = {
    "add": "Add",
    "scale": "Scale",
    "smooth": "Smooth",
    "sharpen": "Sharpen",
}

# Which fixed gesture pair (`action` property) each real action belongs to
# -- ui.py's panel-button rows set this on the operator instance so
# `self.action` stays consistent even though a plain button click always
# supplies `resolved_action` directly and never actually calls _resolve().
ACTION_TO_GESTURE_PAIR = {
    "add": "add_scale",
    "scale": "add_scale",
    "smooth": "smooth_sharpen",
    "sharpen": "smooth_sharpen",
}

_GESTURE_DRAG_THRESHOLD = 4  # pixels before a click becomes a drag (matches bone_picker's overlay-size gesture)
_GESTURE_DRAG_SENSITIVITY = 1.0 / 300.0  # 300px horizontal drag spans 0 -> +-1.0
# Scroll-down during the gesture steps through these divisors in order
# (index 0 = normal speed); scroll-up steps back down toward index 0.
_GESTURE_SLOW_DIVISORS = (1.0, 3.0, 6.0)
_GESTURE_APPLY_INTERVAL = 1.0 / 60.0  # cap expensive apply+flatten ticks to ~60Hz, independent of raw MOUSEMOVE rate
# Was 1/30 (~9ms measured compute in a 33ms budget, 27% duty cycle) -- after
# the dirty_verts/caching optimizations landed, there's enough headroom to
# double the tick rate (~54% duty cycle at 60Hz) for a smoother feel without
# meaningfully raising CPU cost. Lower this back toward 1/30-1/20 if a much
# larger/heavier scene pushes per-tick compute closer to the new budget.

# Each combined gesture's `action` property spans a signed [-1.0, 1.0] drag
# value starting at 0.0. The sign picks which of its two real domain actions
# runs; the magnitude becomes that action's intensity:
#   add_scale:       [0, 1] -> add(v)         [-1, 0] -> scale(1.0 + v)
#   smooth_sharpen:  [0, 1] -> smooth(v)      [-1, 0] -> sharpen(-v)
# So dragging left from 0 ramps scale's intensity down from 1.0 (no change)
# to 0.0 (fully scaled to zero) at -1.0, and ramps sharpen's intensity up
# from 0.0 (no change) to 1.0 at -1.0 -- both read as "0 is neutral" in
# their own direction.
_COMBINED_RESOLVERS = {
    "add_scale": (("add", lambda v: v), ("scale", lambda v: 1.0 + v)),
    "smooth_sharpen": (("smooth", lambda v: v), ("sharpen", lambda v: -v)),
}


class SUPERSKIN_OT_weight_gesture(bpy.types.Operator):
    """Runs Add/Scale/Smooth/Sharpen. Two entry points share this one
    class and one code path:

      1. A plain click on one of ui.py's panel row buttons -- Blender
         calls `execute()` directly (`EXEC_DEFAULT`), with `resolved_action`
         and `intensity` pre-set by the button itself (see ui.py's
         `_draw_op_row()`) to that action's current preference slider
         value. No drag, no modal.
      2. The Alt-drag gesture below -- `invoke()`/`modal()` live-preview a
         hold-and-drag on a single signed drag axis starting at 0.0:

      Alt+LMB (`add_scale`):        positive -> Add,     negative -> Scale
      Alt+Shift+LMB (`smooth_sharpen`): positive -> Smooth, negative -> Sharpen

    Each keymap entry starts the gesture directly in its own fixed mode --
    there is no mid-gesture mode switch (a previous revision had a Ctrl-tap
    toggle between the two; that has been removed, so `self.action` is the
    mode for the whole gesture, not just its starting point).

    There is no plain-click apply -- a click that never crosses the drag
    threshold does nothing at all (0.0 is neutral for both sides, so this
    also matches what a 0-intensity apply would have done, but skips the
    write/undo-step entirely instead of committing a no-op). Holding and
    dragging horizontally live-previews the resolved action/intensity on the
    mesh; releasing commits the final value as a single write (one undo
    step). There is no mid-gesture cancel by design -- the only way back is
    Blender's native Ctrl+Z, never an ESC/cancel branch here.

    `add_scale`'s drag value stays clamped to [-1.0, 1.0] (Add/Scale's
    intensity is meaningful only in that range), but `smooth_sharpen`'s is
    deliberately left unclamped so repeated/larger drags can smooth or
    sharpen further than a single clamped pass would allow.

    Scrolling during the drag steps through `_GESTURE_SLOW_DIVISORS`:
    scroll down moves to the next (slower) tier -- normal -> 3x slower ->
    6x slower, capped there -- scroll up steps back toward normal. This
    replaced a previous Shift-tap on/off toggle with a genuine two-level
    slow-down, addressable in either direction.

    MOUSEMOVE can fire far faster than the expensive apply+flatten path can
    usefully keep up with (100+ Hz on some mice/tablets), so tracking the
    drag value is decoupled from actually applying it: MOUSEMOVE only updates
    `self._drag_value` (cheap arithmetic), and a modal TIMER event
    (`_GESTURE_APPLY_INTERVAL`) is what actually triggers `_apply()`. This
    bounds apply+flatten calls/sec to a fixed budget regardless of raw input
    rate. RELEASE always applies once more unconditionally so the committed
    result matches the last-seen mouse position exactly, even if it lands
    between two timer ticks.

    RELEASE resolves the final signed drag value into a (`resolved_action`,
    `intensity`) pair -- `intensity` already in the SAME [0, 1]-ish range as
    that action's own Add/Scale/Smooth/Sharpen slider, not the raw signed
    axis -- writes both into RNA properties, and calls `self.execute()`
    (rather than duplicating the apply logic inline), which is what makes
    this operator show Blender's native "Adjust Last Operation" panel
    (bottom-left, F9) afterward: dragging that panel's "Amount" slider
    re-applies `resolved_action` at the edited `intensity` from a fresh
    baseline, exactly like any other native redo-able tool -- but, unlike
    the old raw-signed-value scheme, can no longer cross back into this
    gesture's OTHER action by dragging past a sign boundary; only the
    magnitude is redo-editable now, matching every other action's own
    operator.

    The redo panel's TITLE BAR always reads "Weight Apply" (`bl_label`,
    static) regardless of which real action committed or which of the two
    entry points above triggered it -- it deliberately does NOT say "Add
    Weight"/"Scale Weight"/etc. per action. An earlier revision tried
    swapping `bl_label` per-instance via a deferred unregister/register
    cycle so the title would read e.g. "Scale Weight", but this corrupted
    the operator-history entry the redo panel itself reads from, so the
    panel silently failed to appear at all (worst on the very first
    gesture, or any time the label actually changed switching between an
    add_scale gesture's two actions -- exactly the cases that exercised
    the swap). Reverted; see git history. `bl_label` is baked into the RNA
    struct once at class-registration time and genuinely cannot be changed
    per-instance without breaking the redo panel that depends on it, so
    this project's normal "never edit bl_label" guardrail stands for this
    operator going forward -- `bl_label` was renamed once, statically, from
    the old "Weight Gesture" to "Weight Apply" (there is no longer a
    conceptually separate "gesture" operator; the drag is just one more way
    to trigger the same Weight Apply action), but is not touched again at
    runtime. `draw()` below covers the per-action naming instead, safely,
    inside the panel body rather than its title bar.

    See `_ensure_baseline()` for why `execute()` must work both right after
    the modal ends (same instance, baseline already snapshotted at invoke)
    and when redo constructs a brand-new instance with no modal history.
    """
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
        """Lazily build the facade/feature/snapshot baseline `_apply()` and
        `execute()` compute from. Already set by `invoke()` for the instance
        that ran the modal gesture -- this only matters for the *separate*
        operator instance Blender's redo panel constructs to call
        `execute()` directly (no invoke/modal), which needs its own fresh
        snapshot taken from the current (by then already undone-back-to-
        pre-gesture) context."""
        if getattr(self, "_facade", None) is None:
            from ...core.facade import CoreFacade
            from .weight_apply_feature import WeightApplyFeature
            self._facade = CoreFacade(context)
            self._feature = WeightApplyFeature()
            self._ctx = self._feature.snapshot_context(self._facade)
        return self._facade, self._feature, self._ctx

    def execute(self, context):
        """Apply `self.resolved_action` at `self.intensity` once. Called
        directly for the modal's own final RELEASE commit, and by Blender's
        redo panel (with `intensity` possibly tweaked) after it undoes the
        previous commit -- both paths must reproduce identically from a
        fresh baseline, so this never reads modal-only state like
        `self._is_dragging`.

        `resolved_action` is fixed at whatever RELEASE resolved it to
        (see modal()) and is not re-derived here -- unlike the old signed
        `drag_value` scheme, the redo panel can no longer cross back over
        into this gesture's OTHER action (e.g. Scale -> Add) by dragging
        past a sign boundary; only the magnitude is redo-editable, matching
        how every other action's own operator/panel slider behaves."""
        facade, feature, ctx = self._ensure_baseline(context)
        real_action = self.resolved_action
        intensity = self.intensity
        if not real_action:
            # Cold-start fallback (execute() called with resolved_action
            # never set by a RELEASE) -- resolve a neutral 0.0 drag through
            # this instance's own `action` pair, same as the old scheme's
            # drag_value=0.0 default (add_scale -> Add, smooth_sharpen ->
            # Smooth, both at intensity 0.0, i.e. a harmless no-op).
            real_action, intensity = self._resolve(0.0)
        # add/scale's formulas are only meaningful in [0, 1] (mirrors the
        # hard clamp _resolve() applies to the live drag axis for
        # add_scale) -- smooth/sharpen stay unclamped so compounding passes
        # above 1.0 keep working from the redo panel too.
        if real_action in ("add", "scale"):
            intensity = max(0.0, min(1.0, intensity))
        else:
            intensity = max(0.0, intensity)
        context.scene.superskin_internal_transaction = True
        try:
            result = feature.apply_action(real_action, facade, ctx, intensity)
        finally:
            context.scene.superskin_internal_transaction = False
        if result.get("status") == "CANCELLED":
            return {'CANCELLED'}
        return {'FINISHED'}

    def draw(self, context):
        """Custom layout for the redo panel body (title bar stays the
        static "Weight Apply" -- see the class docstring on why that can't
        vary per-action). Split into a plain text label + a separate bare
        slider -- matching ui.py's own panel-row look (`_draw_op_row()`:
        button/label on the left, bare slider on the right) instead of
        Blender's default single-widget property layout, where the label
        text is drawn merged into the slider control itself.

        `layout.split()` + `layout.label()` + `layout.prop(..., text="")`
        are pure per-draw layout calls, not RNA registration changes, so
        unlike the title bar this is safe to vary per-instance: reading
        `self.resolved_action` here just relabels the same already-
        registered `intensity` slider as "Add"/"Scale"/"Smooth"/"Sharpen"
        so the field itself names the action that will actually re-apply
        when dragged."""
        label = _ACTION_FIELD_LABELS.get(self.resolved_action, "Amount")
        split = self.layout.split(factor=0.3)
        split.label(text=label)
        split.prop(self, "intensity", text="", slider=True)

    def invoke(self, context, event):
        self._ensure_baseline(context)

        self._trigger_type = event.type
        self._initial_x = event.mouse_x
        self._initial_y = event.mouse_y
        self._is_dragging = False
        self._drag_value = 0.0
        self._last_applied_value = None
        self._slow_tier = 0

        context.window.cursor_modal_set('NONE')
        self._timer = context.window_manager.event_timer_add(
            _GESTURE_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)

        real_action, intensity = self._resolve(0.0)
        draw.show(self.action)
        draw.update(real_action, intensity, 0.0, 0)
        return {'RUNNING_MODAL'}

    def _remove_timer(self, context):
        context.window_manager.event_timer_remove(self._timer)

    def _apply(self, context, drag_value):
        """Resolve `drag_value` to a real action/intensity and run one
        apply_action() pass, wrapped in the same superskin_internal_transaction
        guard every other weight-mutating operator uses (see
        interface/utils/op_exec.py:run_domain_via_unified).

        No active-bone gate here -- `apply_action()` itself already no-ops
        gracefully (returns a CANCELLED-status dict, no write) for
        add/scale/sharpen with no active bone and no mask context; Smooth
        never needed one. Gating at invoke() would have blocked the whole
        gesture whenever it started in add_scale mode with nothing selected."""
        real_action, intensity = self._resolve(drag_value)
        context.scene.superskin_internal_transaction = True
        try:
            self._feature.apply_action(real_action, self._facade, self._ctx, intensity)
        finally:
            context.scene.superskin_internal_transaction = False
        return real_action, intensity

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            delta = event.mouse_x - self._initial_x
            if not self._is_dragging and abs(delta) > _GESTURE_DRAG_THRESHOLD:
                self._is_dragging = True
            if self._is_dragging:
                # `cursor_warp` below resets the mouse back to _initial_x every
                # frame (infinite-drag), so `delta` here is only the small
                # movement since the last warp -- it must be ACCUMULATED onto
                # the running value, not used as an absolute offset each time
                # (that was the bug: recomputing `delta * sensitivity` fresh
                # every frame capped the value at whatever a single event's
                # movement could reach, ~0.03-0.04).
                # NOTE: only the value is tracked here -- the expensive
                # apply+flatten call is throttled to the TIMER tick below, not
                # run on every MOUSEMOVE (see class docstring).
                sensitivity = _GESTURE_DRAG_SENSITIVITY / _GESTURE_SLOW_DIVISORS[self._slow_tier]
                new_value = self._drag_value + delta * sensitivity
                if self.action == "add_scale":
                    new_value = max(-1.0, min(1.0, new_value))
                self._drag_value = new_value
                context.window.cursor_warp(self._initial_x, self._initial_y)
                # Live HUD update -- cheap (pure Python + tag_redraw, no FFI
                # call), so this tracks every raw MOUSEMOVE independent of
                # the throttled TIMER tick that actually applies the change.
                real_action, intensity = self._resolve(self._drag_value)
                draw.update(real_action, intensity, self._drag_value, self._slow_tier)

        elif event.type == 'WHEELDOWNMOUSE':
            # Step to the next (slower) tier, capped at the last one.
            self._slow_tier = min(self._slow_tier + 1, len(_GESTURE_SLOW_DIVISORS) - 1)
            if self._is_dragging:
                real_action, intensity = self._resolve(self._drag_value)
                draw.update(real_action, intensity, self._drag_value, self._slow_tier)
            return {'RUNNING_MODAL'}

        elif event.type == 'WHEELUPMOUSE':
            # Step back toward normal speed, capped at 0.
            self._slow_tier = max(self._slow_tier - 1, 0)
            if self._is_dragging:
                real_action, intensity = self._resolve(self._drag_value)
                draw.update(real_action, intensity, self._drag_value, self._slow_tier)
            return {'RUNNING_MODAL'}

        elif event.type == 'TIMER':
            if self._is_dragging and self._drag_value != self._last_applied_value:
                real_action, intensity = self._apply(context, self._drag_value)
                self._last_applied_value = self._drag_value
                slow_suffix = (
                    f" [Slow x{_GESTURE_SLOW_DIVISORS[self._slow_tier]:.0f}]"
                    if self._slow_tier > 0 else ""
                )
                context.area.header_text_set(
                    f"{_GESTURE_LABELS.get(real_action, real_action)}: {intensity:.2f}"
                    + slow_suffix
                )

        elif event.type == self._trigger_type and event.value == 'RELEASE':
            self._remove_timer(context)
            context.window.cursor_modal_restore()
            context.area.header_text_set(None)
            draw.hide()
            if not self._is_dragging:
                # Plain click, never dragged -- no single-click apply anymore,
                # so this is a pure no-op (no write, no undo step).
                return {'CANCELLED'}
            # Always apply once more unconditionally, even if _drag_value
            # already matches _last_applied_value -- guarantees the committed
            # result matches the last-seen mouse position exactly, regardless
            # of where release lands relative to the last timer tick.
            # Resolved here (not read back from a stored signed drag_value)
            # so the RNA `intensity` property gets the actual natural-range
            # value applied -- that's what Blender's redo panel reads/writes
            # on a later re-execute, and what draw() below labels.
            real_action, intensity = self._resolve(self._drag_value)
            self.resolved_action = real_action
            self.intensity = intensity
            return self.execute(context)

        return {'RUNNING_MODAL'}


# ── Registration ──────────────────────────────────────────────────────────

_classes = (
    SUPERSKIN_OT_weight_gesture,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
