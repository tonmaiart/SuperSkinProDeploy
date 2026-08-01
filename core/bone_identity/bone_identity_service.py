"""BoneIdentityService — stable bone-UUID backfill, orphan scanning, and
explicit bone-weight delete for the active mesh.

Mirrors UIController's "instantiate per-operation" shape. The orphan scan
itself is a signature-gated cache (``scan_readonly``) rather than something
a caller must remember to explicitly trigger — an earlier revision scanned
only from specific call sites (entering Edit Mode) and went stale after any
event those call sites didn't cover (undo/redo of a remap/delete was the
one that shipped first). A signature-gated cache mirrors the existing
pattern in ``ui/utils.py``'s ``_get_visible_influence_bones`` (cheap key
check gates the expensive recompute).

As of the bones-list mirror-collection refactor (2026-06), the Deform
Bones list's orphan rows are no longer scanned directly from draw() —
they're read from ``obj.superskin_bones_collection`` (kept current by
``ui.utils.sync_bones_to_ui_collection``, called from the
``depsgraph_update_post`` / ``load_post`` handlers via
``get_scan_for_object``), the same mirror-collection pattern the Layers
list already used for its own JSON-backed metadata. ``scan_readonly`` /
``backfill_and_scan`` stay split because Blender forbids writing to ID
data (mesh custom properties, and the mirror CollectionProperty itself)
from inside a panel ``draw()`` callback — ``backfill_and_scan``'s
bone-UUID-map write raises ``AttributeError: Writing to ID classes in
this context is not allowed`` if called from there.
"""

import json

from ..layer_storage.storage_service import LayerStorageService
from ...core_subsystems.debug_logging import DebugLogService
from . import orphan_resolver

# {mesh_name: [orphan_dict, ...]}
_scan_cache: dict = {}
# {mesh_name: signature} — see _compute_signature
_scan_cache_key: dict = {}

# Temp VG name for previewing an orphan bone's composited weight via
# Blender's native Weight Overlay -- see preview_orphan_weight().
_ORPHAN_PREVIEW_VG = "__ssp_orphan_preview"


def _find_armature(obj):
    return next(
        (m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object),
        None,
    )


def _mapping_has_orphan_ids(storage, obj) -> bool:
    """Cheap check: does this Edit Mode session's bone mapping
    (``__ssp_meta_map``) contain any synthetic (orphan) ID at all?

    Just a small-JSON parse plus a vertex-group-name pass -- no mesh scan.
    Lets ``_read_live_active_layer`` skip its expensive per-vertex read
    entirely for the overwhelmingly common case (no orphans in this
    session), which matters because that read used to run on every
    depsgraph tick, including the several tick fired back-to-back while
    Entering/Exiting Edit Mode creates or removes one ``__ssp_N`` VG per
    bone -- that multiplication was the actual cause of the Enter/Exit lag,
    not the read itself being slow for a normal (no-orphan) mesh.

    real_vg_count comes from LayerStorageService.real_vg_count() -- the
    single authoritative real/orphan boundary, never re-derived inline
    here (see that method's docstring in geometry.py for why).
    """
    real_vg_count = storage.real_vg_count(obj)
    try:
        id_to_bone = json.loads(obj.get("__ssp_meta_map", "{}"))
    except Exception:
        return False
    return any(int(k) >= real_vg_count for k in id_to_bone)


