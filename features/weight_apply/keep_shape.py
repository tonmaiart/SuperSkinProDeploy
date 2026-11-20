
import numpy as np

from ...core.facade import CoreFacade
from .stroke_skin import primary_armature

_STILL = 1e-6


def skinning_matrices(obj, bone_ids, id_to_bone):
    md = primary_armature(obj)
    if md is None:
        return None
    arm = md.object
    pre = arm.matrix_world.inverted() @ obj.matrix_world
    post = pre.inverted()
    ids, mats = [], []
    posed = False
    for b in sorted(bone_ids):
        pb = arm.pose.bones.get(id_to_bone.get(b, ""))
        if pb is None or not pb.bone.use_deform:
            continue
        m = np.array(post @ pb.matrix @ pb.bone.matrix_local.inverted() @ pre, dtype=np.float64)
        posed = posed or np.abs(m - np.eye(4)).max() > _STILL
        ids.append(b)
        mats.append(m[:3].ravel())
    if not posed:
        return None
    return np.asarray(ids, dtype=np.int32), np.ascontiguousarray(np.concatenate(mats))


def shape_inputs(core_facade, ctx, coords, flat, welds, bind):
    from .logic import posed_geometry_flat
    posed = ctx.get("_posed_cache")
    if posed is None:
        posed = ctx["_posed_cache"] = posed_geometry_flat(core_facade, len(coords))
    if not len(posed[0]):
        return None
    origin = ctx.get("_shape_origin")
    if origin is None or origin[0] is not posed:
        obj = core_facade.get_obj()
        origin = ctx["_shape_origin"] = (
            posed, ctx["layer_int"].copy(), skinning_matrices(obj, ctx["id_to_bone"].keys(), ctx["id_to_bone"]),
            core_facade.get_flattened_mask_dict(core_facade.get_active_layer_index()) or {},
        )
    if origin[2] is None:
        return None
    extra = ctx.get("_shape_extra")
    if extra is None or extra[0] is not coords or extra[1] is not welds:
        extra = ctx["_shape_extra"] = (coords, welds, _extra_pairs(core_facade, coords, welds))
    extra = extra[2]
    return {"coords": coords, "flat": flat, "posed": posed[0], "origin": origin[1],
            "matrices": origin[2], "coverage": origin[3], "extra": extra, "bind": bind, "welds": welds,
            "layer": ctx["layer_int"], "gateway": core_facade.get_rust_gateway("keep_shape")}


def _extra_pairs(core_facade, coords, welds):
    from .brush_tool.brush_logic import shell_pairs
    from .logic import weld_members
    a, b = shell_pairs(core_facade, coords, core_facade.get_obj().data)
    heads, tails = [a], [b]
    if welds:
        for group in weld_members(welds).values():
            if len(group) > 1:
                heads.append(np.asarray(group[:-1], dtype=np.int64))
                tails.append(np.asarray(group[1:], dtype=np.int64))
    return (np.ascontiguousarray(np.concatenate(heads), dtype=np.int64),
            np.ascontiguousarray(np.concatenate(tails), dtype=np.int64))


def _bound_pairs(shape, free):
    from .brush_tool.brush_logic import _bind_pairs
    if not shape["bind"]:
        return None
    a, b = _bind_pairs(shape["bind"], shape["coords"])
    keep = np.isin(a, free) & np.isin(b, free)
    return (a[keep], b[keep]) if keep.any() else None


def _coverage(shape, verts):
    flattened = shape["coverage"]
    if not flattened:
        return np.ones(len(verts))
    get = flattened.get
    return np.fromiter((float(get(v, 0.0)) for v in verts.tolist()), dtype=np.float64, count=len(verts))


def _grow(verts, offsets, targets):
    starts = offsets[verts]
    lengths = offsets[verts + 1] - starts
    skip = np.concatenate(([0], np.cumsum(lengths)[:-1]))
    return np.union1d(verts, targets[np.repeat(starts - skip, lengths) + np.arange(int(lengths.sum()))])


def _columns(rows):
    coo = getattr(rows, "coo", None)
    if coo is None:
        coo = CoreFacade.layer_to_coo(rows)
    v, b, w = (np.asarray(x) for x in coo)
    keep = v >= 0
    return v[keep].astype(np.int64), b[keep].astype(np.int32), w[keep].astype(np.float64)


