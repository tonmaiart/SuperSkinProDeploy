"""Weight-apply operators — Add, Scale, Smooth, Sharpen.

The four actions have no standalone per-action operator classes of their
own -- both the panel row buttons (ui.py) and the Alt-drag gesture invoke
the SAME `superskin.weight_gesture` operator below, just with different
entry points (`EXEC_DEFAULT` from a button click vs `invoke()`/`modal()`
from a drag). This keeps exactly one code path, one redo-panel contract,
and one bl_label for every way of running Add/Scale/Smooth/Sharpen."""

import threading
import time

import bpy
from ...core.facade import CoreFacade
from . import draw

# Real action -> the Rust gateway tag `logic.py`'s apply_*() functions build
# via CoreFacade.get_rust_gateway(tag) when no pre-built one is passed in --
# used by SUPERSKIN_OT_weight_gesture._ensure_rust_gateway() to build/cache
# each gateway on the main thread before handing it to a background compute.
_ACTION_GATEWAY_TAGS = {
    "add": "add_logic", "scale": "scale_logic",
    "smooth": "smooth_logic", "sharpen": "sharpen_logic",
}


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
# Was 1/15 (before that 1/24, 1/30, and 1/60 again -- ~9ms measured compute
# in a 33ms budget, 27% duty cycle). Raised back to 1/60 now that the grid
# cache below (see `_GRID_COARSE_STEP`) makes most ticks a cache hit --
# `_finish_write()` alone, no Rust FFI call -- so a 60Hz tick rate no longer
# means 60 Rust calls/sec, only 60 cache lookups/sec on a hit. RELEASE always
# applies once more unconditionally regardless of this value, so the final
# committed result is unaffected either way, only the live-preview update
# rate during the drag changes. Lower back toward 1/15-1/24 if a cache-miss-
# heavy drag (e.g. a fast flick past the precomputed window) feels too
# expensive in practice.

# Throttles raw MOUSEMOVE handling itself (delta accumulation, cursor_warp,
# HUD redraw tag), separate from `_GESTURE_APPLY_INTERVAL` above (which only
# throttles the expensive apply+flatten call). MOUSEMOVE can fire far faster
# than either budget on a high-poll-rate mouse/tablet (100+ Hz), and every
# processed event still costs a real `cursor_warp()` (Blender/OS call, not
# free) plus a redraw tag even though it skips the Rust FFI path. Safe to
# skip an event entirely: `_drag_value`'s delta is measured against
# `_initial_x`, which only moves when `cursor_warp()` actually runs, so a
# skipped event simply lets the OS cursor drift a little further before the
# next processed event catches the FULL accumulated delta -- no motion is
# lost, only its updates are batched. The one edge case: on a very fast
# flick, the OS cursor could reach a screen edge and clip before the next
# processed event catches up, silently truncating that portion of the drag --
# rare in practice at normal drag speeds, but real.
# Was tied 1:1 to `_GESTURE_APPLY_INTERVAL`, then a fixed ~12Hz. Raised back
# to `1.0 / 60.0` for max input fidelity (MOUSEMOVE handling itself is cheap
# -- pure Python delta accumulation + cursor_warp + redraw tag, no FFI call --
# so running it faster than `_GESTURE_APPLY_INTERVAL` costs little and keeps
# HUD/cursor feedback smooth even while the expensive apply+flatten path
# below is throttled much more aggressively).
_GESTURE_INPUT_INTERVAL = 1.0 / 60.0

# Minimum `_drag_value` change (in the same signed units as the drag axis
# itself, e.g. add_scale's [-1, 1] range) required since the last applied
# value before a TIMER tick will re-run the expensive apply+flatten path.
# Previously ANY nonzero change (down to float epsilon) re-triggered a full
# apply -- a hand tremor mid-drag could burn a Rust FFI call for a change too
# small to see. This is a THIRD, independent throttle from
# `_GESTURE_APPLY_INTERVAL`/`_GESTURE_INPUT_INTERVAL` above: those two bound
# calls/sec regardless of how much the value moved; this one bounds calls by
# how much the value actually moved, regardless of how much time passed.
# RELEASE still always applies once more unconditionally (see modal()'s
# RELEASE branch) no matter how small the final leftover delta is, so the
# committed result is never affected by this -- only which intermediate
# preview ticks get skipped during the drag.
#
# Superseded by the quantized grid below: since the applied preview value is
# now always snapped to a grid bucket (0.1 coarse / 0.01 fine), "did the
# bucket change" is itself the gate -- a hand-tremor-sized raw delta that
# never crosses a bucket boundary already produces the same quantized key as
# before, so there is no separate deadzone constant to tune anymore.