def _read_live_active_layer(storage, obj):
    """Return (active_layer_index, layer_dict) from the live temp VGs while
    an Edit Mode session is loaded AND at least one orphan bone is part of
    this session's mapping, or (None, None) otherwise.

    During Edit Mode, the active layer's true current weights live in
    ``__ssp_*`` temp VGs, not ``ss_layer_N`` -- persistence is deferred to
    Save Weights. Reading this lets orphan detection reflect painting
    changes as they happen instead of only after baking back to storage.

    Gated by ``_mapping_has_orphan_ids()`` first -- see that function's
    docstring for why skipping the expensive scan when there's nothing to
    find is not just an optimization but the fix for a real Enter/Exit
    Edit Mode lag regression.

    Uses read_temp_vgs_from_bm() (one pass over bm.verts, reading each
    vertex's own deform layer entries) instead of read_temp_vgs_to_layer()
    (one full mesh.vertices pass PER __ssp_* VG) whenever obj is actually in
    EDIT mode -- this can still run on every depsgraph tick while painting
    a mesh that does have an orphan, so the per-bone-vs-per-vertex cost
    difference matters here. Falls back to the mesh.vertices-based reader
    for the brief OBJECT-mode window mid-bake where temp VGs still exist
    but there's no edit BMesh to read from.
    """
    from ..layer_storage.temp_vg_bridge import (
        has_temp_vgs, read_temp_vgs_from_bm, read_temp_vgs_to_layer,
        get_active_layer_from_meta,
    )
    if not has_temp_vgs(obj) or not _mapping_has_orphan_ids(storage, obj):
        return None, None
    active_idx = get_active_layer_from_meta(obj)
    if obj.mode == 'EDIT':
        import bmesh
        bm = bmesh.from_edit_mesh(obj.data)
        layer_dict, _, _ = read_temp_vgs_from_bm(bm, obj)
    else:
        layer_dict, _, _ = read_temp_vgs_to_layer(obj)
    return active_idx, layer_dict


def _compute_signature(storage, obj, arm_obj) -> tuple:
    """Cheap signature of everything orphan detection depends on: real
    (non-``__ssp_*``) vertex-group names, every layer's raw weight blob,
    the live armature's bone names, and (only while this Edit Mode
    session's mapping actually includes an orphan) the ``__ssp_deform_gen``
    write counter. A mismatch against the last scan's signature means
    something that could change orphan status actually changed, so it's
    safe to skip the real (more expensive, decode-every-layer, and
    possibly live-BMesh) scan whenever this matches — same trade-off as
    ``_get_visible_influence_bones``'s ``hash(raw_blob)`` key, just
    extended across every layer instead of only the active one, since an
    orphan can hide in any layer.

    Deliberately cheap enough to call on every redraw / depsgraph tick with
    NO mesh scan of any kind:
      - ``__ssp_*`` temp VG names are excluded from ``vg_names`` so
        Entering/Exiting Edit Mode (which creates/removes one ``__ssp_N``
        VG per bone) doesn't spuriously invalidate this every tick of that
        sequence -- that over-invalidation, combined with the live-BMesh
        read `_read_live_active_layer()` used to always perform to build
        this signature, was the actual cause of a real Enter/Exit lag
        regression.
      - ``__ssp_deform_gen`` (bumped once per real weight write by
        write_active_layer_from_calc(), and only then) stands in for the
        live active-layer content instead of hashing it directly, so
        callers only need to actually read the live temp-VG data
        (`_read_live_active_layer()`) on a genuine signature mismatch, not
        on every call just to compute this signature.
    """
    vg_names = tuple(sorted(
        vg.name for vg in obj.vertex_groups if not vg.name.startswith("__ssp_")
    ))
    layer_blobs = tuple(sorted(storage.harvest_layer_data_map().items()))
    arm_names = tuple(sorted(b.name for b in arm_obj.data.bones)) if arm_obj else ()
    deform_gen = obj.get("__ssp_deform_gen", 0) if _mapping_has_orphan_ids(storage, obj) else None
    return (vg_names, hash(layer_blobs), arm_names, deform_gen)


