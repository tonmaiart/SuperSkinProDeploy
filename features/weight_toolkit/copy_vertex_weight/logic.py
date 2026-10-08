
from ....core.facade import CoreFacade as _CoreFacade

data_ops = _CoreFacade.get_clipboard_data_ops()


class _ClipboardManager:
    def __init__(self):
        self._clip = None

    def has_clipboard(self) -> bool:
        return self._clip is not None and "data" in self._clip and bool(self._clip["data"])

    def get_clipboard(self) -> dict:
        if not self.has_clipboard():
            raise ValueError("Clipboard is empty — copy a vertex first")
        return self._clip

    def set_clipboard(self, kind: str, data: dict, source_mesh: str, active_bone: str = ""):
        self._clip = {"kind": kind, "data": data, "source_mesh": source_mesh, "active_bone": active_bone}


_clipboard = _ClipboardManager()


def _copy_impl(ctrl, is_mask: bool) -> dict:
    selected = ctrl.get_selected_verts()
    all_verts_count = len(ctrl.mesh.vertices)

    if len(selected) == 0 or len(selected) == all_verts_count:
        if is_mask:
            mask_dict = ctrl.get_active_mask_dict()
            subset = {str(k): v for k, v in mask_dict.items()}
        else:
            layer_dict = ctrl.read_active_layer()
            subset = {str(k): dict(w) for k, w in layer_dict.items()}
    else:
        if is_mask:
            mask_dict = ctrl.get_active_mask_dict()
            subset = data_ops.extract_mask_subset(mask_dict, selected)
        else:
            layer_dict = ctrl.read_active_layer()
            subset = data_ops.extract_weight_subset(layer_dict, selected)

    if not subset:
        raise ValueError("Nothing to copy — selected vertices have no data.")

    kind = 'MASK' if is_mask else 'WEIGHT'
    active_bone = "" if is_mask else ctrl.get_active_vg_name()
    _clipboard.set_clipboard(kind, subset, ctrl.mesh.name, active_bone)
    return _clipboard.get_clipboard()


def copy_single_vertex(ctrl) -> dict:
    selected = ctrl.get_selected_verts()
    if len(selected) != 1:
        raise ValueError("Select exactly one vertex to copy its influence")
    return _copy_impl(ctrl, ctrl.is_mask_context())


def _convert_mask_to_weight(mask_data: dict, ctrl) -> dict:
    active_bone_name = ctrl.get_active_vg_name()
    if not active_bone_name:
        raise ValueError("No active Vertex Group selected for conversion")
    return {v: {active_bone_name: float(val)} for v, val in mask_data.items()}


def _convert_weight_to_mask(weight_data: dict, bone_name: str) -> dict:
    if not bone_name:
        raise ValueError(
            "No active bone was selected when copying — select a bone row, "
            "copy again, then paste onto the Mask row."
        )
    return {v: float(weights.get(bone_name, 0.0)) for v, weights in weight_data.items()}


def paste_vertex(ctrl) -> dict:
    clip = _clipboard.get_clipboard()
    clip_data = clip["data"]
    clip_kind = clip["kind"]
    source_mesh_name = clip["source_mesh"]

    is_mask_target = ctrl.is_mask_context()
    all_verts_count = len(ctrl.mesh.vertices)
    selected_targets = ctrl.get_selected_verts()
    target_verts = selected_targets if len(selected_targets) > 0 else list(range(all_verts_count))

    paste_kind = clip_kind
    paste_data = clip_data

    if clip_kind == 'WEIGHT' and is_mask_target:
        paste_kind = 'MASK'
        paste_data = _convert_weight_to_mask(clip_data, clip.get("active_bone", ""))
    else:
        target_vg_names = {vg.name for vg in ctrl.obj.vertex_groups}
        ok, reason = data_ops.validate_bone_compatibility(clip_data, target_vg_names, clip_kind)
        if not ok:
            raise ValueError(reason)

    if clip_kind == 'MASK' and not is_mask_target:
        paste_kind = 'WEIGHT'
        paste_data = _convert_mask_to_weight(paste_data, ctrl)

    current_mesh_name = ctrl.mesh.name
    if paste_kind == 'WEIGHT':
        resolved = data_ops.resolve_paste_targets_weight(
            paste_data, target_verts, source_mesh_name, current_mesh_name,
        )
        layer_dict = {int(k): v for k, v in ctrl.read_active_layer().items()}
        for v_int, bone_weights in resolved.items():
            layer_dict[v_int] = {bone_name: float(w) for bone_name, w in bone_weights.items()}
        ctrl.write_active_layer(layer_dict, color_only=False)
    else:
        resolved = data_ops.resolve_paste_targets_mask(
            paste_data, target_verts, source_mesh_name, current_mesh_name,
        )
        mask_dict = {int(k): v for k, v in ctrl.get_active_mask_dict().items()}
        for v_int, val in resolved.items():
            mask_dict[v_int] = float(val)
        ctrl.write_mask_dict(mask_dict)
        ctrl.finish(color_only=False)
    return {"status": "FINISHED"}
