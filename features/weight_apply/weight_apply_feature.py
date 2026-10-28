
import os

import bpy
import numpy as np

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import live_feed
from .outcome import outcome_warning

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


def _engine_steps(action, amount):
    if action == "sharpen":
        return [max(0.0, min(1.0, amount))]
    return [amount]


def _intensity_steps(action, intensity):
    amount = max(0.0, intensity)
    return _engine_steps(action, amount if action == "smooth" else min(1.0, amount))


def _is_weight_tool_active(tool: str) -> bool:
    from .brush_tool.brush_tool import _WEIGHT_BRUSH_TOOL_IDNAME, _get_active_tool_idname
    from .vertex_tool.common import LASSO_TOOL_IDNAME

    target = _WEIGHT_BRUSH_TOOL_IDNAME if tool == "brush" else LASSO_TOOL_IDNAME
    return _get_active_tool_idname(bpy.context) == target


def _is_gesture_active(pair_action: str) -> bool:
    from . import draw
    return draw.active_pair() == pair_action


def _gesture_hints(positive: str, negative: str, apply_rows: list, cancel_key: str) -> list:
    return [
        {"key": "Drag Right", "label": positive},
        {"key": "Drag Left", "label": negative},
        {"key": "Shift", "label": "Slow Down", "mode": "Hold"},
        {"key": "Ctrl", "label": "Snap to 0.1", "mode": "Hold"},
        {"separator": True},
        *apply_rows,
        {"key": cancel_key, "label": "Cancel"},
    ]


def keep_volume_enabled() -> bool:
    prefs = getattr(bpy.context.window_manager, "superskin_weight_brush_prefs", None)
    return bool(prefs is not None and prefs.keep_volume)


def _needs_active_bone(action, action_data):
    return (action in ("add", "scale", "sharpen") and action_data["active_vg_id"] is None
            and not action_data["is_mask"])



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
        name="Add", min=0.001, max=1.0, precision=3, default=0.61,
        description="Add weight strength",
        update=_on_intensity_changed,
    )

    scale_val: bpy.props.FloatProperty(
        name="Scale", min=0.0, max=0.999, precision=3, default=0.61,
        description="Reduce weights (lower value = stronger effect)",
        update=_on_intensity_changed,
    )
    smooth_val: bpy.props.FloatProperty(
        name="Smooth", min=0.001, max=100.0, soft_max=1.0, precision=3, default=0.61,
        description="Smooth weight blending (Drag up to 1.0, type up to 100).",
        update=_on_intensity_changed,
    )
    sharpen_val: bpy.props.FloatProperty(
        name="Sharpen", min=0.001, max=1.0, precision=3, default=0.61,
        description="Bring back volume lost from smoothing.",
        update=_on_intensity_changed,
    )
    smooth_affected_only: bpy.props.BoolProperty(
        name="Smooth Affected Only",
        description="Only smooth vertices that already have weight",
        default=False,
        update=_on_smooth_affected_only_changed,
    )

    vertex_front_face_only: bpy.props.BoolProperty(
        name="Front Face Only for Lasso",
        description="Select visible vertices only when using Lasso.",
        default=False,
    )

    last_action: bpy.props.StringProperty(default="")
    last_intensity: bpy.props.FloatProperty(default=0.0)



class WeightApplyPreferencesService:
    """Stateless accessor for weight-apply prefs — consumed by logic.py and ui.py."""

    @staticmethod
    def get_prefs() -> "SSPrefWeightApply":
        return bpy.context.window_manager.superskin_weight_apply_prefs


get_prefs = WeightApplyPreferencesService.get_prefs



class WeightApplyFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Apply domain."""


    domain_id = "weight_apply"
    actions = ["add", "scale", "smooth", "sharpen"]
    section_title = "Apply"
    show_section_label = False
    draw_tab = "SKINNING"
    link = "https://tonmaiart.github.io/superskinpro-docs/operation/"
    defaults_path = _DEFAULTS_PATH
    priority = 1
    keymaps = [
        {
            "key": "Alt+Shift+RMB", "label": "Switch Brush / Vertex Tool",
            "source_label": "Toggle Weight Brush Tool",
        },
        {
            "is_active": lambda: _is_gesture_active("add_scale"),
            "exclusive": True,
            "sub_keymaps": _gesture_hints("Add Weight", "Scale Weight", [
                {"key": "Release", "label": "Apply"},
            ], "Right Click / Esc"),
        },
        {
            "is_active": lambda: _is_gesture_active("smooth_sharpen"),
            "exclusive": True,
            "sub_keymaps": _gesture_hints("Smooth Weight", "Sharpen Weight", [
                {"key": "Release / Left Click", "label": "Apply"},
            ], "Esc"),
        },
        {
            "is_active": lambda: _is_weight_tool_active("brush"),
            "sub_keymaps": [
                {"key": "Paint", "label": "Add"},
                {"key": "Shift+Paint", "label": "Smooth"},
                {"key": "Ctrl+Paint", "label": "Scale (Remove)"},
                {"key": "Alt+Paint", "label": "Sharpen"},
                {"key": "F", "label": "Change Brush Size"},
                {"key": "Alt+F", "label": "Toggle Projected"},
                {"key": "RMB", "label": "Options"},
            ],
        },
        {
            "is_active": lambda: _is_weight_tool_active("vertex"),
            "sub_keymaps": [
                {
                    "key": "Alt+LMB", "label": "Add / Scale Weight", "mode": "Hold",
                    "source_label": "Add / Scale (start normal)",
                },
                {
                    "key": "Alt+RMB", "label": "Smooth / Sharpen Weight", "mode": "Hold",
                    "source_label": "Smooth / Sharpen (start normal)",
                },
                {"key": "Double-Click", "label": "Select Linked"},
                {"key": "A / Alt+A", "label": "Select All / Deselect All"},
                {"key": "H / Alt+H / Shift+H", "label": "Hide Selected / Reveal Hidden / Hide Unselected"},
                {"key": "Alt+Ctrl+Scroll", "label": "Grow / Shrink Selection"},
                {"key": "RMB", "label": "Options"},
            ],
        },
    ]
    expanded_by_default = True
    locked_expanded = True


    def snapshot_context(self, core_facade: CoreFacade, need_selected: bool = True) -> dict:
        is_mask = core_facade.is_mask_context()
        active_vg_id = core_facade.get_active_vg_id()
        with core_facade.profile_section("weight_apply.snapshot.read_layer") as read_timer:
            layer_int = core_facade.read_active_layer_int()
            if read_timer.t0 is not None:
                read_timer.size = sum(map(len, layer_int.values()))
        _bone_to_id, id_to_bone = core_facade.get_unified_mapping()
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
            "_scale_targets_cache": {},
        }

    def _prepare_action_data(self, action: str, core_facade: CoreFacade, ctx: dict,
                             *, affected_only: bool = None, links: dict = None,
                             welds: dict = None, keep_volume: bool = False) -> dict:
        from .logic import (SHARPEN_REGION_HOPS, expand_hops_csr, smooth_reference_edges,
                            weld_closure, weld_members)

        p = get_prefs()
        is_mask = ctx["is_mask"]
        active_vg_id = ctx["active_vg_id"]
        id_to_bone = ctx["id_to_bone"]
        selected = ctx["selected"]
        locks_id = ctx["locks_id"]
        volume = self._keep_volume_scope(core_facade, ctx, selected) if keep_volume and selected else None
        if volume is not None:
            selected = volume["selected"]
            if action == "smooth":
                links, welds = volume["links"], volume["welds"]

        dirty_verts = set(selected)
        neighbors = None
        coords = None
        smooth_coords = None
        smooth_neighbors = None
        smooth_flat = None
        smooth_links = None
        sharpen_flat = None
        if action == "smooth":
            with core_facade.profile_section("weight_apply.smooth.prepare.neighbors", size=len(selected)):
                neighbors = core_facade.get_cached_mesh_neighbors()
            with core_facade.profile_section("weight_apply.smooth.prepare.coords", size=len(selected)):
                coords = ctx.get("_coords_cache")
                if coords is None:
                    coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
            if "_smooth_reference_edges" not in ctx:
                ctx["_smooth_reference_edges"] = smooth_reference_edges(
                    self._smooth_flat(ctx, coords, neighbors)[0], neighbors)
            for v in selected:
                dirty_verts.update(neighbors.get(v, ()))
            if links:
                smooth_links = {v: links[v] for v in selected if v in links}
                for linked in smooth_links.values():
                    dirty_verts.update(linked)
            if welds and not any(v in welds for v in selected):
                welds = None
            if welds:
                members = weld_members(welds)
                welded = weld_closure(welds, members, selected)
                for v in welded:
                    dirty_verts.update(neighbors.get(v, ()))
                dirty_verts = weld_closure(welds, members, dirty_verts | welded)
                with core_facade.profile_section("weight_apply.smooth.prepare.slice", size=len(dirty_verts)):
                    smooth_coords = {v: coords[v] for v in dirty_verts}
                    smooth_neighbors = {v: neighbors[v] for v in selected if v in neighbors}
            else:
                smooth_flat = self._smooth_flat(ctx, coords, neighbors)
        elif action == "sharpen":
            coords = ctx.get("_coords_cache")
            if coords is None:
                coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
            neighbors = core_facade.get_cached_mesh_neighbors()
            sharpen_flat = self._sharpen_flat(core_facade, ctx, coords, neighbors)
            dirty_verts.update(expand_hops_csr(*sharpen_flat[-2:], selected, SHARPEN_REGION_HOPS).tolist())

        mask_dict = ctx["mask_dict"]
        with core_facade.profile_section(f"weight_apply.{action}.prepare.rust_inputs",
                                         size=len(dirty_verts)):
            layer = ctx["layer_int"]
            layer_int_for_rust = {v: layer.get(v, {}) for v in dirty_verts}
            mask_dict_for_rust = {v: mask_dict[v] for v in dirty_verts if v in mask_dict}

        active_layer_index = None
        locks_scoped = None
        recipient_shares = {}
        if action == "add":
            active_layer_index = core_facade.get_active_layer_index()
        elif action == "scale":
            deform_ids = core_facade.get_deform_bone_ids()
            locks_scoped = {b_id: locked for b_id, locked in locks_id.items()
                            if b_id in deform_ids}
            if not is_mask:
                recipient_shares = self._scale_recipient_shares(
                    core_facade, ctx, selected, deform_ids, locks_scoped,
                )

        affected = p.smooth_affected_only if affected_only is None else affected_only

        return {
            "is_mask": is_mask,
            "active_vg_id": active_vg_id,
            "id_to_bone": id_to_bone,
            "selected": selected,
            "locks_id": locks_id,
            "dirty_verts": dirty_verts,
            "write_verts": set(selected),
            "coords": coords,
            "neighbors": neighbors,
            "smooth_coords": smooth_coords,
            "smooth_neighbors": smooth_neighbors,
            "smooth_flat": smooth_flat,
            "smooth_links": smooth_links,
            "smooth_welds": welds if action == "smooth" else None,
            "keep_volume": None if volume is None else {
                "bind": volume["bind"], "piece_of": volume["piece_of"], "leader": volume["leader"],
                "layer": ctx["layer_int"], "mask": ctx["mask_dict"],
            },
            "sharpen_flat": sharpen_flat,
            "layer_int_for_rust": layer_int_for_rust,
            "mask_dict_for_rust": mask_dict_for_rust,
            "mask_dict": mask_dict,
            "mask_default": ctx["mask_default"],
            "active_layer_index": active_layer_index,
            "locks_scoped": locks_scoped,
            "recipient_shares": recipient_shares,
            "baseline_layer": layer_int_for_rust,
            "affected": affected,
            "smooth_reference_edges": ctx.get("_smooth_reference_edges"),
        }

    @staticmethod
    def _smooth_flat(ctx, coords, neighbors):
        from .brush_tool.brush_logic import _adjacency_csr
        cached = ctx.get("_smooth_flat")
        if cached is None or cached[0] is not coords:
            cached = ctx["_smooth_flat"] = (coords, np.asarray(coords, dtype=np.float64).ravel())
        offsets, targets = _adjacency_csr(neighbors, len(coords))
        return cached[1], np.frombuffer(offsets, dtype=np.int64), np.frombuffer(targets, dtype=np.int64)

    def _sharpen_flat(self, core_facade, ctx, coords, neighbors):
        from .logic import posed_geometry_flat, vertex_normals_flat
        coords_flat, offsets, targets = self._smooth_flat(ctx, coords, neighbors)
        normals = ctx.get("_normals_flat")
        if normals is None:
            normals = ctx["_normals_flat"] = vertex_normals_flat(core_facade, len(coords))
        posed = ctx.get("_posed_cache")
        if posed is None:
            posed = ctx["_posed_cache"] = posed_geometry_flat(core_facade, len(coords))
        return coords_flat, normals, posed[0], posed[1], offsets, targets

    def _keep_volume_scope(self, core_facade, ctx, selected):
        from collections import Counter
        from .brush_tool.brush_logic import (bind_links, build_layer_bind, build_reach_graph,
                                             carry_selected_layers, coincident_welds,
                                             vertex_hide_flags, weld_joined_pieces)
        from .logic import weld_closure, weld_members
        coords = ctx.get("_coords_cache")
        if coords is None:
            coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
        mesh = core_facade.get_obj().data
        try:
            bind = build_layer_bind(core_facade, coords, mesh)
            welds = coincident_welds(core_facade, coords)
            piece_of = weld_joined_pieces(build_reach_graph(core_facade, coords).piece_of, welds, len(coords))[0]
        except Exception as exc:
            core_facade.debug_log("feature_domains", f"weight_apply keep volume setup failed: {exc!r}")
            return None
        hidden = vertex_hide_flags(mesh)
        members = weld_members(welds)
        leader = Counter(piece_of(v) for v in selected).most_common(1)[0][0]
        chosen = {v for v in weld_closure(welds, members, selected) if not hidden[v]}
        chosen |= carry_selected_layers(bind, chosen, piece_of, leader, hidden)
        chosen = {v for v in weld_closure(welds, members, chosen) if not hidden[v]}
        return {
            "selected": sorted(chosen), "bind": bind, "piece_of": piece_of, "leader": leader,
            "welds": welds or None, "links": bind_links(bind, coords) or None,
        }

    @staticmethod
    def _follow_selection(action_data, result):
        from .brush_tool.brush_logic import lead_rows
        volume = action_data.get("keep_volume")
        if not volume:
            return
        write = action_data["write_verts"]
        piece_of, leader = volume["piece_of"], volume["leader"]
        if action_data["is_mask"]:
            diff, base, default = result["res_mask_diff"], volume["mask"], action_data["mask_default"]
            rows = lead_rows(
                volume["bind"], {v for v in write if piece_of(v) == leader},
                lambda v: diff[v] if v in diff else base.get(v, default), piece_of, leader,
                lambda _v, _corners: 1.0, before=lambda v: base.get(v, default),
            )
        else:
            diff, base = result["res_layer_diff"], volume["layer"]
            rows = lead_rows(
                volume["bind"], {v for v in write if piece_of(v) == leader},
                lambda v: diff[v] if v in diff else base.get(v, {}), piece_of, leader,
                lambda _v, _corners: 1.0, locks=action_data["locks_id"],
                before=lambda v: base.get(v, {}),
            )
        diff.update({v: row for v, row in rows.items() if v in write})

    def _scale_recipient_shares(self, core_facade, ctx, selected, deform_ids, locks_scoped):
        from .logic import compute_scale_recipient_shares, compute_chain_bone_ids
        cache = ctx.get("_scale_targets_cache")
        if cache is not None and "recipient_shares" in cache:
            return cache["recipient_shares"]
        active_vg_id = ctx["active_vg_id"]
        id_to_bone = ctx["id_to_bone"]
        scale_coords = ctx.get("_coords_cache")
        if scale_coords is None:
            scale_coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
        candidate_ids = {b_id for b_id in deform_ids
                         if not locks_scoped.get(b_id) and b_id != active_vg_id}
        chain_ids = compute_chain_bone_ids(core_facade, active_vg_id, id_to_bone, deform_ids)
        with core_facade.profile_section("weight_apply.scale.prepare.targets", size=len(selected)):
            recipient_shares = compute_scale_recipient_shares(
                core_facade, ctx["layer_int"], scale_coords, selected,
                active_vg_id, candidate_ids, chain_ids, id_to_bone,
            )
        if cache is not None:
            cache["recipient_shares"] = recipient_shares
        return recipient_shares

    def _dispatch_compute(self, action: str, action_data: dict, intensity: float,
                          *, rust_gateways: dict = None) -> dict:
        result = self._compute(action, action_data, intensity, rust_gateways)
        if result["status"] == "CANCELLED":
            return result
        self._follow_selection(action_data, result)
        if action_data["is_mask"]:
            return result
        banding = action_data.get("banding")
        warning = outcome_warning(
            action, action_data["baseline_layer"], result["res_layer_diff"],
            action_data["selected"], action_data["active_vg_id"],
            (lambda i: banding[1][banding[0][i]][0]) if banding else (lambda i: intensity),
        )
        if warning:
            result["warning"] = warning
        return result

    def _compute(self, action: str, action_data: dict, intensity: float,
                 rust_gateways: dict = None) -> dict:
        from .logic import (apply_add, apply_scale, apply_implicit_smooth_csr, apply_sharpen_scoped,
                            apply_welded_smooth, smooth_radius)

        rust_gateways = rust_gateways or {}
        session = action_data.get("session")
        if session is not None:
            return self._dispatch_session(action, action_data, intensity, session)
        is_mask = action_data["is_mask"]
        active_vg_id = action_data["active_vg_id"]
        selected = action_data["selected"]
        locks_id = action_data["locks_id"]
        layer_int_for_rust = action_data["layer_int_for_rust"]
        mask_dict_for_rust = action_data["mask_dict_for_rust"]
        mask_default = action_data["mask_default"]
        banding = action_data.get("banding")
        follow_view = None

        if action == "add":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_add(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                _intensity_steps(action, intensity)[0], locks_id,
                action_data["active_layer_index"], is_mask, mask_default,
                rust=rust_gateways.get("add_logic"), banding=banding,
            )

        elif action == "scale":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            res_layer_diff, res_mask_diff = apply_scale(
                layer_int_for_rust, mask_dict_for_rust, selected,
                active_vg_id if active_vg_id is not None else -1,
                _intensity_steps(action, intensity)[0], action_data["locks_scoped"], is_mask, action_data["recipient_shares"],
                mask_default,
                rust=rust_gateways.get("scale_logic"), banding=banding,
            )

        elif action == "smooth":
            reference = action_data["smooth_reference_edges"]
            if banding is not None:
                selected_band, band_steps = banding
                closes = [steps[0] for steps in band_steps]
            else:
                selected_band, closes = None, [_intensity_steps(action, intensity)[0]]
            radii = [smooth_radius(step, reference) for step in closes]
            smooth_rust = rust_gateways.get("smooth_logic") or CoreFacade.get_rust_gateway("smooth_logic")
            if action_data.get("smooth_welds"):
                follow_view = {}
                res_layer_diff, res_mask_diff = apply_welded_smooth(
                    action_data["smooth_welds"], action_data["coords"], action_data["neighbors"],
                    layer_int_for_rust, mask_dict_for_rust, selected, radii, locks_id,
                    action_data["affected"], is_mask, mask_default,
                    rust=smooth_rust, selected_band=selected_band, links=action_data.get("smooth_links"),
                    closes=closes, eased=follow_view,
                )
            else:
                coords_flat, offsets, targets = action_data["smooth_flat"]
                res_layer_diff, res_mask_diff = apply_implicit_smooth_csr(
                    layer_int_for_rust, mask_dict_for_rust, selected, coords_flat, offsets, targets,
                    radii, locks_id, action_data["affected"], is_mask, mask_default,
                    rust=smooth_rust, selected_band=selected_band, links=action_data.get("smooth_links"),
                )

        elif action == "sharpen":
            if active_vg_id is None and not is_mask:
                return {"status": "CANCELLED", "message": "No active bone"}
            rust = rust_gateways.get("sharpen_logic") or CoreFacade.get_rust_gateway("sharpen_logic")
            res_layer_diff, res_mask_diff = apply_sharpen_scoped(
                layer_int_for_rust, mask_dict_for_rust, selected, action_data["sharpen_flat"], locks_id,
                _intensity_steps(action, intensity), is_mask,
                mask_default=mask_default, rust=rust, banding=banding,
            )

        else:
            return {"status": "CANCELLED", "message": f"Unknown action: {action}"}

        result = {"status": "OK", "res_layer_diff": res_layer_diff, "res_mask_diff": res_mask_diff}
        if follow_view:
            result["follow_view"] = follow_view
        return result

    def build_session(self, action: str, action_data: dict, rust) -> None:
        from .logic import SHARPEN_REGION_HOPS
        fn_name = ("rust_weight_session_boundary_sharpen_flat" if action == "sharpen"
                   else f"rust_weight_session_{action}")
        if action_data.get("session") is not None:
            return
        if _needs_active_bone(action, action_data):
            return
        if action == "smooth":
            return
        vert_ids, bone_ids, weights = CoreFacade.layer_to_coo(action_data["layer_int_for_rust"])
        mask = action_data["mask_dict_for_rust"]
        selected = list(action_data["selected"])
        active = action_data["active_vg_id"] if action_data["active_vg_id"] is not None else -1
        is_mask = action_data["is_mask"]
        mask_default = action_data["mask_default"]
        if action == "add":
            args = (vert_ids, bone_ids, weights, mask, selected, active,
                    action_data["locks_id"], is_mask, mask_default)
        elif action == "scale":
            args = (vert_ids, bone_ids, weights, mask, selected, active,
                    action_data["locks_scoped"], is_mask, action_data["recipient_shares"],
                    mask_default)
        else:
            args = (vert_ids, bone_ids, weights, mask, np.asarray(selected, dtype=np.int64),
                    *action_data["sharpen_flat"], action_data["locks_id"], is_mask,
                    SHARPEN_REGION_HOPS, mask_default)
        action_data["session"] = rust.call(fn_name, *args)

    def _dispatch_session(self, action, action_data, intensity, session):
        if _needs_active_bone(action, action_data):
            return {"status": "CANCELLED", "message": "No active bone"}
        out_v, out_b, out_w, res_mask_diff = session.compute(_intensity_steps(action, intensity))
        return {"status": "OK", "res_layer_diff": CoreFacade.coo_to_layer(out_v, out_b, out_w),
                "res_mask_diff": res_mask_diff}

    @staticmethod
    def _merge_into(base: dict, diff: dict, working, name: str) -> dict:
        if working is True:
            base.update(diff)
            return base
        if working is None:
            merged = dict(base)
            merged.update(diff)
            return merged
        merged = working.get(name)
        touched_key = name + "_touched"
        if merged is None:
            merged = working[name] = dict(base)
        else:
            for v in working.get(touched_key, ()):
                if v in base:
                    merged[v] = base[v]
                else:
                    merged.pop(v, None)
        merged.update(diff)
        working[touched_key] = set(diff)
        return merged

    def _finish_write(self, action: str, core_facade: CoreFacade, ctx: dict,
                      action_data: dict, compute_result: dict, *, working: dict = None) -> dict:
        if compute_result["status"] == "CANCELLED":
            return compute_result

        res_layer_diff = compute_result["res_layer_diff"]
        res_mask_diff = compute_result["res_mask_diff"]

        is_mask = action_data["is_mask"]
        id_to_bone = action_data["id_to_bone"]
        dirty_verts = action_data.get("write_verts", action_data["dirty_verts"])
        mask_dict = action_data["mask_dict"]

        with core_facade.profile_section(f"weight_apply.{action}.merge", size=len(dirty_verts)):
            full_layer_int = self._merge_into(ctx["layer_int"], res_layer_diff, working, "layer")

        with core_facade.profile_section(f"weight_apply.{action}.write", size=len(dirty_verts)):
            if core_facade.is_addon_stroke_active(core_facade.get_obj().name):
                full_mask = None
                if is_mask:
                    full_mask = self._merge_into(ctx["mask_dict"], res_mask_diff, working, "mask")
                core_facade.write_active_layer_live(
                    full_layer_int, id_to_bone, dirty_verts, mask_dict=full_mask, is_mask=is_mask,
                )
                if not is_mask:
                    live_feed.publish(core_facade.get_obj(), full_layer_int, id_to_bone,
                                      dirty_verts)
            elif is_mask:
                full_mask = self._merge_into(ctx["mask_dict"], res_mask_diff, working, "mask")
                core_facade.write_active_layer_int(full_layer_int, id_to_bone, full_mask,
                                                   is_mask_mode=True, dirty_verts=dirty_verts)
                core_facade.finish(color_only=True)
            else:
                with core_facade.profile_section(f"weight_apply.{action}.write.storage",
                                                 size=len(dirty_verts)):
                    core_facade.write_active_layer_int(full_layer_int, id_to_bone, None,
                                                       is_mask_mode=False,
                                                       dirty_verts=dirty_verts)
                with core_facade.profile_section(f"weight_apply.{action}.write.finish",
                                                 size=len(dirty_verts)):
                    core_facade.finish(color_only=True)

        return {
            "status": "FINISHED",
            "layer_int": full_layer_int,
            "mask_dict": full_mask if is_mask else mask_dict,
            "warning": compute_result.get("warning"),
        }

    def apply_bands(self, action: str, core_facade: CoreFacade, ctx: dict, bands,
                    *, in_place: bool = False, links: dict = None, welds: dict = None,
                    follow=None) -> dict:
        verts_all = [v for _intensity, verts in bands for v in verts]
        if not verts_all:
            return {"status": "CANCELLED", "message": "Nothing to apply"}
        if action == "scale" and not ctx["is_mask"]:
            ctx["_scale_targets_cache"] = {}

        ctx["selected"] = verts_all
        with core_facade.profile_section(f"weight_apply.{action}.prepare", size=len(verts_all)):
            action_data = self._prepare_action_data(action, core_facade, ctx, links=links, welds=welds)
        action_data["banding"] = (
            [i for i, (_intensity, verts) in enumerate(bands) for _v in verts],
            [_engine_steps(action, intensity) for intensity, _verts in bands],
        )
        with core_facade.profile_section(f"weight_apply.{action}.rust_ffi",
                                         size=len(action_data["dirty_verts"])):
            result = self._dispatch_compute(action, action_data, bands[0][0])
        if result["status"] == "CANCELLED":
            return result

        write_set = action_data["write_verts"]
        result["res_layer_diff"] = {v: row for v, row in result["res_layer_diff"].items() if v in write_set}
        result["res_mask_diff"] = {v: m for v, m in result["res_mask_diff"].items() if v in write_set}
        self._merge_follow(ctx, action_data, result, follow)
        return self._finish_write(
            action, core_facade, ctx, action_data, result,
            working=True if in_place else None,
        )

    def brush_sharpen_target(self, core_facade: CoreFacade, ctx: dict, *, links: dict = None):
        from .brush_tool.brush_logic import _adjacency_csr
        from .logic import (brush_sharpen_target, linked_average, posed_geometry_flat,
                            vertex_normals_flat)
        if ctx["active_vg_id"] is None and not ctx["is_mask"]:
            return None
        coords = ctx.get("_coords_cache")
        if coords is None:
            coords = ctx["_coords_cache"] = core_facade.get_vertex_coordinates()
        flat = ctx.get("_rest_flat")
        if flat is None or flat[0] is not coords:
            flat = ctx["_rest_flat"] = (
                coords, np.asarray(coords, dtype=np.float64).ravel(),
                vertex_normals_flat(core_facade, len(coords)),
            )
        layer, mask = ctx["layer_int"], ctx["mask_dict"]
        if links:
            layer, mask = linked_average(layer, mask, links, ctx["is_mask"], ctx["mask_default"])
        with core_facade.profile_section("weight_apply.sharpen.target", size=len(coords)):
            return brush_sharpen_target(
                layer, mask, flat[1], flat[2],
                posed_geometry_flat(core_facade, len(coords)),
                _adjacency_csr(core_facade.get_cached_mesh_neighbors(), len(coords)),
                ctx["locks_id"], ctx["is_mask"], ctx["mask_default"],
                rust=CoreFacade.get_rust_gateway("sharpen_logic"),
            )

    @staticmethod
    def _merge_follow(ctx, action_data, result, follow):
        if follow is None:
            return
        diff = result["res_mask_diff" if ctx["is_mask"] else "res_layer_diff"]
        view = result.get("follow_view")
        extra = follow({**diff, **{v: row for v, row in view.items() if v in diff}} if view else diff)
        if extra:
            diff.update(extra)
            action_data["write_verts"] = set(action_data["write_verts"]) | set(extra)

    def apply_target_bands(self, core_facade: CoreFacade, ctx: dict, target, bands,
                           *, in_place: bool = False, follow=None) -> dict:
        from .logic import blend_toward_target
        verts_all = [v for _amount, verts in bands for v in verts]
        if not verts_all:
            return {"status": "CANCELLED", "message": "Nothing to apply"}
        amounts = [amount for amount, verts in bands for _v in verts]
        ctx["selected"] = verts_all
        with core_facade.profile_section("weight_apply.sharpen.blend", size=len(verts_all)):
            layer_diff, mask_diff = blend_toward_target(target, verts_all, amounts)
        write_set = set(verts_all)
        action_data = {
            "is_mask": ctx["is_mask"],
            "id_to_bone": ctx["id_to_bone"],
            "dirty_verts": write_set,
            "write_verts": write_set,
            "mask_dict": ctx["mask_dict"],
        }
        result = {"status": "OK", "res_layer_diff": layer_diff, "res_mask_diff": mask_diff}
        self._merge_follow(ctx, action_data, result, follow)
        return self._finish_write("sharpen", core_facade, ctx, action_data, result,
                                  working=True if in_place else None)

    def apply_action(self, action: str, core_facade: CoreFacade, ctx: dict,
                     intensity: float, *, affected_only: bool = None) -> dict:
        with core_facade.profile_section(f"weight_apply.{action}.prepare", size=len(ctx["selected"])):
            action_data = self._prepare_action_data(
                action, core_facade, ctx, affected_only=affected_only,
                keep_volume=keep_volume_enabled(),
            )
        with core_facade.profile_section(f"weight_apply.{action}.rust_ffi",
                                         size=len(action_data["dirty_verts"])):
            compute_result = self._dispatch_compute(action, action_data, intensity)
        obj_name = core_facade.get_obj().name
        own_stroke = (compute_result["status"] != "CANCELLED"
                      and not core_facade.is_addon_stroke_active(obj_name)
                      and core_facade.is_paint_session())
        if not own_stroke:
            return self._finish_write(action, core_facade, ctx, action_data, compute_result)
        core_facade.begin_live_stroke()
        try:
            return self._finish_write(action, core_facade, ctx, action_data, compute_result)
        finally:
            core_facade.end_live_stroke()

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
        from . import keymap as _keymap
        from .brush_tool import brush_tool as _brush_tool
        from .vertex_tool import keymap as _vertex_keymap
        return (
            _keymap.get_registered_keymap_items()
            + _brush_tool.get_registered_keymap_items()
            + _vertex_keymap.get_registered_keymap_items()
        )


    def draw_section(self, layout, context) -> None:
        pass


    def populate(self, data: dict) -> None:
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



def register():
    bpy.utils.register_class(SSPrefWeightApply)
    bpy.types.WindowManager.superskin_weight_apply_prefs = bpy.props.PointerProperty(
        type=SSPrefWeightApply, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(WeightApplyFeature())


def unregister():
    UnifiedRegistry.unregister("weight_apply")
    try:
        del bpy.types.WindowManager.superskin_weight_apply_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefWeightApply)