# ── Lazy/progressive grid pre-computation (speculative background cache) ──
# Drag preview is snapped to a coarse 0.01 grid by default; holding Ctrl
# switches to a fine 0.001 sub-grid (same visual range, no HUD change other
# than the "(Slow)" text suffix -- see draw.py) for precise per-vertex/joint
# adjustments. A background thread speculatively pre-computes neighboring
# grid buckets (biased toward the observed drag direction, or toward the
# fine sub-grid window while Ctrl is held) so crossing a bucket boundary is
# normally a cache hit -- a direct `_finish_write()` call, no Rust FFI wait
# -- rather than a fresh compute.
_GRID_COARSE_STEP = 0.01
_GRID_FINE_STEP = 0.001
# Anchor +/- this many units, in fine mode -- derived from _GRID_COARSE_STEP
# (exactly one coarse step each side) rather than a second literal, so the
# two can never silently drift out of sync if either one is retuned later.
_GRID_FINE_HALF_RANGE = _GRID_COARSE_STEP
# Ctrl OVERRIDES (does not stack with) the scroll-wheel `_GESTURE_SLOW_DIVISORS`
# tier above while held -- releasing Ctrl restores whatever tier was already
# selected, untouched by having been ignored while Ctrl was down.
_FINE_MODE_DIVISOR = 5.0
_PRECOMPUTE_COARSE_LOOKAHEAD = 3  # extra steps beyond the immediate neighbors, direction-biased
_PRECOMPUTE_MAX_QUEUED = 5        # bounds worst-case background backlog
_PRECOMPUTE_POLL_INTERVAL = 0.01  # how often the idle worker re-checks for new work / the stop signal
# Disabled after repeated Smooth hangs were reported even after two rounds of
# `_rust_call_lock` serialization fixes (worker-vs-fallback, then worker-vs-
# execute()/RELEASE) -- the exact remaining trigger is not yet confirmed, so
# rather than ship a third unverified guess, the persistent background
# worker thread itself is switched off here: `invoke()` no longer starts it,
# and MOUSEMOVE no longer maintains its wanted-list (see both call sites
# below). Everything else this feature added stays fully active -- grid
# quantization/stepping, the Ctrl fine sub-grid, the stop-tick HUD, and the
# single-flight fallback compute/cache -- only the SPECULATIVE ahead-of-time
# precompute is off, so crossing an uncached bucket now costs one normal
# compute tick instead of being instant. This removes the only thing that
# ever let two threads call into the Rust engine at the same time (the
# single-flight fallback was always the sole caller before this feature
# existed). Flip back to `True` once the worker's own root cause is
# confirmed rather than merely no-longer-racing.
_ENABLE_PRECOMPUTE_WORKER = False


def _quantize(drag_value, fine):
    """Snap `drag_value` to the active grid and return an INT bucket index
    (not a rounded float) so cache keys never depend on float rounding
    behavior -- recover the actual grid value via `idx / _grid_n(fine)`.
    `fine=True` uses the `_GRID_FINE_STEP` sub-grid, `fine=False` the
    `_GRID_COARSE_STEP` coarse grid."""
    return round(drag_value * _grid_n(fine))