class BoneIdentityService:
    def __init__(self, context, obj=None):
        """*context* may be ``None`` when *obj* is passed explicitly — only
        ``delete_bone`` (which builds a real ``UIController``)
        needs a live ``context``; the read-only scan path doesn't."""
        self.ctx = context
        self.obj = obj if obj is not None else (context.active_object if context else None)
        if not self.obj or self.obj.type != 'MESH':
            raise ValueError("No active mesh object")
        self.storage = LayerStorageService(self.obj.data)
        self.arm_obj = _find_armature(self.obj)

    def scan_readonly(self) -> list:
        """Return the current orphan list without writing anything to mesh
        data — the only variant safe to call from a panel ``draw()``
        callback. Blender raises ``AttributeError: Writing to ID classes
        in this context is not allowed`` on any ID-data write attempted
        during draw (this shipped once: an earlier revision called
        ``backfill_and_scan()``, including its ``write_bone_uuid_map()``
        write, directly from here).

        Signature-gated (see ``_compute_signature``) against the same
        cache ``backfill_and_scan()`` populates, so this only recomputes
        when something that could change orphan status actually changed
        since the last scan by *either* method — cheap enough to call on
        every redraw.
        """
        key = self.obj.data.name
        sig = _compute_signature(self.storage, self.obj, self.arm_obj)
        if _scan_cache_key.get(key) == sig and key in _scan_cache:
            return _scan_cache[key]

        live_override = _read_live_active_layer(self.storage, self.obj)
        result = orphan_resolver.scan_orphans(
            self.storage, self.obj, self.arm_obj, live_override=live_override,
        )
        _scan_cache[key] = result
        _scan_cache_key[key] = sig
        return result

    def backfill_and_scan(self) -> list:
        """Scan for orphans AND refresh the bone-UUID map (a write) — only
        call this from an operator or app-handler, NEVER from draw() (see
        ``scan_readonly``'s docstring for why). Always does the real work
        when called instead of trusting the signature cache, since this is
        only invoked from infrequent write-context triggers (e.g.
        entering Edit Mode), not every redraw, so there's no per-redraw
        cost to gate — and skipping the write whenever a draw-time
        ``scan_readonly()`` happened to already match the signature would
        mean the uuid map might never get backfilled at all.

        Uses the uuid map as it stood at the END of the PREVIOUS backfill,
        THEN refreshes the map to the current live names for next time.
        Ordering matters: classifying an orphan as "renamed" depends on
        the map still holding the bone's PREVIOUSLY known name at scan
        time. Refreshing the map first would overwrite that old name with
        the live one before the comparison ever runs, making every rename
        look identical to a deletion — the same baseline-computed-after-
        the-mutation mistake documented in docs/bug-history/0011.
        """
        live_override = _read_live_active_layer(self.storage, self.obj)
        result = orphan_resolver.scan_orphans(
            self.storage, self.obj, self.arm_obj, live_override=live_override,
        )
        orphan_resolver.backfill_uuid_map(self.storage, self.obj, self.arm_obj)
        key = self.obj.data.name
        _scan_cache[key] = result
        _scan_cache_key[key] = _compute_signature(self.storage, self.obj, self.arm_obj)
        return result

    def delete_bone(self, source_name: str, layer_index: int = None):
        """Remove *source_name*'s weight entirely, across every layer (or
        just *layer_index* if given)."""
        from ..facade import CoreFacade
        facade = CoreFacade(self.ctx)
        meta_list = self.storage.read_meta_list()
        orphan_resolver.delete_bone_weights(
            self.storage, meta_list, source_name, layer_index=layer_index
        )
        facade.finish()

    def preview_orphan_weight(self, orphan_name: str):
        """Point the native Weight Overlay at *orphan_name*'s weight, even
        though an orphan has no real VertexGroup of its own. Called from
        ``SUPERSKIN_OT_select_orphan_bone_row`` on row selection.

        Two branches, since the active layer lives in a different place
        depending on mode:

        Object Mode: composites *orphan_name*'s weight across all visible
        layers (exactly like flatten_visible_layers_to_mesh() does for a
        real bone) into a dedicated ``__ssp_orphan_preview`` temp VG, then
        sets that as the native active VG.

        Edit Mode: the orphan already has its own ``__ssp_N`` temp VG (via
        get_unified_mapping()'s synthetic ID, wired into
        _load_active_layer_to_temp_vgs() in ops_scene_modes.py) holding the
        *active layer's* weight for it -- no new VG or weight write needed,
        just look its index up from ``__ssp_meta_map`` and point
        vertex_groups.active_index there.
        """
        if self.obj.mode == 'EDIT':
            self._preview_orphan_weight_edit_mode(orphan_name)
            return

        weights = orphan_resolver.composite_orphan_weight(self.storage, self.obj, orphan_name)

        vg = self.obj.vertex_groups.get(_ORPHAN_PREVIEW_VG)
        num_verts = len(self.obj.data.vertices)
        if vg is not None:
            vg.remove(range(num_verts))
        else:
            vg = self.obj.vertex_groups.new(name=_ORPHAN_PREVIEW_VG)

        for v_idx, w in weights.items():
            vg.add([v_idx], w, 'REPLACE')

        self.obj.vertex_groups.active_index = vg.index

        DebugLogService.log(
            "bone_id",
            f"preview_orphan_weight(): obj={self.obj.name!r} orphan_name={orphan_name!r} "
            f"OBJECT mode -- preview_vg_index={vg.index} weighted_verts={len(weights)}",
        )

    def _preview_orphan_weight_edit_mode(self, orphan_name: str):
        weights_map_raw = self.obj.get("__ssp_meta_map", "{}")
        try:
            id_to_bone = {int(k): v for k, v in json.loads(weights_map_raw).items()}
        except Exception:
            id_to_bone = {}

        vg_idx = next((k for k, v in id_to_bone.items() if v == orphan_name), None)
        temp_vg = self.obj.vertex_groups.get(f"__ssp_{vg_idx}") if vg_idx is not None else None

        DebugLogService.log(
            "bone_id",
            f"preview_orphan_weight(): obj={self.obj.name!r} orphan_name={orphan_name!r} "
            f"EDIT mode -- ssp_meta_map has {len(id_to_bone)} entries, resolved "
            f"vg_idx={vg_idx!r} temp_vg_found={temp_vg is not None}",
        )

        if temp_vg is not None:
            self.obj.vertex_groups.active_index = temp_vg.index

    def clear_orphan_weight_preview(self):
        """Remove the ``__ssp_orphan_preview`` temp VG, if present. Call when
        the Deform Bones list selection moves away from an orphan row (a
        real bone or the Mask row gets selected instead) so the preview
        doesn't linger as a stale, invisible VG on the mesh."""
        vg = self.obj.vertex_groups.get(_ORPHAN_PREVIEW_VG)
        if vg is not None:
            self.obj.vertex_groups.remove(vg)

    @staticmethod
    def get_scan_for_object(obj) -> list:
        """Single source of truth for orphan rows, driven by *obj* alone —
        for handler-driven callers (``ui.utils.sync_bones_to_ui_collection``,
        invoked from ``depsgraph_update_post`` / ``load_post``) that iterate
        every SuperSkinPro-managed mesh in the scene, not just whichever
        one happens to be ``context.active_object`` at that moment. Routes
        through the signature-gated ``scan_readonly()`` / ``_scan_cache``,
        so it recomputes automatically whenever vertex-group names, any
        layer's weight data, or the live armature's bone set actually
        changed since the last scan — entering Edit Mode, undo/redo, and a
        live bone rename all just work without a dedicated call site for
        each, and repeated calls for the same unchanged mesh are cheap."""
        if not obj or obj.type != 'MESH':
            return []
        try:
            return BoneIdentityService(None, obj=obj).scan_readonly()
        except ValueError:
            return []

    @staticmethod
    def clear_scan_cache():
        _scan_cache.clear()
        _scan_cache_key.clear()
