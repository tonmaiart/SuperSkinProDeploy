
import bpy
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from .in_mesh_transfer import logic as in_mesh_transfer_logic
from .hammer import logic as hammer_logic
from .copy_vertex_weight import logic as copy_vertex_weight_logic
from .copy_weight import logic as copy_weight_logic
from .select_vertices import logic as select_vertices_logic
from .limit_total import logic as limit_total_logic
from .mirror.logic import execute_mirror_pipeline

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


def _write_pool_blend(core_facade: CoreFacade, blend: dict, pool_bone_names: set) -> None:
    layer_dict = core_facade.read_active_layer()
    for v_idx, bone_weights in blend.items():
        vert_layer = layer_dict.setdefault(v_idx, {})
        for stale in (vert_layer.keys() & pool_bone_names) - bone_weights.keys():
            del vert_layer[stale]
        outside_pool_total = sum(w for n, w in vert_layer.items() if n not in pool_bone_names)
        budget = max(0.0, 1.0 - outside_pool_total)
        pool_total = sum(bone_weights.values())
        scale = (budget / pool_total) if pool_total > 1e-9 else 0.0
        vert_layer.update({n: w * scale for n, w in bone_weights.items()})
    core_facade.write_active_layer_touched(layer_dict, set(blend.keys()))



def _on_changed(self, context):
    from ...core.facade import CoreFacade
    CoreFacade.save_prefs()


class SSPrefMirrorSRItem(bpy.types.PropertyGroup):
    """A single bone-name search/replace rule used to find mirror pairs."""
    search_text:  bpy.props.StringProperty(name="Search",  update=_on_changed)
    replace_text: bpy.props.StringProperty(name="Replace", update=_on_changed)


class SSPrefMirror(bpy.types.PropertyGroup):
    """Mirror settings (per-machine — shared across every .blend file)."""
    mirror_axis: bpy.props.EnumProperty(
        name="MirrorAxis",
        items=[
            ('X', "X", "Mirror along axis X"),
            ('Y', "Y", "Mirror along axis Y"),
            ('Z', "Z", "Mirror along axis Z"),
        ],
        default='X',
        update=_on_changed,
    )
    direction: bpy.props.EnumProperty(
        name="Direction",
        items=[
            ('POS_NEG', "Positive to Negative", "Mirror from the positive side to the negative side"),
            ('NEG_POS', "Negative to Positive", "Mirror from the negative side to the positive side"),
        ],
        default='POS_NEG',
        update=_on_changed,
    )
    mirror_data: bpy.props.EnumProperty(
        name="Mirror Data",
        items=[
            ('BONE', "Bone", "Mirror only the bone weights"),
            ('MASK', "Layer", "Mirror only the layer mask"),
            ('BOTH', "Bone and Layer", "Mirror both the bone weights and the layer mask"),
        ],
        default='BOTH',
        update=_on_changed,
    )
    search_replace_pairs:  bpy.props.CollectionProperty(type=SSPrefMirrorSRItem)
    search_replace_index:  bpy.props.IntProperty(name="Index", default=0)



class MirrorPreferencesService:
    """Stateless accessor for mirror prefs — consumed by mirror/logic.py."""

    @staticmethod
    def _prefs() -> "SSPrefMirror":
        return bpy.context.window_manager.superskin_mirror_prefs

    @classmethod
    def get_mirror_axis(cls) -> str:
        return cls._prefs().mirror_axis

    @classmethod
    def get_mirror_direction(cls) -> str:
        return cls._prefs().direction

    @classmethod
    def get_mirror_data(cls) -> str:
        return cls._prefs().mirror_data

    @classmethod
    def get_mirror_search_replace_pairs(cls) -> list:
        return [(p.search_text, p.replace_text) for p in cls._prefs().search_replace_pairs]



