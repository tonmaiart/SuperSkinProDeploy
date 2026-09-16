"""Detects and resolves weight mismatches between SuperSkinPro's layer
storage and Blender's native deform Vertex Groups.

A user can leave SuperSkinPro's own Edit Layer Weight mode and paint the
mesh's real deform Vertex Groups directly with Blender's native tools
(Weight Paint mode, the Vertex Groups panel, etc.). SuperSkinPro's layer
system never observes those edits -- it only reads/writes its own
`ss_layer_N` storage -- so the next `finish()` call during a fresh Edit
Layer Weight session would silently overwrite them with the layer stack's
own composited result. This module lets `ops_scene_modes.py` detect that
situation on Enter and ask the user which side should win.

See docs/domains/controller.md's "Native Weight Guard" section for the full
design, the two exit-side recording call sites, and known scope limits.
"""

import hashlib

from ...core.facade import CoreFacade

_SIGNATURE_ID_KEY = "ssp_native_guard_sig"
_WEIGHT_EPSILON = 1e-5


def _real_weight_snapshot(obj) -> list:
    """Sorted `(v_idx, bone_name, weight)` rows describing the mesh's
    current real deform Vertex Group weights. Excludes SuperSkinPro's own
    temp VGs (`__ssp_*`) -- these should already be deleted by the time this
    runs, but the prefix is skipped defensively regardless. Weights are
    rounded to 5 decimals so harmless floating-point recompute noise (e.g.
    non-deterministic thread-interleaving order in a parallel Rust
    reduction) never reads as a "native edit"."""
    vg_names = {vg.index: vg.name for vg in obj.vertex_groups if not vg.name.startswith("__ssp_")}
    if not vg_names:
        return []
    rows = []
    for v in obj.data.vertices:
        for g in v.groups:
            name = vg_names.get(g.group)
            if name is None:
                continue
            w = round(g.weight, 5)
            if w <= _WEIGHT_EPSILON:
                continue
            rows.append((v.index, name, w))
    rows.sort()
    return rows


def compute_deform_signature(obj) -> str:
    """Hashes the mesh's current real deform Vertex Group weights into a
    short, stable signature string."""
    rows = _real_weight_snapshot(obj)
    payload = "|".join(f"{v}:{n}:{w}" for v, n, w in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_flatten_signature(obj) -> None:
    """Snapshots the mesh's current real deform weights as the new "last
    known SuperSkinPro-authored state", stored as a plain custom ID property
    on the mesh datablock (survives save/undo like any other mesh data).
    Call this right after SuperSkinPro itself finishes writing the real
    Vertex Groups (end of `_exit_edit_mode()` / `_do_auto_save()`), never
    mid-edit."""
    sig = compute_deform_signature(obj)
    obj.data[_SIGNATURE_ID_KEY] = sig
    CoreFacade.debug_log(
        "adhoc:native_weight_guard",
        f"record_flatten_signature(): obj={obj.name!r} mesh={obj.data.name!r} "
        f"sig={sig[:12]}... row_count={len(_real_weight_snapshot(obj))}",
    )


def has_native_mismatch(obj) -> bool:
    """True if the mesh's current real deform weights differ from the
    signature recorded at the last SuperSkinPro-authored write -- i.e.
    something other than SuperSkinPro (almost always native Weight Paint /
    Vertex Groups editing) has changed them since. Returns False if no
    baseline has ever been recorded (a mesh that has never completed an Edit
    Layer Weight session yet) -- there is nothing to compare against."""
    if obj is None or obj.type != 'MESH':
        CoreFacade.debug_log(
            "adhoc:native_weight_guard",
            f"has_native_mismatch(): obj={obj!r} -- not a mesh, returning False",
        )
        return False
    stored = obj.data.get(_SIGNATURE_ID_KEY)
    if not stored:
        CoreFacade.debug_log(
            "adhoc:native_weight_guard",
            f"has_native_mismatch(): obj={obj.name!r} mesh={obj.data.name!r} -- "
            f"no stored baseline yet (stored={stored!r}), returning False",
        )
        return False
    current = compute_deform_signature(obj)
    mismatch = stored != current
    CoreFacade.debug_log(
        "adhoc:native_weight_guard",
        f"has_native_mismatch(): obj={obj.name!r} mesh={obj.data.name!r} "
        f"stored={stored[:12]}... current={current[:12]}... mismatch={mismatch}",
    )
    return mismatch


def import_native_into_active_layer(context, obj) -> None:
    """Overwrites the active layer's stored weights with the mesh's current
    real deform Vertex Group weights ("Keep Blender Native Weights"). Must
    be called from Object Mode, before `_enter_edit_mode()` loads the (still
    stale) active layer into temp VGs.

    Caveat: if the active layer is not the only visible layer in the stack,
    the compositor's next flatten blends this imported data with every
    other visible layer above/below it, so the mesh's real weights after
    that flatten are not guaranteed to exactly reproduce the native paint
    that was just imported -- only the active layer's own contribution
    does. This matches the pre-existing single-layer editing model (every
    weight tool only ever edits the active layer), not a limitation new to
    this guard.
    """
    layer_dict = {}
    for v_idx, name, w in _real_weight_snapshot(obj):
        layer_dict.setdefault(v_idx, {})[name] = w
    facade = CoreFacade(context)
    facade.write_layer_dict(layer_dict)
    facade.finish(color_only=False)
