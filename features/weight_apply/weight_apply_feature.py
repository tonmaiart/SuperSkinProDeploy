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

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

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


def _decompose_intensity_passes(intensity):
    """Mirrors `_run_compound_passes()`'s intensity decomposition (`full_passes`
    copies of `1.0` plus a trailing remainder) but returns a plain list instead
    of driving a Python-side loop -- Smooth's Rust call now runs every compound
    pass internally in one shot (see `apply_smooth()`'s docstring), so this list
    is handed straight to Rust instead of being consumed by
    `_run_compound_passes()`. Sharpen still goes through `_run_compound_passes()`
    itself and does not use this."""
    full_passes = min(int(intensity), _COMPOUND_MAX_PASSES)
    remainder = intensity - full_passes
    passes = [1.0] * full_passes
    if remainder > 1e-9 or not passes:
        passes.append(remainder)
    return passes


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

    # Session-only bookkeeping for `ops.py`'s Shift+R "repeat last weight
    # apply" -- NOT a user-facing setting (no `update=`, deliberately never
    # read/written by populate()/serialize_into() below, so it never round-
    # trips through default_config.json). Piggybacks on this PropertyGroup's
    # existing `options={'SKIP_SAVE'}` WindowManager registration (see
    # register() below) rather than introducing a second PointerProperty
    # just for two fields -- same convention `profiler`'s session-only
    # Enabled checkbox already uses on its own domain's prefs.
    last_action: bpy.props.StringProperty(default="")
    last_intensity: bpy.props.FloatProperty(default=0.0)


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
            # Mutable, gesture-scoped memoization slot -- the one deliberate
            # exception to "apply_action() never mutates ctx" (see that
            # method's own docstring). Scale's compute_nearest_bones() is a
            # pure function of data already frozen in this same ctx
            # (selected/locks_id/active_vg_id/layer_int), so its result is
            # identical on every apply_action("scale", ...) call sharing this
            # ctx -- including every tick of an Alt+LMB drag (up to 60Hz,
            # ops.py's SUPERSKIN_OT_weight_gesture._apply()), which previously
            # recomputed it (a full get_vertex_coordinates() mesh read plus a
            # spatial search) from scratch on every single tick. Caching it
            # here cannot go stale mid-gesture, because nothing that feeds it
            # ever changes without a fresh snapshot_context() call first.
            "_nearest_bones_cache": {},
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
        Rust's `apply_sharpen` returns the *complete* `dirty_verts`-scoped
        dict it was given (not a sparse true diff -- see `apply_action()`'s
        own docstring on `res_layer_diff` naming), so each pass's output is
        already the correct, self-consistent input for the next one -- no
        re-merging against the original baseline needed between passes.

        **Sharpen only.** Smooth used to go through this same method, but its
        compound-pass loop now runs entirely inside a single Rust call (see
        `apply_smooth()`'s docstring and `_decompose_intensity_passes()`
        above) -- the old per-pass FFI call shape meant the whole layer/mask
        dict round-tripped out to Python and back in as the next pass's
        input, which dominated profiling for a large selection with several
        compound passes."""
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

    def _prepare_action_data(self, action: str, core_facade: CoreFacade, ctx: dict,
                             *, affected_only: bool = None) -> dict:
        """Phase 1 of `apply_action()`: everything that depends on `action`/
        `ctx` but NOT on `intensity` -- dirty_verts, coords/neighbors/density
        factors, Scale's nearest-bone targeting, and the dirty_verts-scoped
        `layer_int_for_rust`/`mask_dict_for_rust` slices. Identical on every
        tick of a gesture drag sharing the same `(action, ctx)` pair (only
        `intensity` changes tick to tick), so a caller ticking repeatedly
        (the gesture modal) can compute this once per `action` and reuse it --
        see `ops.py:SUPERSKIN_OT_weight_gesture._ensure_action_data()`.

        Touches `bpy` throughout (`core_facade.*` calls) -- MAIN THREAD ONLY.
        Never mutates `ctx`'s WEIGHT/SELECTION baseline, same exception as
        `apply_action()`'s own docstring (Scale's `_nearest_bones_cache`)."""
        from .logic import (
            compute_density_factors, expand_sharpen_dirty_verts, compute_nearest_bones,
            compute_chain_bone_ids,
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
        coords = None
        density_factors = None
        smooth_coords = None
        smooth_neighbors = None
        if action == "smooth":
            neighbors = core_facade.get_cached_mesh_neighbors()
            # Fetched once here and reused both by compute_density_factors()
            # below and by every compound pass's apply_smooth() call further
            # down -- previously each of those fetched its own copy of
            # get_vertex_coordinates() (a full mesh-vertex-count read), so a
            # single gesture tick with compound passes active could repeat
            # that read up to _COMPOUND_MAX_PASSES + 2 times over.
            coords = core_facade.get_vertex_coordinates()
            density_factors = compute_density_factors(coords, neighbors, selected)
            for v in selected:
                dirty_verts.update(neighbors.get(v, ()))
            # rust_smooth_logic's `coords`/`neighbors` params only ever get
            # read for a vertex in `selected` (self) or one of its listed
            # neighbors -- exactly `dirty_verts`. core_facade's full-mesh
            # coords/neighbors above exist for compute_density_factors();
            # scoping a separate copy here keeps the FFI marshal cost (and
            # every compound pass re-marshals it -- up to
            # _COMPOUND_MAX_PASSES + 1 times per tick) proportional to the
            # brush's footprint instead of total mesh size, which was a flat
            # per-call cost floor showing up in profiling regardless of
            # dirty_verts size.
            smooth_coords = {v: coords[v] for v in dirty_verts}
            smooth_neighbors = {v: neighbors[v] for v in selected if v in neighbors}
        elif action == "sharpen":
            # Sharpen's write set is still exactly `selected`, but Rust's
            # diffusion (`apply_sharpen()` -> `rust_sharpen_full_vector`)
            # reads a few hops further out to build its low-pass contrast
            # reference -- dirty_verts must cover those reads too, or they'd
            # silently default to zero weight below (see
            # expand_sharpen_dirty_verts()'s docstring).
            dirty_verts |= expand_sharpen_dirty_verts(core_facade, selected)
            # Fetched once here and reused by every compound pass's
            # apply_sharpen() call further down -- same redundant-refetch fix
            # already applied to Smooth's coords (see the comment in the
            # `smooth` branch below): apply_sharpen() previously fetched its
            # own get_vertex_coordinates()/get_cached_mesh_neighbors() copy
            # on every one of up to _COMPOUND_MAX_PASSES + 1 passes per tick.
            coords = core_facade.get_vertex_coordinates()
            neighbors = core_facade.get_cached_mesh_neighbors()

        # Only feed the vertices this call could possibly touch (dirty_verts)
        # -- every rust_*_logic function's write set is exactly `selected`,
        # and its only reads outside `selected` are neighbor/diffusion
        # lookups (Smooth, Sharpen) that dirty_verts already guarantees are
        # present. This keeps the Rust FFI marshaling cost proportional to
        # brush size, not total painted-vertex count on the mesh.
        layer_int_for_rust = {v: dict(ctx["layer_int"].get(v, {})) for v in dirty_verts}
        mask_dict_for_rust = {v: ctx["mask_dict"][v] for v in dirty_verts if v in ctx["mask_dict"]}
        mask_dict = dict(ctx["mask_dict"])

        active_layer_index = None
        locks_scoped = None
        nearest_bone_ids = {}
        if action == "add":
            active_layer_index = core_facade.get_active_layer_index()
        elif action == "scale":
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
            if not is_mask:
                # Cached per-ctx (see snapshot_context()'s docstring) --
                # every input below (deform_ids, locks_scoped, active_vg_id,
                # ctx["layer_int"]) is derived from data already frozen in
                # ctx, so this is byte-identical on every call sharing the
                # same ctx. Avoids re-running the spatial nearest-bone
                # search (including a full get_vertex_coordinates() mesh
                # read) on every tick of a Scale drag.
                nb_cache = ctx.get("_nearest_bones_cache")
                if nb_cache is not None and "nearest_bone_ids" in nb_cache:
                    nearest_bone_ids = nb_cache["nearest_bone_ids"]
                else:
                    # Hierarchy scoping: further restrict candidates to
                    # whichever bones sit on the SAME position-reconstructed
                    # chain as the active bone (its ancestors/descendants --
                    # see compute_chain_bone_ids()'s docstring) so a vertex
                    # near two unrelated branches (e.g. two separate fingers
                    # that happen to touch in space) can't redistribute
                    # across into the wrong one. `None` means "no chain data
                    # available" (no armature, orphan bone, etc.) -- never
                    # narrows the candidate pool in that case, same
                    # never-block-Scale-outright philosophy as the topology
                    # corroboration step inside compute_nearest_bones().
                    chain_ids = compute_chain_bone_ids(
                        core_facade, active_vg_id, id_to_bone, deform_ids,
                    )
                    candidate_ids = {b_id for b_id in deform_ids
                                     if not locks_scoped.get(b_id) and b_id != active_vg_id
                                     and (chain_ids is None or b_id in chain_ids)}
                    nearest_bone_ids = compute_nearest_bones(
                        core_facade, selected, candidate_ids, id_to_bone,
                        layer_int=ctx["layer_int"],
                    )
                    if nb_cache is not None:
                        nb_cache["nearest_bone_ids"] = nearest_bone_ids

        affected = p.smooth_affected_only if affected_only is None else affected_only

        return {
            "is_mask": is_mask,
            "active_vg_id": active_vg_id,
            "id_to_bone": id_to_bone,
            "selected": selected,
            "locks_id": locks_id,
            "dirty_verts": dirty_verts,
            "coords": coords,
            "neighbors": neighbors,
            "density_factors": density_factors,
            "smooth_coords": smooth_coords,
            "smooth_neighbors": smooth_neighbors,
            "layer_int_for_rust": layer_int_for_rust,
            "mask_dict_for_rust": mask_dict_for_rust,
            "mask_dict": mask_dict,
            "active_layer_index": active_layer_index,
            "locks_scoped": locks_scoped,
            "nearest_bone_ids": nearest_bone_ids,
            "affected": affected,
        }

    def _dispatch_compute(self, action: str, action_data: dict, intensity: float,
                          *, rust_gateways: dict = None) -> dict:
        """Phase 2 of `apply_action()`: the actual Rust FFI dispatch --
        `{"status": "OK", "res_layer_diff": ..., "res_mask_diff": ...}` on
        success, or the pre-existing `{"status": "CANCELLED", "message": ...}`
        contract (no active bone, unknown action).

        **Zero `bpy` access** -- every value it reads comes from `action_data`
        (built by `_prepare_action_data()` on the main thread) or `intensity`
        (a plain float). `CoreFacade.layer_to_coo()`/`coo_to_layer()` (inside
        `apply_add`/`apply_scale`/`apply_smooth`/`apply_sharpen`) are pure
        `array.array` manipulation, and `core_facade.profile_section()` never
        touches `bpy.context` (`ProfilerService`'s INV-3). This is what's
        safe to run on a background thread -- see
        `ops.py:SUPERSKIN_OT_weight_gesture._start_compute()`. The one thing
        that does need `bpy.context` is constructing a `RustWeightEngine`
        (license prefs), which is why callers must pass already-built
        gateways via `rust_gateways` (`{tag: engine}`) rather than let
        `apply_add()` etc. construct their own -- see those functions'
        `rust=` parameter docstrings.

        `core_facade` here is used *only* for `profile_section()` (confirmed
        bpy-free), never for anything that reads/writes bpy/BMesh state."""
        from .logic import apply_add, apply_scale, apply_smooth, apply_sharpen

        rust_gateways = rust_gateways or {}
        is_mask = action_data["is_mask"]
        active_vg_id = action_data["active_vg_id"]
        selected = action_data["selected"]
        locks_id = action_data["locks_id"]
        layer_int_for_rust = action_data["layer_int_for_rust"]
        mask_dict_for_rust = action_data["mask_dict_for_rust"]

        if action == "add":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_add(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                intensity, locks_id,
                action_data["active_layer_index"], is_mask,
                rust=rust_gateways.get("add_logic"),
            )

        elif action == "scale":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_scale(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                intensity, action_data["locks_scoped"], is_mask, action_data["nearest_bone_ids"],
                rust=rust_gateways.get("scale_logic"),
            )

        elif action == "smooth":
            # Every compound pass now runs inside one Rust call (see
            # apply_smooth()'s docstring) instead of going through
            # _run_compound_passes()'s Python-side per-pass loop -- that
            # used to mean one full FFI round trip of the entire
            # layer/mask dict per pass.
            res_layer_diff, res_mask_diff = apply_smooth(
                layer_int_for_rust, mask_dict_for_rust, selected,
                action_data["smooth_coords"], action_data["smooth_neighbors"],
                _decompose_intensity_passes(intensity), locks_id, action_data["affected"], is_mask,
                action_data["density_factors"],
                rust=rust_gateways.get("smooth_logic"),
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
                    layer_in, mask_in, selected, action_data["coords"], action_data["neighbors"],
                    locks_id, step_intensity, is_mask,
                    rust=rust_gateways.get("sharpen_logic"),
                ),
                layer_int_for_rust, mask_dict_for_rust,
            )

        else:
            return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

        return {"status": "OK", "res_layer_diff": res_layer_diff, "res_mask_diff": res_mask_diff}

    def _finish_write(self, action: str, core_facade: CoreFacade, ctx: dict,
                      action_data: dict, compute_result: dict) -> dict:
        """Phase 3 of `apply_action()`: merge the Rust diff into the full
        baseline and write it to BMesh/storage. MAIN THREAD ONLY (BMesh
        writes, `core_facade.get_obj()`, `finish()`).

        `compute_result` is `_dispatch_compute()`'s return value -- a
        CANCELLED status short-circuits and is returned as-is, nothing to
        write."""
        if compute_result["status"] == "CANCELLED":
            return compute_result

        # CRITICAL: `res_layer_diff`/`res_mask_diff` are NOT a complete
        # active-layer snapshot -- merge them into `full_layer_int` below and
        # use ONLY that downstream. Passing the small diff anywhere a
        # complete dict is expected reproduces the exact "untouched vertex's
        # weight-paint color goes black" bug already hit and fixed once this
        # session (see git history / prior session notes on
        # write_layer_to_temp_vgs_bm's "absence = clear" semantics, and
        # flatten_to_mesh_edit()'s active_layer_override contract, and
        # ss_layer_N's direct-persistence path -- all three require the
        # complete layer, not a subset).
        res_layer_diff = compute_result["res_layer_diff"]
        res_mask_diff = compute_result["res_mask_diff"]

        is_mask = action_data["is_mask"]
        id_to_bone = action_data["id_to_bone"]
        dirty_verts = action_data["dirty_verts"]
        mask_dict = action_data["mask_dict"]

        with core_facade.profile_section(f"weight_apply.{action}.merge", size=len(dirty_verts)):
            # Merge the small Rust diff into a full copy of the baseline -- this
            # (not res_layer_diff) is what every downstream consumer below uses.
            full_layer_int = {v: dict(w) for v, w in ctx["layer_int"].items()}
            full_layer_int.update(res_layer_diff)

        with core_facade.profile_section(f"weight_apply.{action}.write", size=len(dirty_verts)):
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

    def apply_action(self, action: str, core_facade: CoreFacade, ctx: dict,
                     intensity: float, *, affected_only: bool = None) -> dict:
        """Compute `action` from the `ctx` baseline (see `snapshot_context()`)
        at `intensity`, then write the result. Never mutates the WEIGHT/
        SELECTION baseline data in `ctx` -- the one exception is Scale
        lazily filling `ctx["_nearest_bones_cache"]` on its first call from a
        given ctx and reusing it on every later call (see
        `snapshot_context()`'s docstring for why that specific value is safe
        to memoize this way) -- so it is otherwise still safe to call
        repeatedly from the same snapshot (gesture drag preview)
        without compounding.

        A thin, fully-synchronous composition of `_prepare_action_data()` /
        `_dispatch_compute()` / `_finish_write()` -- every existing caller
        (this method itself, unchanged from the caller's perspective) still
        gets byte-identical behavior. The gesture modal
        (`ops.py:SUPERSKIN_OT_weight_gesture`) is the one caller that invokes
        the three phases separately, running `_dispatch_compute()` on a
        background thread -- see that class's `_start_compute()`."""
        action_data = self._prepare_action_data(action, core_facade, ctx, affected_only=affected_only)
        compute_result = self._dispatch_compute(action, action_data, intensity)
        return self._finish_write(action, core_facade, ctx, action_data, compute_result)

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
