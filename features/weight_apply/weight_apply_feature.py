"""WeightApplyFeature — Unified Component Architecture implementation for the weight_apply domain.

Collapses the old WeightApplyDomain (action dispatch) and prefs.py (PropertyGroup,
draw, persistence) into a single UnifiedFeatureExtension subclass.

Owns:
  - SSPrefWeightApply PropertyGroup (registered on WindowManager)
  - WeightApplyPreferencesService (stateless accessor)
  - Action dispatch: add, scale, smooth, sharpen
  - UI layout: draw_section()
  - JSON persistence: populate() / serialize_into()
"""

import bpy
import os
import time

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from ...core_subsystems.debug_logging import DebugLogService

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")

# Smooth/Sharpen's Rust formula (`new = current + (avg - current) * intensity`
# for smooth, `new = current + (current - avg) * intensity` for sharpen) is a
# single linear extrapolation, not an iterative diffusion -- at intensity > 1.0
# it overshoots past the immediate 1-ring neighbor average of a FIXED baseline
# rather than reproducing what repeatedly pressing the button at intensity 1.0
# N times would (each such press re-reads the *already-smoothed* neighbor
# average from the previous press, genuinely propagating influence outward
# hop by hop). `_run_compound_passes()` below closes that gap for the gesture
# (whose drag value can exceed 1.0, unlike the button, which is clamped to
# [0, 1] by SSPrefWeightApply's FloatProperty) by actually chaining that many
# intensity=1.0 passes -- capped at `_COMPOUND_MAX_PASSES` to bound the
# per-tick Rust-call cost of a single gesture frame; intensity beyond the cap
# falls back to one final extrapolated pass for the leftover amount on top of
# the capped result, same as the old single-shot formula would have produced
# for that remainder alone.
_COMPOUND_MAX_PASSES = 5


# ==============================================================================
# Property Groups
# ==============================================================================

def _on_intensity_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


class SSPrefWeightApply(bpy.types.PropertyGroup):
    """Weight-apply intensity settings (per-machine)."""
    add_val: bpy.props.FloatProperty(
        name="Add", min=0.0, max=1.0, default=0.61,
        update=_on_intensity_changed,
    )
    scale_val: bpy.props.FloatProperty(
        name="Scale", min=0.0, max=1.0, default=0.61,
        update=_on_intensity_changed,
    )
    smooth_val: bpy.props.FloatProperty(
        name="Smooth", min=0.0, max=1.0, default=0.61,
        update=_on_intensity_changed,
    )
    sharpen_val: bpy.props.FloatProperty(
        name="Sharpen", min=0.0, max=1.0, default=0.61,
        update=_on_intensity_changed,
    )
    smooth_affected_only: bpy.props.BoolProperty(
        name="Smooth Affected Only",
        description="Limit smoothing to vertices that already have weight > 0",
        default=False,
        update=_on_intensity_changed,
    )
    smooth_across_surface: bpy.props.BoolProperty(
        name="Smooth Across Surface",
        description=(
            "Expand the smoothing neighborhood using surface (geodesic) distance "
            "instead of raw vertex adjacency, so results stay consistent across "
            "areas with uneven topology density"
        ),
        default=False,
        update=_on_intensity_changed,
    )


# ==============================================================================
# Preferences accessor (replaces the old get_prefs() in prefs.py)
# ==============================================================================

class WeightApplyPreferencesService:
    """Stateless accessor for weight-apply prefs — consumed by logic.py and ui.py."""

    @staticmethod
    def get_prefs() -> "SSPrefWeightApply":
        return bpy.context.window_manager.superskin_weight_apply_prefs


# Backward-compat alias so `from .weight_apply_feature import get_prefs` works
get_prefs = WeightApplyPreferencesService.get_prefs


# ==============================================================================
# WeightApplyFeature — UnifiedFeatureExtension
# ==============================================================================

class WeightApplyFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Apply domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "weight_apply"
    actions = ["add", "scale", "smooth", "sharpen"]
    section_title = "Apply"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH
    priority = 1
    keymaps = [
        {"key": "Alt+LMB", "label": "Add / Scale Gesture", "mode": "Hold"},
        {"key": "Alt+RMB", "label": "Smooth / Sharpen Gesture", "mode": "Hold"},
        # Weight Brush is a WorkSpaceTool (brush/brush_tool.py), not a
        # keymap -- "select the tool from the Toolbar" has no keyboard
        # shortcut to show here, so it deliberately has no HUD entry.
    ]
    expanded_by_default = True
    locked_expanded = True
    
    # ── Action dispatch ───────────────────────────────────────────────────

    def snapshot_context(self, core_facade: CoreFacade) -> dict:
        """Read everything an apply action needs to compute from, once.

        Used both by the single-shot `execute()` path and by the gesture
        modal operator (`ops.py:SUPERSKIN_OT_weight_gesture`). The gesture
        operator re-runs `apply_action()` on every mouse-move at a changing
        intensity, and must always compute from this same fixed baseline —
        recomputing from `core_facade` fresh each move would apply on top of
        the previous preview's result and compound instead of preview.
        """
        is_mask = core_facade.is_mask_context()
        active_vg_id = core_facade.get_active_vg_id()
        layer_str = core_facade.read_active_layer()
        bone_to_id, id_to_bone = core_facade.get_unified_mapping()
        layer_int = {
            v_idx: {bone_to_id[b]: w for b, w in weights.items() if b in bone_to_id}
            for v_idx, weights in layer_str.items()
        }
        return {
            "is_mask": is_mask,
            "active_vg_id": active_vg_id,
            "layer_int": layer_int,
            "id_to_bone": id_to_bone,
            "mask_dict": core_facade.get_active_mask_dict(),
            "locks_id": core_facade.get_locks_by_id(),
            "selected": core_facade.get_selected_verts(),
        }

    def _run_compound_passes(self, intensity, pass_fn, layer_int_for_rust, mask_dict_for_rust):
        """Decompose `intensity` into `full_passes = min(int(intensity),
        _COMPOUND_MAX_PASSES)` chained intensity=1.0 calls to `pass_fn`, each
        fed the PREVIOUS pass's output as its input (genuine iterative
        diffusion, matching repeated button presses), plus one final pass at
        the leftover `remainder = intensity - full_passes` on top of that --
        skipped only when `full_passes > 0` and `remainder` is negligible, so
        at least one pass always runs (covers `intensity == 0.0`, a no-op
        pass, matching what a single call at intensity 0 would already do).

        For `intensity <= 1.0` (the button path is always in this range;
        SSPrefWeightApply's Add/Scale/Smooth/Sharpen FloatProperties are all
        clamped `min=0.0, max=1.0`) this reduces to exactly one call at
        `intensity` -- byte-for-byte the same single Rust call `apply_action()`
        made before this existed. Only gesture drag values above 1.0 (allowed
        since `smooth_sharpen`'s clamp was removed -- see this domain's
        README) actually chain multiple passes.

        `pass_fn(layer_dict, mask_dict, step_intensity) -> (layer_out, mask_out)`
        closes over whichever action-specific fixed arguments (`neighbors`,
        `locks_id`, `active_vg_id`, etc.) `apply_action()` already resolved.
        Rust's `apply_smooth` and `apply_sharpen` both return the *complete*
        `dirty_verts`-scoped dict they were given (not a sparse true diff --
        see `apply_action()`'s own docstring on `res_layer_diff` naming), so
        each pass's output is already the correct, self-consistent input for
        the next one -- no re-merging against the original baseline needed
        between passes."""
        full_passes = min(int(intensity), _COMPOUND_MAX_PASSES)
        remainder = intensity - full_passes

        layer_out, mask_out = layer_int_for_rust, mask_dict_for_rust
        ran_any = False
        for _ in range(full_passes):
            layer_out, mask_out = pass_fn(layer_out, mask_out, 1.0)
            ran_any = True
        if remainder > 1e-9 or not ran_any:
            layer_out, mask_out = pass_fn(layer_out, mask_out, remainder)
        return layer_out, mask_out

    def apply_action(self, action: str, core_facade: CoreFacade, ctx: dict,
                     intensity: float, *, affected_only: bool = None) -> dict:
        """Compute `action` from the `ctx` baseline (see `snapshot_context()`)
        at `intensity`, then write the result. Never mutates `ctx`, so it is
        safe to call repeatedly from the same snapshot (gesture drag preview)
        without compounding."""
        from .logic import (
            apply_add, apply_scale, apply_smooth, apply_sharpen,
            build_surface_neighbors, expand_sharpen_dirty_verts,
            compute_nearest_bones,
        )

        p = get_prefs()
        is_mask = ctx["is_mask"]
        active_vg_id = ctx["active_vg_id"]
        id_to_bone = ctx["id_to_bone"]
        selected = ctx["selected"]
        locks_id = ctx["locks_id"]

        # dirty_verts: every vertex this tick's write could possibly touch --
        # always a superset of `selected`, widened by whichever neighbor set
        # is about to be read below (Rust's `neighbors` argument for Smooth,
        # or Sharpen's own multi-hop diffusion reach). Never a separately
        # re-derived approximation, so it can't under-cover what gets read.
        # Passed to the write calls below to let the core flatten pipeline
        # skip full-mesh BMesh scans on this hot path.
        dirty_verts = set(selected)
        neighbors = None
        if action == "smooth":
            if p.smooth_across_surface:
                neighbors = build_surface_neighbors(core_facade, selected)
            else:
                neighbors = core_facade.get_cached_mesh_neighbors()
            for v in selected:
                dirty_verts.update(neighbors.get(v, ()))
        elif action == "sharpen":
            # Sharpen's write set is still exactly `selected`, but Rust's
            # diffusion (`apply_sharpen()` -> `rust_sharpen_full_vector`)
            # reads a few hops further out to build its low-pass contrast
            # reference -- dirty_verts must cover those reads too, or they'd
            # silently default to zero weight below (see
            # expand_sharpen_dirty_verts()'s docstring).
            dirty_verts |= expand_sharpen_dirty_verts(core_facade, selected)

        # Only feed the vertices this call could possibly touch (dirty_verts)
        # -- every rust_*_logic function's write set is exactly `selected`,
        # and its only reads outside `selected` are neighbor/diffusion
        # lookups (Smooth, Sharpen) that dirty_verts already guarantees are
        # present. This keeps the Rust FFI marshaling cost proportional to
        # brush size, not total painted-vertex count on the mesh.
        #
        # CRITICAL: the return values are named `res_layer_diff`/`res_mask_diff`
        # (never `res_layer`/`res_mask`) specifically so it's structurally
        # obvious they are NOT a complete active-layer snapshot -- merge them
        # into `full_layer_int` below and use ONLY that downstream. Passing
        # the small diff anywhere a complete dict is expected reproduces the
        # exact "untouched vertex's weight-paint color goes black" bug
        # already hit and fixed once this session (see git history / prior
        # session notes on write_layer_to_temp_vgs_bm's "absence = clear"
        # semantics, and flatten_to_mesh_edit()'s active_layer_override
        # contract, and ss_layer_N's direct-persistence path -- all three
        # require the complete layer, not a subset).
        layer_int_for_rust = {v: dict(ctx["layer_int"].get(v, {})) for v in dirty_verts}
        mask_dict_for_rust = {v: ctx["mask_dict"][v] for v in dirty_verts if v in ctx["mask_dict"]}
        mask_dict = dict(ctx["mask_dict"])

        # Profiling: gated by the same lazy-guard pattern as debug logging --
        # time.perf_counter() calls themselves are cheap, but keep this
        # opt-in (enable the "feature_domains" debug category in Preferences
        # to see per-tick ms in the console) rather than always-on.
        _profile = DebugLogService.is_enabled("feature_domains")
        _t0 = time.perf_counter() if _profile else None

        if action == "add":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_add(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                intensity, locks_id,
                core_facade.get_active_layer_index(), is_mask,
            )

        elif action == "scale":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            # Scale's Rust redistribution falls back to spreading freed
            # weight across every *unlocked* bone in the dict passed here
            # only when the vertex being processed has no other bone of its
            # own carrying weight to redistribute proportionally against
            # (see rust_scale_logic's own comment). `locks_id` comes from
            # the unified mapping, which is every non-temp vertex group on
            # the mesh -- NOT the same set as the Deform Bones list (see
            # CoreFacade.get_deform_bone_ids()). Scoping to that set keeps
            # this fallback from ever inventing weight on a vertex group
            # that's invisible in the Deform Bones list (e.g. left over
            # from an earlier rig version, or a control/helper bone never
            # flagged use_deform).
            deform_ids = core_facade.get_deform_bone_ids()
            locks_scoped = {b_id: locked for b_id, locked in locks_id.items()
                            if b_id in deform_ids}
            # Spatial targeting: restrict redistribution to whichever
            # unlocked, non-active, deform bone(s) sit closest (by distance
            # to their head-tail segment) to each selected vertex, instead
            # of spreading across every bone that happens to carry weight
            # there -- see logic.compute_nearest_bones()'s docstring and
            # this domain's README ("Scale's Redistribution Target Scope").
            nearest_bone_ids = {}
            if not is_mask:
                candidate_ids = {b_id for b_id in deform_ids
                                 if not locks_scoped.get(b_id) and b_id != active_vg_id}
                nearest_bone_ids = compute_nearest_bones(
                    core_facade, selected, candidate_ids, id_to_bone,
                    layer_int=ctx["layer_int"],
                )
            res_layer_diff, res_mask_diff = apply_scale(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                intensity, locks_scoped, is_mask, nearest_bone_ids,
            )

        elif action == "smooth":
            affected = p.smooth_affected_only if affected_only is None else affected_only
            res_layer_diff, res_mask_diff = self._run_compound_passes(
                intensity,
                lambda layer_in, mask_in, step_intensity: apply_smooth(
                    layer_in, mask_in, selected, neighbors,
                    step_intensity, locks_id, affected, is_mask,
                ),
                layer_int_for_rust, mask_dict_for_rust,
            )

        elif action == "sharpen":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            # Sharpen operates on every unlocked bone at each vertex (not
            # just active_vg_id) -- see apply_sharpen()'s docstring.
            # active_vg_id itself is only used above as the existing
            # "is there a weight-painting context at all" gate.
            res_layer_diff, res_mask_diff = self._run_compound_passes(
                intensity,
                lambda layer_in, mask_in, step_intensity: apply_sharpen(
                    core_facade, layer_in, mask_in, selected,
                    locks_id, step_intensity, is_mask,
                ),
                layer_int_for_rust, mask_dict_for_rust,
            )

        else:
            return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

        if _profile:
            _t_rust = time.perf_counter()

        # Merge the small Rust diff into a full copy of the baseline -- this
        # (not res_layer_diff) is what every downstream consumer below uses.
        full_layer_int = {v: dict(w) for v, w in ctx["layer_int"].items()}
        full_layer_int.update(res_layer_diff)

        if _profile:
            _t_merge = time.perf_counter()

        if is_mask:
            # Merge the Rust-modified vertices back into the full baseline mask so
            # non-selected vertices keep their existing mask values.  Rust returns
            # only the selected vertices in res_mask_diff; writing it directly
            # would clear every other vertex's mask to 0.
            full_mask = dict(ctx["mask_dict"])
            full_mask.update(res_mask_diff)
            # Use the ctrl escape with is_mask_mode=True to bypass the bone
            # normalization loop, which would otherwise prune unselected vertices.
            ctrl = core_facade.get_ctrl()
            ctrl._write_active_layer_string(full_layer_int, id_to_bone,
                                            full_mask, is_mask_mode=True,
                                            dirty_verts=dirty_verts)
            core_facade.finish(color_only=True, dirty_verts=dirty_verts)
        elif core_facade.get_obj().mode == 'EDIT':
            # write_active_layer_from_calc() takes Rust's int-keyed output
            # directly, skipping the int->string->int round-trip
            # write_active_layer() would otherwise do, and already flattens +
            # redraws inline for EDIT mode -- no separate finish() call needed
            # (calling one here would flatten a second time).
            # mask_override=mask_dict: non-mask actions never modify the mask
            # (Rust only mutates it when is_mask_mode=True), so this tick's
            # mask_dict is still the correct, current one -- passing it lets
            # the compositor skip re-reading it from the BMesh too.
            core_facade.write_active_layer_from_calc(
                full_layer_int, id_to_bone, dirty_verts=dirty_verts, mask_override=mask_dict,
            )
        else:
            # write_active_layer_from_calc()'s Object-Mode branch only saves
            # to storage and does not flatten/redraw, so Object Mode keeps the
            # slower string round-trip path (which calls finish() internally).
            res_layer_str = {
                v_idx: {id_to_bone[b]: w for b, w in weights.items() if b in id_to_bone}
                for v_idx, weights in full_layer_int.items()
            }
            core_facade.write_active_layer(res_layer_str, color_only=True, dirty_verts=dirty_verts)

        if _profile:
            _t_write = time.perf_counter()
            DebugLogService.log(
                "feature_domains",
                f"apply_action({action!r}) dirty_verts={len(dirty_verts)}: "
                f"rust={1000 * (_t_rust - _t0):.2f}ms "
                f"merge={1000 * (_t_merge - _t_rust):.2f}ms "
                f"write={1000 * (_t_write - _t_merge):.2f}ms "
                f"total={1000 * (_t_write - _t0):.2f}ms",
            )

        # `layer_int`/`mask_dict` are additive on top of the pre-existing
        # `{"status": ...}` contract -- every current caller (weight_gesture's
        # execute(), run_domain_via_unified, SUPERSKIN_OT_execute_action) only
        # reads `result.get("status")`, so this is backward compatible. Added
        # so a caller that dispatches several apply_action() calls in a row
        # against the SAME ctx (the Weight Brush modal in `brush/brush_ops.py`,
        # one dab per tick) can fold each dab's actual written result back
        # into its own local ctx and let the next dab read the just-painted
        # state -- real accumulation within one stroke -- without re-running
        # the full `snapshot_context()` (a BMesh read) every tick.
        return {
            "status": "FINISHED",
            "layer_int": full_layer_int,
            "mask_dict": full_mask if is_mask else mask_dict,
        }

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        p = get_prefs()
        core_facade.debug_log(
            "feature_domains",
            f"weight_apply.execute() action={action!r}",
        )

        ctx = self.snapshot_context(core_facade)
        intensity = {
            "add": p.add_val, "scale": p.scale_val,
            "smooth": p.smooth_val, "sharpen": p.sharpen_val,
        }.get(action, 0.0)
        result = self.apply_action(action, core_facade, ctx, intensity)

        core_facade.debug_log("feature_domains", f"weight_apply.execute() action={action!r} done")
        return result

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the full Weight Apply section: Add, Scale, Smooth, Sharpen controls."""
        from .ui import draw_section
        draw_section(layout)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        p = get_prefs()
        p.add_val = float(data.get("add_val", 0.61))
        p.scale_val = float(data.get("scale_val", 0.61))
        p.smooth_val = float(data.get("smooth_val", 0.61))
        p.sharpen_val = float(data.get("sharpen_val", 0.61))
        p.smooth_affected_only = bool(data.get("smooth_affected_only", False))
        p.smooth_across_surface = bool(data.get("smooth_across_surface", False))

        # Weight Brush settings nest under this same domain's "brush" key
        # (see brush/brush_ops.py) rather than getting a second top-level
        # UnifiedFeatureExtension registration -- same-package import, not a
        # cross-feature one. Skipped while BRUSH_ENABLED is False
        # (brush/__init__.py) -- SSPrefWeightBrush isn't even registered on
        # WindowManager then, so get_brush_prefs() would raise.
        from .brush import BRUSH_ENABLED
        if BRUSH_ENABLED:
            from .brush.brush_ops import populate_prefs as _populate_brush
            _populate_brush(data.get("brush", {}))

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        p = get_prefs()
        full_dict["weight_apply"] = {
            "add_val": p.add_val,
            "scale_val": p.scale_val,
            "smooth_val": p.smooth_val,
            "sharpen_val": p.sharpen_val,
            "smooth_affected_only": p.smooth_affected_only,
            "smooth_across_surface": p.smooth_across_surface,
        }
        # See the matching guard in populate() above.
        from .brush import BRUSH_ENABLED
        if BRUSH_ENABLED:
            from .brush.brush_ops import serialize_prefs as _serialize_brush
            full_dict["weight_apply"]["brush"] = _serialize_brush()


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register PropertyGroups on WindowManager and the extension with UnifiedRegistry."""
    bpy.utils.register_class(SSPrefWeightApply)
    bpy.types.WindowManager.superskin_weight_apply_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightApply, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(WeightApplyFeature())


def unregister():
    """Unregister PropertyGroups and the extension."""
    UnifiedRegistry.unregister("weight_apply")
    try:
        del bpy.types.WindowManager.superskin_weight_apply_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightApply)