class WeightToolkitFeature(UnifiedFeatureExtension):
    """Unified extension for the Weight Toolkit domain (Mirror, Auto Block Weight,
    In-Mesh Transfer, Hammer, Copy Vertex Weight)."""


    domain_id = "weight_toolkit"
    actions = ["mirror", "auto", "mark_source", "transfer", "hammer", "copy_single",
               "paste_replace", "select_exceeded", "limit_total", "normalize",
               "copy_bone_plane", "cut_bone_plane", "paste_bone_plane_add",
               "paste_bone_plane_subtract", "paste_bone_plane_replace",
               "copy_layer_plane", "cut_layer_plane", "paste_layer_plane_add",
               "paste_layer_plane_subtract", "paste_layer_plane_replace",
               "select_bone_affected", "deselect_bone_affected",
               "select_mask_affected", "deselect_mask_affected"]
    section_title = "Weight Toolkit"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH
    priority = 2
    locked_expanded = True


    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        core_facade.debug_log("feature_domains", f"weight_toolkit.execute() action={action!r}")
        try:
            if action == "mirror":
                execute_mirror_pipeline(core_facade)
                return {"status": "FINISHED"}
            if action == "auto":
                return self._execute_auto(core_facade)
            if action == "mark_source":
                count = in_mesh_transfer_logic.mark_source(core_facade)
                if count is None:
                    core_facade.show_toast("Source mark cleared")
                else:
                    core_facade.show_toast(f"Marked {count} vertices as Source")
                return {"status": "FINISHED"}
            if action == "transfer":
                in_mesh_transfer_logic.transfer(core_facade)
                return {"status": "FINISHED"}
            if action == "hammer":
                hammer_logic.hammer(core_facade)
                return {"status": "FINISHED"}
            if action == "copy_single":
                copy_vertex_weight_logic.copy_single_vertex(core_facade)
                return {"status": "FINISHED"}
            if action == "paste_replace":
                copy_vertex_weight_logic.paste_vertex(core_facade)
                return {"status": "FINISHED"}
            if "_plane" in action:
                return self._execute_copy_weight(action, core_facade)
            if action.endswith("_affected"):
                deselect = action.startswith("deselect_")
                count = select_vertices_logic.select_affected(
                    core_facade, use_mask="_mask_" in action, deselect=deselect)
                verb = "Deselected" if deselect else "Selected"
                return {"status": "FINISHED", "message": f"{verb} {count} vertices"}
            if action == "select_exceeded":
                return self._execute_select_exceeded(core_facade)
            if action == "limit_total":
                return self._execute_limit_total(core_facade)
            if action == "normalize":
                return self._execute_normalize(core_facade)
            return {"status": "CANCELLED", "message": f"Unknown action: {action}"}
        except ValueError as e:
            core_facade.debug_log(
                "feature_domains", f"weight_toolkit.execute() action={action!r} raised {e!r}",
            )
            return {"status": "CANCELLED", "message": str(e)}

    def _execute_copy_weight(self, action: str, core_facade: CoreFacade) -> dict:
        verb, target, _, *mode = action.split("_")
        clip = copy_weight_logic.layer_weight_clipboard if target == "layer" else copy_weight_logic.bone_weight_clipboard
        if verb == "copy":
            clip.copy(core_facade)
        elif verb == "cut":
            clip.cut(core_facade)
        else:
            clip.paste(core_facade, mode=mode[0].upper())
        return {"status": "FINISHED"}

    def _execute_auto(self, core_facade: CoreFacade) -> dict:
        from .auto_block_weight.logic import gather_auto_bone_data
        from .auto_block_weight import refine_logic, result_cache, soften_logic

        smooth, repeat, rings, point = soften_logic.take_options()

        if core_facade.is_mask_context():
            return {"status": "CANCELLED",
                    "message": "Auto Assign not available in mask mode"}

        obj = core_facade.get_obj()
        arm_obj = next(
            (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None
        )
        if not arm_obj:
            return {"status": "CANCELLED", "message": "No armature modifier found"}

        bpy.context.view_layer.update()

        with core_facade.profile_section("auto_block.gather"):
            bone_data, bone_name_to_name = gather_auto_bone_data(core_facade, arm_obj)

        selected_verts = core_facade.get_selected_verts()

        cache_key = result_cache.make_key(obj, core_facade.get_mesh(), bone_data, selected_verts, rings, point)
        assignment = result_cache.get(cache_key)
        if assignment is None:
            with core_facade.profile_section("auto_block.assign", size=len(selected_verts)):
                mesh_graph = refine_logic.build_mesh(core_facade)
                assignment = refine_logic.assign_bones(mesh_graph, bone_data, selected_verts, rings, point)
            result_cache.put(cache_key, assignment)
            result_cache.extra(cache_key, "mesh", lambda: mesh_graph)

        pool_bone_names = set(bone_name_to_name.values())

        if smooth > 0.0:
            with core_facade.profile_section("auto_block.soften", size=len(assignment)):
                blend = soften_logic.soften_assignment(
                    core_facade, bone_data, assignment, smooth, repeat, cache_key, rings, point
                )
            with core_facade.profile_section("auto_block.write", size=len(blend)):
                _write_pool_blend(core_facade, blend, pool_bone_names)
            return {"status": "FINISHED"}

        layer_dict = core_facade.read_active_layer()
        locks = core_facade.get_bone_locks()

        with core_facade.profile_section("auto_block.normalize", size=len(assignment)):
            for v_idx, best_bone_name in assignment.items():
                others = {
                    n: w for n, w in layer_dict.get(v_idx, {}).items()
                    if n != best_bone_name and n not in pool_bone_names
                }
                if any(w > 0.001 for w in others.values()):
                    locked = {n: w for n, w in others.items() if locks.get(n, False)}
                    best_weight = 1.0 - min(1.0, sum(locked.values()))
                    vert_layer = {n: w for n, w in locked.items() if w >= 0.0001}
                    if best_weight >= 0.0001:
                        vert_layer[best_bone_name] = best_weight
                else:
                    vert_layer = {n: w for n, w in others.items() if w >= 0.0001}
                    vert_layer[best_bone_name] = 1.0
                layer_dict[v_idx] = vert_layer

        with core_facade.profile_section("auto_block.write", size=len(assignment)):
            core_facade.write_active_layer_touched(layer_dict, set(assignment.keys()))
        return {"status": "FINISHED"}

    def _execute_select_exceeded(self, core_facade: CoreFacade) -> dict:
        max_influences = bpy.context.window_manager.superskin_limit_total_max_influences
        count = limit_total_logic.select_exceeded(core_facade, max_influences)
        if count:
            core_facade.show_toast(f"Selected {count} vertex(es) exceeding {max_influences} influences")
        else:
            core_facade.show_toast(f"No vertices exceed {max_influences} influences")
        return {"status": "FINISHED"}

    def _execute_limit_total(self, core_facade: CoreFacade) -> dict:
        max_influences = bpy.context.window_manager.superskin_limit_total_max_influences
        count = limit_total_logic.limit_total(core_facade, max_influences)
        if count:
            core_facade.show_toast(f"Clamped {count} vertex(es) to {max_influences} influences")
        else:
            core_facade.show_toast(f"No vertices exceeded {max_influences} influences")
        return {"status": "FINISHED"}

    def _execute_normalize(self, core_facade: CoreFacade) -> dict:
        count = limit_total_logic.normalize(core_facade)
        if count:
            core_facade.show_toast(f"Normalized {count} vertex(es)")
        else:
            core_facade.show_toast("Weights are already normalized")
        return {"status": "FINISHED"}


    def draw_section(self, layout, context) -> None:
        pass


    def populate(self, data: dict) -> None:
        mirror = bpy.context.window_manager.superskin_mirror_prefs
        mirror.mirror_axis = data.get("mirror_axis", "X")
        mirror.direction   = data.get("direction",   "POS_NEG")
        mirror.mirror_data = data.get("mirror_data", "BOTH")

        sr_coll = mirror.search_replace_pairs
        sr_coll.clear()
        for pair in data.get("search_replace_pairs", []):
            item = sr_coll.add()
            item.search_text  = pair[0]
            item.replace_text = pair[1]

    def serialize_into(self, full_dict: dict) -> None:
        mirror = bpy.context.window_manager.superskin_mirror_prefs
        full_dict["mirror"] = {
            "mirror_axis": mirror.mirror_axis,
            "direction":   mirror.direction,
            "mirror_data": mirror.mirror_data,
            "search_replace_pairs": [
                [p.search_text, p.replace_text]
                for p in mirror.search_replace_pairs
            ],
        }



def register():
    bpy.utils.register_class(SSPrefMirrorSRItem)
    bpy.utils.register_class(SSPrefMirror)
    bpy.types.WindowManager.superskin_mirror_prefs = bpy.props.PointerProperty(
        type=SSPrefMirror, options={'SKIP_SAVE'},
    )
    UnifiedRegistry.register(WeightToolkitFeature())


def unregister():
    UnifiedRegistry.unregister("weight_toolkit")
    try:
        del bpy.types.WindowManager.superskin_mirror_prefs
    except Exception:
        pass
    bpy.utils.unregister_class(SSPrefMirror)
    bpy.utils.unregister_class(SSPrefMirrorSRItem)
