"""WeightApplyFeature — Unified Component Architecture implementation for the weight_apply domain."""

import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")

_COMPOUND_MAX_PASSES = 5


def _decompose_intensity_passes(intensity):
    """Split intensity into `full_passes` copies of 1.0 plus a trailing remainder, as a list
    handed straight to Rust (apply_smooth runs every compound pass internally)."""
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


def _on_smooth_affected_only_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()
    from . import draw
    draw.sync_affected_only_hud()

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
        update=_on_smooth_affected_only_changed,
    )

    vertex_front_face_only: bpy.props.BoolProperty(
        name="Front Face Only",
        description="Vertex tool selects only vertices visible from the view; off selects regardless of facing",
        default=False,
    )

    last_action: bpy.props.StringProperty(default="")
    last_intensity: bpy.props.FloatProperty(default=0.0)


# ==============================================================================
# Preferences accessor
# ==============================================================================

class WeightApplyPreferencesService:
    """Stateless accessor for weight-apply prefs — consumed by logic.py and ui.py."""

    @staticmethod
    def get_prefs() -> "SSPrefWeightApply":
        return bpy.context.window_manager.superskin_weight_apply_prefs


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
    show_section_label = False
    draw_tab = "SKINNING"
    link = "https://docs.superskinpro.com/operation/"
    defaults_path = _DEFAULTS_PATH
    priority = 1
    keymaps = [
        {
            "key": "Alt+LMB", "label": "Add / Scale Gesture", "mode": "Hold",
            "source_label": "Add / Scale (start normal)",
        },
        {
            "key": "Alt+RMB", "label": "Smooth / Sharpen Gesture", "mode": "Hold",
            "source_label": "Smooth / Sharpen (start normal)",
        },
        {"key": "Alt+Shift+MMB", "label": "Drag-Select Add (Circle Brush)", "mode": "Hold"},
        {"key": "Alt+Ctrl+MMB", "label": "Drag-Select Remove (Circle Brush)", "mode": "Hold"},
        {"key": "Alt+Ctrl+Scroll", "label": "Grow/Shrink Selection"},
    ]
    expanded_by_default = True
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def snapshot_context(self, core_facade: CoreFacade, need_selected: bool = True) -> dict:
        """Read everything an apply action needs, once."""
        is_mask = core_facade.is_mask_context()
        active_vg_id = core_facade.get_active_vg_id()
        with core_facade.profile_section("weight_apply.snapshot.read_layer") as read_timer:
            layer_str = core_facade.read_active_layer()
            if read_timer.t0 is not None:
                read_timer.size = sum(map(len, layer_str.values()))
        bone_to_id, id_to_bone = core_facade.get_unified_mapping()
        with core_facade.profile_section("weight_apply.snapshot.remap", size=len(layer_str)):
            layer_int = {
                v_idx: {bone_to_id[b]: w for b, w in weights.items() if b in bone_to_id}
                for v_idx, weights in layer_str.items()
            }
        active_idx = core_facade.get_active_layer_index()
        mask_default = 1.0
        for m in core_facade.get_meta_list():
            if m.get("index", -1) == active_idx:
                mask_default = float(m.get("mask_default", 1.0))
                break
        with core_facade.profile_section("weight_apply.snapshot.mask_locks_selected"):
            mask_dict = core_facade.get_active_mask_dict()
            locks_id = core_facade.get_locks_by_id()
            selected = core_facade.get_selected_verts() if need_selected else []
        return {
            "is_mask": is_mask,
            "active_vg_id": active_vg_id,
            "layer_int": layer_int,
            "id_to_bone": id_to_bone,
            "mask_dict": mask_dict,
            "mask_default": mask_default,
            "locks_id": locks_id,
            "selected": selected,
            "_nearest_bones_cache": {},
        }

    def _run_compound_passes(self, intensity, pass_fn, layer_int_for_rust, mask_dict_for_rust):
        """Decompose `intensity` into `full_passes` chained intensity=1.0 calls to `pass_fn`
        (each fed the previous."""
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
        """Phase 1 of `apply_action()`: everything that depends on `action`/`ctx` but not on
        `intensity`."""
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

        dirty_verts = set(selected)
        neighbors = None
        coords = None
        density_factors = None
        smooth_coords = None
        smooth_neighbors = None
        if action == "smooth":
            with core_facade.profile_section("weight_apply.smooth.prepare.neighbors", size=len(selected)):
                neighbors = core_facade.get_cached_mesh_neighbors()
            # Fetched once and reused by every compound pass below, instead
            # of each pass re-reading the full mesh vertex coordinates.
            with core_facade.profile_section("weight_apply.smooth.prepare.coords", size=len(selected)):
                coords = ctx.get("_coords_cache")
                if coords is None:
                    coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
            with core_facade.profile_section("weight_apply.smooth.prepare.density", size=len(selected)):
                density_factors = compute_density_factors(coords, neighbors, selected)
            for v in selected:
                dirty_verts.update(neighbors.get(v, ()))
            # Scoped to dirty_verts (not the full-mesh copies above) so FFI
            # marshaling cost stays proportional to the brush footprint.
            with core_facade.profile_section("weight_apply.smooth.prepare.slice", size=len(dirty_verts)):
                smooth_coords = {v: coords[v] for v in dirty_verts}
                smooth_neighbors = {v: neighbors[v] for v in selected if v in neighbors}
        elif action == "sharpen":
            dirty_verts |= expand_sharpen_dirty_verts(core_facade, selected)
            coords = ctx.get("_coords_cache")
            if coords is None:
                coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
            neighbors = core_facade.get_cached_mesh_neighbors()

        with core_facade.profile_section(f"weight_apply.{action}.prepare.rust_inputs", size=len(dirty_verts)):
            layer_int_for_rust = {v: dict(ctx["layer_int"].get(v, {})) for v in dirty_verts}
            mask_dict_for_rust = {v: ctx["mask_dict"][v] for v in dirty_verts if v in ctx["mask_dict"]}
            mask_dict = dict(ctx["mask_dict"])

        active_layer_index = None
        locks_scoped = None
        nearest_bone_ids = {}
        if action == "add":
            active_layer_index = core_facade.get_active_layer_index()
        elif action == "scale":
            deform_ids = core_facade.get_deform_bone_ids()
            locks_scoped = {b_id: locked for b_id, locked in locks_id.items()
                            if b_id in deform_ids}
            if not is_mask:
                # Cached per-ctx — identical on every call sharing this ctx,
                # so a Scale drag doesn't re-run the spatial search each tick.
                nb_cache = ctx.get("_nearest_bones_cache")
                if nb_cache is not None and "nearest_bone_ids" in nb_cache:
                    nearest_bone_ids = nb_cache["nearest_bone_ids"]
                else:
                    chain_ids = compute_chain_bone_ids(
                        core_facade, active_vg_id, id_to_bone, deform_ids,
                    )
                    candidate_ids = {b_id for b_id in deform_ids
                                     if not locks_scoped.get(b_id) and b_id != active_vg_id
                                     and (chain_ids is None or b_id in chain_ids)}
                    nearest_bone_ids = compute_nearest_bones(
                        core_facade, selected, candidate_ids, id_to_bone,
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
            "mask_default": ctx["mask_default"],
            "active_layer_index": active_layer_index,
            "locks_scoped": locks_scoped,
            "nearest_bone_ids": nearest_bone_ids,
            "affected": affected,
        }

    def _dispatch_compute(self, action: str, action_data: dict, intensity: float,
                          *, rust_gateways: dict = None) -> dict:
        """Phase 2 of `apply_action()`: the Rust FFI dispatch."""
        from .logic import apply_add, apply_scale, apply_smooth, apply_sharpen

        rust_gateways = rust_gateways or {}
        is_mask = action_data["is_mask"]
        active_vg_id = action_data["active_vg_id"]
        selected = action_data["selected"]
        locks_id = action_data["locks_id"]
        layer_int_for_rust = action_data["layer_int_for_rust"]
        mask_dict_for_rust = action_data["mask_dict_for_rust"]
        mask_default = action_data["mask_default"]

        if action == "add":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_add(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                intensity, locks_id,
                action_data["active_layer_index"], is_mask, mask_default,
                rust=rust_gateways.get("add_logic"),
            )

        elif action == "scale":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_scale(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                intensity, action_data["locks_scoped"], is_mask, action_data["nearest_bone_ids"],
                mask_default,
                rust=rust_gateways.get("scale_logic"),
            )

        elif action == "smooth":
            res_layer_diff, res_mask_diff = apply_smooth(
                layer_int_for_rust, mask_dict_for_rust, selected,
                action_data["smooth_coords"], action_data["smooth_neighbors"],
                _decompose_intensity_passes(intensity), locks_id, action_data["affected"], is_mask,
                action_data["density_factors"], mask_default,
                rust=rust_gateways.get("smooth_logic"),
            )

        elif action == "sharpen":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            # Sharpen operates on every unlocked bone at each vertex, not
            # just active_vg_id — that's only the weight-painting-context gate.
            res_layer_diff, res_mask_diff = self._run_compound_passes(
                intensity,
                lambda layer_in, mask_in, step_intensity: apply_sharpen(
                    layer_in, mask_in, selected, action_data["coords"], action_data["neighbors"],
                    locks_id, step_intensity, is_mask,
                    mask_default=mask_default,
                    rust=rust_gateways.get("sharpen_logic"),
                ),
                layer_int_for_rust, mask_dict_for_rust,
            )

        else:
            return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

        return {"status": "OK", "res_layer_diff": res_layer_diff, "res_mask_diff": res_mask_diff}

    def _finish_write(self, action: str, core_facade: CoreFacade, ctx: dict,
                      action_data: dict, compute_result: dict) -> dict:
        """Phase 3 of `apply_action()`: merge the Rust diff into the full baseline and write it
        to BMesh/storage."""
        if compute_result["status"] == "CANCELLED":
            return compute_result

        res_layer_diff = compute_result["res_layer_diff"]
        res_mask_diff = compute_result["res_mask_diff"]

        is_mask = action_data["is_mask"]
        id_to_bone = action_data["id_to_bone"]
        dirty_verts = action_data["dirty_verts"]
        mask_dict = action_data["mask_dict"]

        with core_facade.profile_section(f"weight_apply.{action}.merge", size=len(dirty_verts)):
            full_layer_int = dict(ctx["layer_int"])
            full_layer_int.update(res_layer_diff)

        with core_facade.profile_section(f"weight_apply.{action}.write", size=len(dirty_verts)):
            if core_facade.is_addon_stroke_active(core_facade.get_obj().name):
                full_mask = None
                if is_mask:
                    full_mask = dict(ctx["mask_dict"])
                    full_mask.update(res_mask_diff)
                core_facade.write_active_layer_live(
                    full_layer_int, id_to_bone, dirty_verts,
                    mask_dict=full_mask, is_mask=is_mask,
                )
            elif is_mask:
                # Rust returns only the touched vertices in res_mask_diff;
                # merge into the full baseline so others keep their mask value.
                full_mask = dict(ctx["mask_dict"])
                full_mask.update(res_mask_diff)
                # is_mask_mode=True bypasses the bone-normalization loop,
                # which would otherwise prune unselected vertices.
                ctrl = core_facade.get_ctrl()
                ctrl._write_active_layer_string(full_layer_int, id_to_bone,
                                                full_mask, is_mask_mode=True,
                                                dirty_verts=dirty_verts,
                                                mask_default=action_data["mask_default"])
                core_facade.finish(color_only=True, dirty_verts=dirty_verts)
            else:
                ctrl = core_facade.get_ctrl()
                with core_facade.profile_section(f"weight_apply.{action}.write.storage",
                                                 size=len(dirty_verts)):
                    ctrl._write_active_layer_string(full_layer_int, id_to_bone, None,
                                                    is_mask_mode=False,
                                                    dirty_verts=dirty_verts)
                with core_facade.profile_section(f"weight_apply.{action}.write.finish",
                                                 size=len(dirty_verts)):
                    core_facade.finish(color_only=True, dirty_verts=dirty_verts)

        return {
            "status": "FINISHED",
            "layer_int": full_layer_int,
            "mask_dict": full_mask if is_mask else mask_dict,
        }

    def apply_action(self, action: str, core_facade: CoreFacade, ctx: dict,
                     intensity: float, *, affected_only: bool = None) -> dict:
        """Compute `action` from the `ctx` baseline at `intensity`, then write the result."""
        with core_facade.profile_section(f"weight_apply.{action}.prepare", size=len(ctx["selected"])):
            action_data = self._prepare_action_data(action, core_facade, ctx, affected_only=affected_only)
        with core_facade.profile_section(f"weight_apply.{action}.rust_ffi",
                                         size=len(action_data["dirty_verts"])):
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

    def get_keymap_items(self) -> list:
        """Expose the gesture/repeat bindings to the in-panel shortcut editor, alongside the
        Weight Brush tool's own toggle shortcut."""
        from . import keymap as _keymap
        from .brush_tool import brush_tool as _brush_tool
        from .vertex_tool import keymap as _vertex_keymap
        return (
            _keymap.get_registered_keymap_items()
            + _brush_tool.get_registered_keymap_items()
            + _vertex_keymap.get_registered_keymap_items()
        )

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """UI moved to features/skin_tools_ui/ui_weight_apply.py."""
        pass

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write section data dict into the live WindowManager property."""
        p = get_prefs()
        p.add_val = float(data.get("add_val", 0.61))
        p.scale_val = float(data.get("scale_val", 0.61))
        p.smooth_val = float(data.get("smooth_val", 0.61))
        p.sharpen_val = float(data.get("sharpen_val", 0.61))
        p.smooth_affected_only = bool(data.get("smooth_affected_only", False))

        from .brush_tool import BRUSH_ENABLED
        if BRUSH_ENABLED:
            from .brush_tool.brush_ops import populate_prefs
            populate_prefs(data.get("brush", {}))

    def serialize_into(self, full_dict: dict) -> None:
        """Write current values into full_dict at the correct JSON path."""
        p = get_prefs()
        section = {
            "add_val": p.add_val,
            "scale_val": p.scale_val,
            "smooth_val": p.smooth_val,
            "sharpen_val": p.sharpen_val,
            "smooth_affected_only": p.smooth_affected_only,
        }

        from .brush_tool import BRUSH_ENABLED
        if BRUSH_ENABLED:
            from .brush_tool.brush_ops import serialize_prefs
            section["brush"] = serialize_prefs()

        full_dict["weight_apply"] = section


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
