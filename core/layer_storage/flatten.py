
from ...core_subsystems.layer_compositor import LayerCompositor
from ...core_subsystems.debug_logging import DebugLogService
from ...core_subsystems.profiler import ProfilerService
from ...core_subsystems.profiler.profiler_service import profile_section
from .bmesh_io import write_deform
from .geometry import get_local_mapping


def _log_compositor_loss(obj, meta, layer_data_map, result):
    if not ProfilerService.is_enabled():
        return

    meta_indices = {int(l["index"]) for l in meta}
    storage_indices = set(layer_data_map.keys())
    orphaned_layer_slots = sorted(storage_indices - meta_indices)
    invisible_layers = sorted(
        int(l["index"]) for l in meta if not l.get("visible", True)
    )

    input_totals = {}
    for raw in layer_data_map.values():
        decoded = LayerCompositor.decode(raw)
        for weights in decoded.values():
            for name, w in weights.items():
                input_totals[name] = input_totals.get(name, 0.0) + w

    output_totals = {}
    for bone_weights in result.values():
        for name, w in bone_weights.items():
            output_totals[name] = output_totals.get(name, 0.0) + w

    dropped = sorted(n for n in input_totals if n not in output_totals)
    collapsed = sorted(
        n for n, total in input_totals.items()
        if n in output_totals and total > 0.5 and output_totals[n] < 0.01
    )
    DebugLogService.log(
        "core_pipeline",
        f"flatten_visible_layers_to_mesh(): obj={obj.name!r} "
        f"meta_layer_indices={sorted(meta_indices)!r} "
        f"storage_layer_indices={sorted(storage_indices)!r} "
        f"orphaned_layer_slots(in storage, no meta entry)={orphaned_layer_slots!r} "
        f"invisible_layers={invisible_layers!r} "
        f"dropped_bones(missing from result)={dropped!r} "
        f"collapsed_bones(near-zero result, storage was substantial)={collapsed!r}",
    )


def flatten_visible_layers_to_mesh(storage, obj):
    if not obj or obj.type != 'MESH' or not storage.has_layer_system():
        return

    mesh = obj.data
    vg_list = obj.vertex_groups
    num_verts = len(mesh.vertices)

    if num_verts == 0 or len(vg_list) == 0:
        return

    with profile_section("core.flatten.harvest", num_verts):
        meta = storage.read_meta_list()
        name_to_idx, idx_to_name = get_local_mapping(obj)

        layer_data_map = storage.harvest_layer_data_map()
        mask_data_map = storage.harvest_mask_data_map()

    with profile_section("core.flatten.composite", num_verts):
        result = LayerCompositor.composite_layers(meta, layer_data_map, mask_data_map,
                                                   idx_to_name, num_verts)

    with profile_section("core.flatten.log_loss", num_verts):
        _log_compositor_loss(obj, meta, layer_data_map, result)

    _write_result_via_bmesh(obj, result, name_to_idx)


_MIN_WRITTEN_WEIGHT = 0.001


def _write_result_via_bmesh(obj, result, name_to_idx):
    managed_idx = frozenset(name_to_idx.values())
    if not managed_idx:
        return
    with profile_section("core.flatten.write_bmesh", len(result)):
        with write_deform(obj.data) as (bm, deform):
            for bv in bm.verts:
                dvert = bv[deform]
                for group in [g for g in dvert.keys() if g in managed_idx]:
                    del dvert[group]
                bone_weights = result.get(bv.index)
                if not bone_weights:
                    continue
                for g_name, w in bone_weights.items():
                    group = name_to_idx.get(g_name)
                    if group is not None and w > _MIN_WRITTEN_WEIGHT:
                        dvert[group] = w
