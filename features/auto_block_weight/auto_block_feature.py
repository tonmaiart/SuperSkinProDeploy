"""AutoBlockFeature — Unified Component Architecture for auto block weight assignment.

Collapses the old AutoBlockDomain (action dispatch) and inline PrefsExtensionSpec
into a single UnifiedFeatureExtension subclass.

Owns:
  - Action dispatch: "auto"
  - UI layout: delegates to .ui.draw_section()
  - JSON persistence: no-op (this domain has no user-editable prefs)
"""

import array
import os

from ...interface.registry.register_api import UnifiedFeatureExtension, UnifiedRegistry
from ...core.facade import CoreFacade
from . import ui

_DEFAULTS_PATH = os.path.join(os.path.dirname(__file__), "default_config.json")


# ==============================================================================
# AutoBlockFeature — UnifiedFeatureExtension
# ==============================================================================

class AutoBlockFeature(UnifiedFeatureExtension):
    """Unified extension for the Auto Block Weight domain."""

    # ── Configuration (class attributes) ───────────────────────────────────

    domain_id = "auto_block"
    actions = ["auto"]
    section_title = "Auto Assign"
    draw_tab = "SKINNING"
    defaults_path = _DEFAULTS_PATH
    json_path = ("auto_block_weight",)
    priority = 2
    expanded_by_default = True
    locked_expanded = True

    # ── Action dispatch ───────────────────────────────────────────────────

    def execute(self, action: str, context, core_facade: CoreFacade) -> dict:
        from .logic import apply, gather_auto_bone_data

        if core_facade.is_mask_context():
            return {"status": "CANCELLED",
                    "message": "Auto Assign not available in mask mode"}

        obj = core_facade.get_obj()
        arm_obj = next(
            (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None
        )
        if not arm_obj:
            return {"status": "CANCELLED", "message": "No armature modifier found"}

        with core_facade.profile_section("auto_block.gather"):
            bone_data, bone_name_to_name = gather_auto_bone_data(core_facade, arm_obj)

        ctrl = core_facade.get_ctrl()
        mat = obj.matrix_world
        selected_verts = ctrl.get_selected_verts()
        mesh_verts = ctrl.mesh.vertices
        # Only transform the selected subset to world space -- the previous
        # version built a world coordinate for every vertex in the mesh
        # (`for v in ctrl.mesh.vertices`) even though Rust only ever looked
        # up the selected ones, so a small selection on a dense mesh paid
        # for a full-mesh Python loop for no reason. Now writes straight
        # into a flat `array.array('d', ...)` buffer instead of building an
        # intermediate list of (x, y, z) tuples -- see logic.py::apply()'s
        # docstring for why this shape crosses the FFI more cheaply.
        with core_facade.profile_section("auto_block.world_coords", size=len(selected_verts)):
            selected_world_coords_flat = array.array("d", [0.0]) * (len(selected_verts) * 3)
            for out_i, v_idx in enumerate(selected_verts):
                co = mat @ mesh_verts[v_idx].co
                base = out_i * 3
                selected_world_coords_flat[base] = co.x
                selected_world_coords_flat[base + 1] = co.y
                selected_world_coords_flat[base + 2] = co.z

        with core_facade.profile_section("auto_block.rust_ffi", size=len(selected_verts)):
            assignment = apply(
                selected_verts=selected_verts,
                selected_world_coords_flat=selected_world_coords_flat,
                bone_data=bone_data,
                bone_name_to_name=bone_name_to_name,
            )

        layer_dict = core_facade.read_active_layer()

        pool_bone_names = set(bone_name_to_name.values())

        with core_facade.profile_section("auto_block.normalize", size=len(assignment)):
            for v_idx, best_bone_name in assignment.items():
                vert_layer = layer_dict.setdefault(v_idx, {})
                # Only clear pool bones actually present on this vertex instead
                # of blindly popping every pool bone name from every vertex --
                # a vertex is usually influenced by a handful of bones, while
                # the pool can be the whole armature's selection.
                stale = vert_layer.keys() & pool_bone_names
                stale.discard(best_bone_name)
                for b_name in stale:
                    del vert_layer[b_name]
                vert_layer[best_bone_name] = 1.0
                # Skip the normalize_weights() round-trip when best_bone_name
                # is now the ONLY entry at this vertex -- the sum of unlocked
                # weights is already exactly 1.0, so redistribution is a
                # guaranteed no-op (matches normalize_weights()'s own
                # has_other_weights fast path in
                # core_subsystems/context_selection_service.py, checked here
                # first to avoid paying the facade/core-subsystem call
                # overhead 14000+ times over on a large selection when it's
                # provably unnecessary -- confirmed via profiler: this call
                # alone was ~1.57s of a ~14,347-vertex Auto Assign before
                # this guard, dwarfing every other phase combined).
                if len(vert_layer) > 1:
                    layer_dict = core_facade.normalize_weights(
                        layer_dict, v_idx, best_bone_name
                    )

        # `dirty_verts` restricts finish()'s post-composite BMesh write-back
        # loop to the vertices Auto Assign actually touched (assignment's own
        # keys) instead of the whole mesh -- `layer_dict` itself stays the
        # complete, current active layer (read via `read_active_layer()`
        # above, mutated in place), so this only trims the write-back
        # iteration, never the compositor's input. See CoreFacade.write_active_layer()'s
        # `dirty_verts` contract in `core/facade/README.md`.
        with core_facade.profile_section("auto_block.write", size=len(assignment)):
            core_facade.write_active_layer(
                layer_dict, color_only=True, dirty_verts=set(assignment.keys())
            )
        return {"status": "FINISHED"}

    # ── UI layout ─────────────────────────────────────────────────────────

    def draw_section(self, layout, context) -> None:
        """Draw the Auto Assign section — delegates to .ui.draw_section()."""
        ui.draw_section(layout)

    # ── JSON persistence ──────────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass


# ==============================================================================
# Registration (called from __init__.py)
# ==============================================================================

def register():
    """Register with UnifiedRegistry."""
    UnifiedRegistry.register(AutoBlockFeature())


def unregister():
    """Unregister from UnifiedRegistry."""
    UnifiedRegistry.unregister("auto_block")