def _grid_n(fine):
    return round(1.0 / _GRID_FINE_STEP) if fine else round(1.0 / _GRID_COARSE_STEP)


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

    The live-preview value is snapped to a grid rather than tracking the raw
    drag continuously: a coarse 0.01 step by default, or a fine 0.001 step
    while Ctrl is held (`_quantize()`, `_GRID_COARSE_STEP`/`_GRID_FINE_STEP`)
    -- same visual range and HUD layout either way, see draw.py. Ctrl
    press/release is edge-detected in MOUSEMOVE and only updates bookkeeping
    (`self._fine_mode`/`self._fine_anchor_idx`) and which sensitivity divisor
    future deltas use (`_FINE_MODE_DIVISOR` OVERRIDES, not stacks with, the
    scroll-wheel tier above) -- it never touches `self._drag_value` or warps
    the cursor differently, so the transition is snap-free, same as the
    scroll tiers. A persistent background thread (`_precompute_worker()`,
    started at `invoke()`, stopped via `self._precompute_stop` at RELEASE)
    speculatively pre-computes neighboring grid buckets ahead of the
    observed drag direction (or the fine sub-grid window while Ctrl is
    held) into `self._compute_cache`, so crossing a bucket boundary is
    normally a cache hit -- a direct `_finish_write()` call, no Rust FFI
    wait -- rather than a fresh compute. A miss falls back to the single-
    flight compute below unchanged. This is unrelated to the earlier-
    mentioned, already-removed Ctrl-tap add_scale/smooth_sharpen mode
    toggle -- Ctrl here only ever affects grid precision/sensitivity within
    whichever pair this gesture instance already started in.

    MOUSEMOVE can fire far faster than the expensive apply+flatten path can
    usefully keep up with (100+ Hz on some mice/tablets), so tracking the
    drag value is decoupled from actually applying it: MOUSEMOVE only updates
    `self._drag_value` (cheap arithmetic), and a modal TIMER event
    (`_GESTURE_APPLY_INTERVAL`) is what actually triggers `_apply()`. This
    bounds apply+flatten calls/sec to a fixed budget regardless of raw input
    rate. RELEASE always applies once more unconditionally so the committed
    result matches the last-seen mouse position exactly, even if it lands
    between two timer ticks.

    MOUSEMOVE handling itself is ALSO throttled (`_GESTURE_INPUT_INTERVAL`,
    separate from `_GESTURE_APPLY_INTERVAL`) -- every processed event still
    costs a real `cursor_warp()` call and a redraw tag even when it skips the
    apply path, so on a high-poll-rate device this caps that cost too instead
    of only capping the Rust FFI side.

    The apply itself runs on a background thread (`_start_compute()`), not
    inline in the TIMER handler, so a slow compute (a large selection/heavy
    scene) never blocks the main thread from processing MOUSEMOVE/redraw --
    the cursor and HUD stay responsive regardless of how long the Rust call
    takes. Only `WeightApplyFeature._dispatch_compute()` (the pure FFI
    dispatch, zero `bpy` access -- see its docstring) runs off-thread;
    `_prepare_action_data()` (setup) and `_finish_write()` (the actual BMesh
    write) always run on the main thread, inline in `modal()`'s `TIMER`
    branch. Single-flight: at most one compute in flight at a time; a
    `_drag_value` change while one is running is picked up by the next
    `_start_compute()` once the current one's result is consumed -- no
    cancellation, nothing is ever lost, just batched. RELEASE (below) never
    waits on an in-flight compute -- it always re-runs its own synchronous,
    authoritative `execute()` call, and the abandoned background thread
    discards its own result via `self._gesture_finished` once it finishes
    (see RELEASE's handling) rather than writing anything after the fact.

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
        # REVERTED: an earlier attempt made this wait on `self._rust_call_lock`
        # so it wouldn't run concurrently with a still-in-flight background
        # compute at RELEASE. That was wrong -- it turned this into a
        # main-thread wait on a background thread's Rust call, and the ONLY
        # way a wait like that can become the unrecoverable, force-quit-only
        # freeze that got reported (reproducing at ANY intensity, seemingly
        # at random) is if that background call occasionally never returns
        # at all. Before that lock existed, such a stuck background thread
        # was invisible and harmless -- a daemon thread quietly stuck
        # forever in the background while RELEASE's OWN synchronous call
        # completed normally and the user never noticed. The lock made the
        # main thread wait on it instead. This is reverted to that original,
        # explicitly-documented behavior: RELEASE never waits on an in-flight
        # compute, full stop -- see this method's own docstring and the class
        # docstring's "Single-flight" paragraph. Whatever actually causes a
        # background call to sometimes never return is still worth finding,
        # but it must never be something the main thread can be made to
        # wait on.
        context.scene.superskin_internal_transaction = True
        try:
            result = feature.apply_action(real_action, facade, ctx, intensity)
        finally:
            context.scene.superskin_internal_transaction = False
        if result.get("status") == "CANCELLED":
            return {'CANCELLED'}
        # Tracked for SUPERSKIN_OT_repeat_last_weight_apply's Shift+R below --
        # every successful commit through this method (button click, gesture
        # RELEASE, or a redo-panel edit) updates this, so "repeat" always
        # replays whatever actually applied most recently, independent of
        # Blender's own native "Repeat Last" (also Shift+R by default, but
        # scoped to the last operator GLOBALLY -- anything else run in
        # between would hijack that, not this).
        from .weight_apply_feature import get_prefs
        p = get_prefs()
        p.last_action = real_action
        p.last_intensity = intensity
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
        self._slow_tier = 0
        self._last_input_time = 0.0  # 0.0 guarantees the first MOUSEMOVE is never throttled

        # Background-compute state -- see class docstring and _start_compute().
        # Single-flight gating is now `self._compute_thread is None` combined
        # with the grid key already being in `self._in_flight_keys` (see the
        # lazy grid pre-computation state below) rather than a raw-value
        # deadzone comparison -- `_last_started_value`/`_GESTURE_APPLY_DEADZONE`
        # are retired, superseded by grid quantization itself.
        self._action_data_cache = {}  # real_action -> _prepare_action_data() result, cached per gesture
        self._rust_gateways = {}  # gateway tag -> RustWeightEngine, built on main thread, cached per gesture
        self._compute_thread = None
        self._compute_result = None
        self._compute_error = None
        self._pending_action = None
        self._pending_intensity = None
        self._pending_action_data = None
        self._pending_key = None  # grid key the in-flight one-shot fallback compute was started for
        # Set True at RELEASE, checked by the worker thread right before it
        # would write self._compute_result/_compute_error -- discards a
        # result that finishes after the gesture already committed via
        # execute(), instead of ever writing it somewhere modal() might
        # (incorrectly) pick it up later.
        self._gesture_finished = False

        # ── Lazy grid pre-computation state -- see the module-level comment
        # above `_GRID_COARSE_STEP`. Cache keys are `(real_action, idx, fine)`
        # where `idx` is `_quantize()`'s int bucket -- see that function.
        self._fine_mode = False
        self._fine_anchor_idx = None   # coarse bucket the fine sub-grid is centered on
        self._last_coarse_idx = None   # previous tick's coarse bucket, for direction bias
        self._last_wanted_key = None   # (fine_mode, current_idx) last used to refresh the wanted-list
        self._last_applied_key = None  # key last sent to _finish_write -- the dedupe gate
        self._compute_cache = {}       # key -> _dispatch_compute() result
        self._in_flight_keys = set()   # keys already being computed (fallback OR precompute worker)
        self._precompute_wanted = []   # [(real_action, idx, fine), ...], nearest-first
        self._precompute_lock = threading.Lock()
        self._precompute_stop = threading.Event()
        # Serializes EVERY `_dispatch_compute()` call across both the one-shot
        # fallback thread (_start_compute()) and the persistent precompute
        # worker (_precompute_worker()) -- before this feature, only ONE
        # thread ever called into the Rust engine at a time for a given
        # gesture. Nothing in this codebase documents the compiled
        # `rust_logic` core (or `RustWeightEngine`) as safe for genuinely
        # CONCURRENT calls from two Python threads at once, so this lock
        # restores that original single-flight guarantee -- the precompute
        # worker still runs ahead of the user on its own thread (never
        # blocking the main thread), it just never overlaps its own Rust
        # call with the fallback path's.
        self._rust_call_lock = threading.Lock()
        # Failed precompute attempts are stashed here (list.append is GIL-safe
        # from the worker thread) rather than calling self._facade.debug_log()
        # directly off-thread -- same main-thread-only logging convention
        # _start_compute()'s _worker() already follows for self._compute_error.
        self._precompute_errors = []

        # Both real actions of this gesture's pair must have their action_data
        # /gateway built now, on the main thread, BEFORE the precompute worker
        # starts below -- it must only ever read these already-built dicts,
        # never call `_ensure_action_data`/`_ensure_rust_gateway` itself (both
        # touch `bpy`). A precompute target can resolve to either sign of the
        # pair from the very first tick (see the class docstring's INVOKE
        # step), so both must be ready upfront, not built lazily on first use.
        for _real_action, _ in _COMBINED_RESOLVERS[self.action]:
            self._ensure_action_data(_real_action)
            self._ensure_rust_gateway(_real_action)

        context.window.cursor_modal_set('NONE')
        self._timer = context.window_manager.event_timer_add(
            _GESTURE_APPLY_INTERVAL, window=context.window,
        )
        context.window_manager.modal_handler_add(self)

        # See `_ENABLE_PRECOMPUTE_WORKER`'s comment above -- currently off.
        if _ENABLE_PRECOMPUTE_WORKER:
            self._precompute_thread = threading.Thread(target=self._precompute_worker, daemon=True)
            self._precompute_thread.start()

        real_action, intensity = self._resolve(0.0)
        draw.show(self.action)
        draw.update(real_action, intensity, 0.0, 0)
        return {'RUNNING_MODAL'}

    def _remove_timer(self, context):
        context.window_manager.event_timer_remove(self._timer)

    def _ensure_action_data(self, real_action):
        """Lazily build+cache `WeightApplyFeature._prepare_action_data()`'s
        result for *real_action*, for the lifetime of this gesture. Main
        thread only (it touches `bpy` throughout -- see that method's
        docstring). Safe to reuse across every tick that resolves to the same
        `real_action`, including after `add_scale`/`smooth_sharpen` flips
        back to a `real_action` it already used earlier in the same drag --
        nothing it depends on (`self._ctx`) changes mid-gesture."""
        if real_action not in self._action_data_cache:
            self._action_data_cache[real_action] = self._feature._prepare_action_data(
                real_action, self._facade, self._ctx,
            )
        return self._action_data_cache[real_action]

    def _ensure_rust_gateway(self, real_action):
        """Lazily build+cache a `RustWeightEngine` gateway for *real_action*'s
        Rust FFI tag, for the lifetime of this gesture. Main thread only --
        `RustWeightEngine.__init__` reads license prefs via `bpy.context`
        (see `logic.py`'s `apply_*()` `rust=` parameter docstrings) -- so a
        background compute must never construct its own.

        Returns `{tag: gateway}`, the shape `_dispatch_compute()`'s
        `rust_gateways` parameter expects."""
        tag = _ACTION_GATEWAY_TAGS[real_action]
        if tag not in self._rust_gateways:
            self._rust_gateways[tag] = CoreFacade.get_rust_gateway(tag)
        return {tag: self._rust_gateways[tag]}

    def _start_compute(self, real_action, intensity):
        """Spawn a background thread running `WeightApplyFeature.
        _dispatch_compute()` (bpy-free -- see that method's docstring) for
        `(real_action, intensity)`, storing its result for a later `TIMER`
        tick to pick up and write. Caller (`modal()`'s `TIMER` branch) must
        only call this when `self._compute_thread` is `None` -- single-flight
        by construction, no cancellation logic needed."""
        action_data = self._ensure_action_data(real_action)
        rust_gateways = self._ensure_rust_gateway(real_action)
        self._pending_action = real_action
        self._pending_intensity = intensity
        self._pending_action_data = action_data

        def _worker():
            try:
                # See `self._rust_call_lock`'s comment in invoke() -- never
                # let this overlap with the precompute worker's own call.
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
        """Which real action a (signed) grid bucket index resolves to,
        mirroring `_resolve()`'s own `drag_value >= 0.0` sign rule -- used by
        the grid/cache machinery to assign a key's real_action without
        needing a raw (unquantized) drag_value on hand."""
        positive, negative = _COMBINED_RESOLVERS[self.action]
        return positive[0] if idx >= 0 else negative[0]

    def _intensity_for_key(self, real_action, idx, fine):
        """Resolve a grid cache key back into the `intensity` `_dispatch_compute()`
        needs, applying the same add_scale [-1, 1] clamp `_resolve()` applies
        to a raw drag_value."""
        drag_value = idx / _grid_n(fine)
        if self.action == "add_scale":
            drag_value = max(-1.0, min(1.0, drag_value))
        positive, negative = _COMBINED_RESOLVERS[self.action]
        fn = positive[1] if real_action == positive[0] else negative[1]
        return fn(drag_value)

    def _update_precompute_wanted(self, current_idx, fine):
        """Recompute the ordered list of grid buckets the background worker
        should prioritize next. Called from modal()'s MOUSEMOVE branch
        whenever the current bucket or fine/coarse mode changes -- pure
        background-work bookkeeping, never touches `self._drag_value` or
        cursor state (so it can never cause a visual snap)."""
        targets = []

        def _add(idx):
            key = (self._real_action_for_idx(idx), idx, fine)
            if key not in self._compute_cache and key not in targets:
                targets.append(key)

        if fine:
            # `2 * half_span + 1` buckets spanning +/- _GRID_FINE_HALF_RANGE
            # around the coarse anchor locked in at the moment Ctrl was
            # pressed (see modal()).
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
            # add_scale's drag_value is clamped to [-1, 1] -- a bucket index
            # outside +/- _grid_n(fine) is just a duplicate of the boundary.
            limit = _grid_n(fine)
            targets = [k for k in targets if -limit <= k[1] <= limit]

        with self._precompute_lock:
            self._precompute_wanted = targets[:_PRECOMPUTE_MAX_QUEUED]

    def _precompute_worker(self):
        """Persistent background worker -- started once at `invoke()`,
        stopped via `self._precompute_stop` at RELEASE (never `join()`ed;
        `daemon=True`, the same fire-and-forget style as `_start_compute()`'s
        one-shot thread). Loops popping the highest-priority not-yet-cached
        target from `self._precompute_wanted` and running the confirmed
        bpy-free, pure `WeightApplyFeature._dispatch_compute()` for it.

        This thread must NEVER call `_ensure_action_data()`/
        `_ensure_rust_gateway()` (both touch `bpy`, main-thread only) --
        `invoke()` guarantees both real actions of the active pair already
        have their action_data/gateway built before this thread starts, so
        this only ever does plain dict reads of already-built objects."""
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
                # See `self._rust_call_lock`'s comment in invoke() -- never
                # let this overlap with the one-shot fallback's own call.
                # Blocks this (background, daemon) thread until the lock is
                # free, never the main thread.
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

            # Re-check immediately before writing -- RELEASE's
            # self._compute_cache.clear() may already have run while
            # _dispatch_compute() was in flight (Smooth/Sharpen compound
            # passes can take real time); mirrors _start_compute()'s
            # existing late-result-discard pattern.
            if not self._precompute_stop.is_set() and not self._gesture_finished:
                self._compute_cache[target] = result
            with self._precompute_lock:
                self._in_flight_keys.discard(target)

    def _apply_result(self, context, real_action, action_data, intensity, result):
        """Write a (possibly cached) `_dispatch_compute()` result to the mesh
        -- main thread only. Wraps `_finish_write()` in the same transaction
        guard every other weight-mutating operator uses
        (`interface/utils/op_exec.py::run_domain_via_unified`) and refreshes
        the header-text readout. Shared by both the direct cache-hit path and
        the one-shot fallback's pickup, in modal()'s TIMER branch below."""
        context.scene.superskin_internal_transaction = True
        try:
            write_result = self._feature._finish_write(
                real_action, self._facade, self._ctx, action_data, result,
            )
        finally:
            context.scene.superskin_internal_transaction = False
        if write_result.get("status") != "CANCELLED":
            # Ctrl (fine mode) never changes the HUD layout -- it only ever
            # appends "(Slow)" to whatever text would already be shown,
            # exactly like the scroll-wheel slow tier below, and overrides
            # (does not stack with) that tier's own suffix while held.
            if self._fine_mode:
                mode_suffix = " (Slow)"
            elif self._slow_tier > 0:
                mode_suffix = f" [Slow x{_GESTURE_SLOW_DIVISORS[self._slow_tier]:.0f}]"
            else:
                mode_suffix = ""
            context.area.header_text_set(
                f"{_GESTURE_LABELS.get(real_action, real_action)}: {intensity:.2f}" + mode_suffix
            )

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            # Throttle raw input handling itself to _GESTURE_INPUT_INTERVAL --
            # see that constant's comment. A skipped event drops out here
            # before touching cursor_warp/drag_value/redraw at all; the OS
            # cursor simply keeps drifting from _initial_x until the next
            # processed event catches the full accumulated delta.
            now = time.perf_counter()
            if now - self._last_input_time < _GESTURE_INPUT_INTERVAL:
                return {'RUNNING_MODAL'}
            self._last_input_time = now

            # Ctrl toggles the fine sub-grid, edge-detected so the anchor is
            # (re)locked exactly once per press rather than every event while
            # held. This ONLY updates bookkeeping/sensitivity used for FUTURE
            # deltas below -- it never touches self._drag_value or warps the
            # cursor differently, which is what keeps the transition
            # snap-free, the same guarantee the scroll-wheel tiers already
            # rely on for their own sensitivity changes.
            ctrl_now = event.ctrl
            if ctrl_now != self._fine_mode:
                self._fine_mode = ctrl_now
                self._fine_anchor_idx = _quantize(self._drag_value, fine=False) if ctrl_now else None

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
                # Ctrl OVERRIDES (does not stack with) the scroll-wheel slow
                # tier -- see _FINE_MODE_DIVISOR's comment above.
                divisor = _FINE_MODE_DIVISOR if self._fine_mode else _GESTURE_SLOW_DIVISORS[self._slow_tier]
                sensitivity = _GESTURE_DRAG_SENSITIVITY / divisor
                new_value = self._drag_value + delta * sensitivity
                if self.action == "add_scale":
                    new_value = max(-1.0, min(1.0, new_value))
                self._drag_value = new_value
                context.window.cursor_warp(self._initial_x, self._initial_y)

                # Snap the live preview to the active grid -- coarse 0.05 by
                # default, fine 0.001 while Ctrl is held (see the module-level
                # comment above `_GRID_COARSE_STEP`). This IS the "steps
                # instead of drifting continuously" behavior; the cache/
                # precompute machinery below only makes crossing a bucket
                # boundary feel instant.
                current_idx = _quantize(self._drag_value, self._fine_mode)
                if _ENABLE_PRECOMPUTE_WORKER:
                    wanted_key = (self._fine_mode, current_idx)
                    if wanted_key != self._last_wanted_key:
                        self._update_precompute_wanted(current_idx, self._fine_mode)
                        self._last_wanted_key = wanted_key
                real_action = self._real_action_for_idx(current_idx)
                intensity = self._intensity_for_key(real_action, current_idx, self._fine_mode)

                # Live HUD update -- cheap (pure Python + tag_redraw, no FFI
                # call), so this tracks every raw MOUSEMOVE independent of
                # the throttled TIMER tick that actually applies the change.
                # Pass the QUANTIZED value (not the raw continuous
                # self._drag_value) so the HUD marker/fill-bar jump
                # stop-to-stop, matching the stop tick marks drawn along the
                # axis and exactly what modal()'s TIMER branch actually
                # applies -- see draw.update()'s docstring.
                draw.update(
                    real_action, intensity, current_idx / _grid_n(self._fine_mode), self._slow_tier,
                    fine_mode=self._fine_mode,
                )

        elif event.type == 'WHEELDOWNMOUSE':
            # Step to the next (slower) tier, capped at the last one. Stored
            # regardless of `self._fine_mode` -- Ctrl overrides which divisor
            # is USED (see MOUSEMOVE above), it never stops the tier itself
            # from being tracked, so releasing Ctrl resumes at this tier.
            self._slow_tier = min(self._slow_tier + 1, len(_GESTURE_SLOW_DIVISORS) - 1)
            if self._is_dragging:
                current_idx = _quantize(self._drag_value, self._fine_mode)
                real_action = self._real_action_for_idx(current_idx)
                intensity = self._intensity_for_key(real_action, current_idx, self._fine_mode)
                # Pass the QUANTIZED value (not the raw continuous
                # self._drag_value) so the HUD marker/fill-bar jump
                # stop-to-stop, matching the stop tick marks drawn along the
                # axis and exactly what modal()'s TIMER branch actually
                # applies -- see draw.update()'s docstring.
                draw.update(
                    real_action, intensity, current_idx / _grid_n(self._fine_mode), self._slow_tier,
                    fine_mode=self._fine_mode,
                )
            return {'RUNNING_MODAL'}

        elif event.type == 'WHEELUPMOUSE':
            # Step back toward normal speed, capped at 0.
            self._slow_tier = max(self._slow_tier - 1, 0)
            if self._is_dragging:
                current_idx = _quantize(self._drag_value, self._fine_mode)
                real_action = self._real_action_for_idx(current_idx)
                intensity = self._intensity_for_key(real_action, current_idx, self._fine_mode)
                # Pass the QUANTIZED value (not the raw continuous
                # self._drag_value) so the HUD marker/fill-bar jump
                # stop-to-stop, matching the stop tick marks drawn along the
                # axis and exactly what modal()'s TIMER branch actually
                # applies -- see draw.update()'s docstring.
                draw.update(
                    real_action, intensity, current_idx / _grid_n(self._fine_mode), self._slow_tier,
                    fine_mode=self._fine_mode,
                )
            return {'RUNNING_MODAL'}

        elif event.type == 'TIMER':
            # 0) Surface any precompute-worker failures on the main thread --
            # mirrors how the one-shot fallback's own error is likewise only
            # ever logged here, never from the background thread itself.
            if self._precompute_errors:
                errors, self._precompute_errors = self._precompute_errors, []
                for target, exc in errors:
                    self._facade.debug_log(
                        "feature_domains",
                        f"weight_apply gesture precompute failed for {target!r}: {exc!r}",
                    )

            # 1) Pick up a finished one-shot FALLBACK compute, if any (the
            # cache-miss path -- see step 3 below). Unconditional reset of
            # all attributes right after reading them -- on every path
            # (success, error, or a discarded/None result) -- so a failed/
            # discarded compute can never leave this operator stuck
            # believing one is still in flight (see class docstring).
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
                    # Always cache it -- even if stale by the time it lands,
                    # a later revisit of this exact bucket becomes a hit.
                    self._compute_cache[pending_key] = result
                    # Only apply it directly here if the drag hasn't moved
                    # past this bucket while the compute was in flight. If it
                    # has, step 2 below (driven by the CURRENT bucket, not
                    # this possibly-stale one) is the sole source of truth
                    # for what's visibly applied -- applying an old result
                    # unconditionally here could briefly regress the mesh
                    # backward to a value the user has already dragged past.
                    current_idx = _quantize(self._drag_value, self._fine_mode)
                    current_key = (self._real_action_for_idx(current_idx), current_idx, self._fine_mode)
                    if pending_key == current_key:
                        self._apply_result(context, pending_action, pending_action_data, pending_intensity, result)
                        self._last_applied_key = pending_key

            # 2) Apply the CURRENT grid bucket. A cache hit is the fast path
            # -- a direct _finish_write() call, main thread, no thread spawn
            # at all. A cache miss falls back to the existing single-flight
            # background compute unchanged (gated by `self._compute_thread
            # is None`, same as before -- grid quantization itself is what
            # replaces the old deadzone as the "did anything change" gate).
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
            # Set before anything else -- this is what a still-running
            # background compute's worker thread checks right before it
            # would write self._compute_result/_compute_error, so a result
            # that finishes after this point is quietly discarded instead of
            # ever being available for modal() to (incorrectly) pick up.
            # `daemon=True` on the thread means it can't block Blender
            # shutdown either way -- this flag is about correctness
            # (never applying a stale result on top of RELEASE's own
            # commit below), not about the thread itself blocking anything.
            self._gesture_finished = True
            # Signal the persistent precompute worker to stop -- never
            # `join()`ed (must not block the UI thread); the thread is
            # `daemon=True` and polls this event on its own short interval
            # (`_PRECOMPUTE_POLL_INTERVAL`), the same fire-and-forget style
            # as the one-shot fallback thread above. Clear the cache/wanted-
            # list immediately rather than deferring to instance GC --
            # Blender keeps the last-executed operator instance alive for
            # the redo panel, so this would otherwise persist until the next
            # gesture entirely replaces it.
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
                # Plain click, never dragged -- no single-click apply anymore,
                # so this is a pure no-op (no write, no undo step).
                return {'CANCELLED'}
            # Always apply once more unconditionally and SYNCHRONOUSLY (never
            # through the background-compute path above), even if
            # _drag_value already matches the last started/applied value --
            # guarantees the committed result matches the last-seen mouse
            # position exactly, regardless of where release lands relative to
            # the last timer tick or any in-flight (now-abandoned) compute.
            # Resolved here (not read back from a stored signed drag_value)
            # so the RNA `intensity` property gets the actual natural-range
            # value applied -- that's what Blender's redo panel reads/writes
            # on a later re-execute, and what draw() below labels.
            real_action, intensity = self._resolve(self._drag_value)
            self.resolved_action = real_action
            self.intensity = intensity
            return self.execute(context)

        return {'RUNNING_MODAL'}


# ── Repeat last (Shift+R) ──────────────────────────────────────────────────

class SUPERSKIN_OT_repeat_last_weight_apply(bpy.types.Operator):
    """Replays the most recently applied Add/Scale/Smooth/Sharpen action
    (`SSPrefWeightApply.last_action`/`last_intensity`, updated by
    `SUPERSKIN_OT_weight_gesture.execute()` on every successful commit --
    button click, gesture RELEASE, or a redo-panel edit) at its exact same
    intensity.

    Tracked independently of Blender's own native "Repeat Last" (also
    Shift+R by default, in the global Window keymap) because that repeats
    whichever operator ran last GLOBALLY -- a selection change, a different
    tool, anything else run in between would hijack it, replaying THAT
    instead of the last weight apply. This operator's own `poll()` only
    succeeds while actively editing weights AND something has actually been
    applied yet this session, so outside that context Shift+R still falls
    through to Blender's native binding unaffected (a failed `poll()` never
    consumes the key event)."""
    bl_idname = "superskin.repeat_last_weight_apply"
    bl_label = "Repeat Last Weight Apply"
    # Required even though this operator only ever delegates to a nested
    # bpy.ops.superskin.weight_gesture() call -- Blender suppresses a nested
    # operator's own undo push (wm->op_undo_depth > 0 while already inside
    # this execute()) and instead pushes exactly one step for the OUTERMOST
    # operator, gated on ITS bl_options. Without 'UNDO' here, Shift+R applied
    # the weight change but registered zero undo steps for it.
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
        # Re-invokes the existing, already-thin `weight_gesture` operator
        # directly (`EXEC_DEFAULT` -- a plain call, not a drag) rather than
        # duplicating its dispatch logic here -- this operator is a pure
        # thin shell. The replayed call pushes its OWN undo/redo-panel entry
        # exactly like a fresh button click would, and updates
        # `last_action`/`last_intensity` again itself (harmless -- keeps
        # tracking "most recently applied", which a repeat legitimately is).
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