def pull_to_shape(shape, diff, write, locks_id, share, affected=False):
    dv, db, dw = _columns(diff)
    written = np.fromiter(write, dtype=np.int64, count=len(write))
    free = np.intersect1d(dv, written)
    amounts = _amounts(share, free)
    free = free[amounts > 0.0]
    amounts = amounts[amounts > 0.0]
    if not len(free):
        return diff
    top = float(amounts.max())
    rest_flat, offsets, targets = shape["flat"]
    around = _grow(_grow(free, offsets, targets), offsets, targets)
    before = _columns(CoreFacade.layer_slice(shape["layer"], around.tolist(), ascending=True))
    origin = _columns(CoreFacade.layer_slice(shape["origin"], around.tolist(), ascending=True))
    mine = np.isin(dv, free)
    after = dv[mine], db[mine], dw[mine]
    matrices = shape["matrices"]
    heads, tails = shape["extra"]
    bound = _bound_pairs(shape, free)
    if bound is not None:
        heads, tails = np.concatenate([heads, bound[0]]), np.concatenate([tails, bound[1]])
    locked = [b for b, is_locked in locks_id.items() if is_locked]
    as_i64 = lambda x: np.ascontiguousarray(x, dtype=np.int64)
    as_f64 = lambda x: np.ascontiguousarray(x, dtype=np.float64)
    out_v, out_b, out_w = shape["gateway"].call(
        "rust_keep_shape", as_f64(rest_flat), as_f64(shape["posed"]), as_i64(offsets), as_i64(targets),
        as_i64(heads), as_i64(tails), as_i64(free), *before, *after, *origin,
        around, _coverage(shape, around), matrices[0], matrices[1], locked, top,
    )
    out_v = np.asarray(out_v, dtype=np.int64)
    if not len(out_v):
        return diff
    out_b, out_w = np.asarray(out_b, dtype=np.int32), np.asarray(out_w, dtype=np.float64)
    if affected:
        out_v, out_b, out_w = _held_bones_only(out_v, out_b, out_w, (dv, db, dw), locks_id)
    if amounts.min() < top:
        out_v, out_b, out_w = _blend(out_v, out_b, out_w, (dv, db, dw),
                                     amounts[np.searchsorted(free, np.unique(out_v))] / top)
    kept = ~np.isin(dv, out_v)
    v, b, w = (np.concatenate([dv[kept], out_v]), np.concatenate([db[kept], out_b]),
               np.concatenate([dw[kept], out_w]))
    return CoreFacade.coo_to_layer(*_join_seams(shape, write, locks_id, v, b, w))


def _amounts(share, verts):
    if isinstance(share, dict):
        get = share.get
        out = np.fromiter((float(get(v, 0.0)) for v in verts.tolist()), dtype=np.float64, count=len(verts))
    else:
        out = np.full(len(verts), float(share))
    return np.clip(out, 0.0, 1.0)


def _held_bones_only(v, b, w, before, locks_id):
    bv, bb, _bw = before
    key = v * (1 << 32) + b.astype(np.int64)
    held = np.isin(key, bv * (1 << 32) + bb.astype(np.int64))
    locked = np.isin(b, [k for k, is_locked in locks_id.items() if is_locked])
    keep = held | locked
    rows, at = np.unique(v, return_inverse=True)
    free_total = np.bincount(at, weights=np.where(locked, 0.0, w), minlength=len(rows))
    kept_total = np.bincount(at, weights=np.where(keep & ~locked, w, 0.0), minlength=len(rows))
    scale = np.divide(free_total, kept_total, out=np.zeros_like(free_total), where=kept_total > 1e-12)
    w = np.where(locked, w, w * scale[at])
    return v[keep], b[keep], w[keep]


def _blend(v, b, w, before, factors):
    bv, bb, bw = before
    rows = np.unique(v)
    mine = np.isin(bv, rows)
    allv = np.concatenate([v, bv[mine]])
    allb = np.concatenate([b, bb[mine]])
    at = np.searchsorted(rows, allv)
    f = factors[at]
    allw = np.concatenate([w, bw[mine]]) * np.concatenate([f[:len(v)], 1.0 - f[len(v):]])
    key = allv * (1 << 32) + allb.astype(np.int64)
    keys, inverse = np.unique(key, return_inverse=True)
    summed = np.bincount(inverse, weights=allw, minlength=len(keys))
    live = summed > 1e-12
    return (keys[live] >> 32).astype(np.int64), (keys[live] & 0xFFFFFFFF).astype(np.int32), summed[live]


def _join_seams(shape, write, locks_id, v, b, w):
    from .logic import weld_mean_diff, weld_members
    welds = shape["welds"]
    if not welds:
        return v, b, w
    seam = [u for group in weld_members(welds).values() if len(group) > 1
            and all(u in write for u in group) for u in group]
    seam = np.asarray(seam, dtype=np.int64)
    on_seam = np.isin(v, seam)
    if not on_seam.any():
        return v, b, w
    rows = {}
    for u, bone, x in zip(v[on_seam].tolist(), b[on_seam].tolist(), w[on_seam].tolist()):
        rows.setdefault(u, {})[bone] = x
    layer = shape["layer"]
    weld_mean_diff(rows, welds, write, lambda u: layer.get(u, {}), locks_id)
    keep = ~np.isin(v, np.fromiter(rows, dtype=np.int64, count=len(rows)))
    pairs = [(u, bone, x) for u, row in rows.items() for bone, x in row.items()]
    return (np.concatenate([v[keep], np.fromiter((p[0] for p in pairs), dtype=np.int64, count=len(pairs))]),
            np.concatenate([b[keep], np.fromiter((p[1] for p in pairs), dtype=np.int32, count=len(pairs))]),
            np.concatenate([w[keep], np.fromiter((p[2] for p in pairs), dtype=np.float64, count=len(pairs))]))
