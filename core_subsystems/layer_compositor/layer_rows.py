
from collections.abc import Mapping, MutableMapping
from itertools import chain
from operator import methodcaller

import numpy as np

_MISSING = object()
_VALUES = methodcaller("values")
_OVERLAY_SLACK = 2
_OVERLAY_MIN_COMPACT = 1 << 20


def _spans(starts, counts):
    total = int(counts.sum())
    return np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts) + np.repeat(starts, counts)


def _dict_columns(verts, rows):
    lens = np.fromiter(map(len, rows), dtype=np.int64, count=len(rows))
    total = int(lens.sum())
    return (np.repeat(np.asarray(verts, dtype=np.int64), lens),
            np.fromiter(chain.from_iterable(rows), dtype=np.int64, count=total),
            np.fromiter(chain.from_iterable(map(_VALUES, rows)), dtype=np.float64, count=total))


def _grouped(vert_ids):
    order = np.argsort(vert_ids, kind="stable")
    sorted_v = vert_ids[order]
    starts = np.empty(len(sorted_v), dtype=bool)
    starts[:1] = True
    np.not_equal(sorted_v[1:], sorted_v[:-1], out=starts[1:])
    first = np.flatnonzero(starts)
    return order, first, sorted_v[first], np.diff(np.append(first, len(sorted_v)))


def _rows_in_first_order(vert_ids, keys, weights):
    if not len(vert_ids):
        return {}
    order, first, uniq, lens = _grouped(vert_ids)
    sorted_k = keys[order].tolist()
    sorted_w = weights[order].tolist()
    bounds = np.append(first, len(order)).tolist()
    by_vertex = [dict(zip(sorted_k[bounds[i]:bounds[i + 1]], sorted_w[bounds[i]:bounds[i + 1]]))
                 for i in range(len(uniq))]
    seen = np.argsort(order[first], kind="stable").tolist()
    uniq = uniq.tolist()
    return {uniq[i]: by_vertex[i] for i in seen}


class LayerRows(MutableMapping):
    """``{v_idx: {bone_id: weight}}`` over a decoded layer's arrays, which never change. A row
    becomes a dict the first time it is read and is then kept, so every read returns the same
    object; writes are kept beside the arrays, as dicts or, through `assign_coo()`, as arrays.
    Iteration follows the dict the arrays decode to, with new keys after it, as a dict would."""

    __slots__ = ("_verts", "_lens", "_starts", "_ids", "_weights", "_complete", "_pos",
                 "_live_rows", "_rows", "_gone", "_n_extra",
                 "_ov_ref", "_ov_start", "_ov_len", "_ov_chunks", "_ov_owned", "_ov_total", "_ov_live")

    @classmethod
    def from_arrays(cls, arrays, bone_to_id):
        names, vert_ids, row_lens, name_idx, weights = arrays
        verts = np.asarray(vert_ids, dtype=np.int64)
        if len(verts) and int(verts.min()) < 0:
            return None
        id_of_name = np.array([bone_to_id.get(n, -1) for n in names] or [-1], dtype=np.int64)
        self = cls.__new__(cls)
        self._verts = verts
        self._lens = np.asarray(row_lens, dtype=np.int64)
        self._starts = np.concatenate(([0], np.cumsum(self._lens))).astype(np.int64)
        self._ids = id_of_name[np.asarray(name_idx, dtype=np.int64)]
        self._weights = np.asarray(weights, dtype=np.float64)
        self._complete = not len(self._ids) or bool(self._ids.min() >= 0)
        pos = np.full(int(verts.max()) + 1 if len(verts) else 0, -1, dtype=np.int64)
        pos[verts] = np.arange(len(verts))
        if not np.array_equal(pos[verts], np.arange(len(verts))):
            return None
        self._pos = pos
        self._live_rows = None
        self._rows = {}
        self._gone = set()
        self._n_extra = 0
        self._ov_ref = self._ov_start = self._ov_len = None
        self._ov_chunks = []
        self._ov_owned = True
        self._ov_total = self._ov_live = 0
        return self

    def copy(self):
        other = LayerRows.__new__(LayerRows)
        for name in ("_verts", "_lens", "_starts", "_ids", "_weights", "_complete", "_pos",
                     "_live_rows", "_ov_ref", "_ov_start", "_ov_len", "_ov_chunks", "_ov_total",
                     "_ov_live"):
            setattr(other, name, getattr(self, name))
        other._rows = self._rows.copy()
        other._gone = set(self._gone)
        other._n_extra = self._n_extra
        other._ov_owned = self._ov_owned = self._ov_ref is None
        return other

    def _index(self, v):
        try:
            if 0 <= v < len(self._pos):
                return int(self._pos[v])
        except TypeError:
            pass
        return -1

    def _is_extra(self, v):
        return v in self._gone or self._index(v) < 0

    def _row_at(self, p):
        s, e = self._starts[p], self._starts[p + 1]
        ids, weights = self._ids[s:e].tolist(), self._weights[s:e].tolist()
        if self._complete:
            return dict(zip(ids, weights))
        return {b: w for b, w in zip(ids, weights) if b >= 0}

    def _in_overlay(self, v):
        ref = self._ov_ref
        return ref is not None and 0 <= v < len(ref) and ref[v] >= 0

    def _overlay_row(self, v):
        chunk_ids, chunk_weights = self._ov_chunks[int(self._ov_ref[v])]
        s = int(self._ov_start[v])
        e = s + int(self._ov_len[v])
        return dict(zip(chunk_ids[s:e].tolist(), chunk_weights[s:e].tolist()))

    def _own_overlay(self):
        if self._ov_ref is None:
            n = len(self._pos)
            self._ov_ref = np.full(n, -1, dtype=np.int32)
            self._ov_start = np.zeros(n, dtype=np.int64)
            self._ov_len = np.zeros(n, dtype=np.int32)
            self._ov_chunks = []
        elif not self._ov_owned:
            self._ov_ref = self._ov_ref.copy()
            self._ov_start = self._ov_start.copy()
            self._ov_len = self._ov_len.copy()
            self._ov_chunks = list(self._ov_chunks)
        self._ov_owned = True

    def _drop_overlay(self, verts):
        if self._ov_ref is None or not len(verts):
            return
        held = verts[self._ov_ref[verts] >= 0]
        if not len(held):
            return
        self._own_overlay()
        self._ov_live -= int(self._ov_len[held].sum())
        self._ov_ref[held] = -1

    def _compact_overlay(self):
        live = np.flatnonzero(self._ov_ref >= 0)
        ids, weights, lens = self._overlay_columns(live)
        self._ov_chunks = [(ids, weights)]
        self._ov_ref[live] = 0
        self._ov_start[live] = np.concatenate(([0], np.cumsum(lens)[:-1])) if len(lens) else 0
        self._ov_total = self._ov_live = len(ids)

    def _overlay_columns(self, verts):
        refs = self._ov_ref[verts]
        lens = self._ov_len[verts].astype(np.int64)
        starts = self._ov_start[verts]
        if len(self._ov_chunks) == 1 or not len(verts):
            ids, weights = self._ov_chunks[0] if self._ov_chunks else (np.empty(0, np.int64),
                                                                       np.empty(0))
            at = _spans(starts, lens)
            return ids[at], weights[at], lens
        out_ids = np.empty(int(lens.sum()), dtype=np.int64)
        out_w = np.empty(len(out_ids), dtype=np.float64)
        dest = np.concatenate(([0], np.cumsum(lens)[:-1]))
        for c in np.unique(refs).tolist():
            sel = refs == c
            ids, weights = self._ov_chunks[c]
            src = _spans(starts[sel], lens[sel])
            to = _spans(dest[sel], lens[sel])
            out_ids[to] = ids[src]
            out_w[to] = weights[src]
        return out_ids, out_w, lens

    def _source_columns(self, cand):
        parts = []
        if self._ov_ref is not None and len(cand):
            in_ov = self._ov_ref[cand] >= 0
            if in_ov.any():
                ov_verts = cand[in_ov]
                ids, weights, lens = self._overlay_columns(ov_verts)
                parts.append((ov_verts, lens, ids, weights))
                cand = cand[~in_ov]
        if len(cand):
            p = self._pos[cand]
            lens = self._lens[p]
            at = _spans(self._starts[p], lens)
            ids, weights = self._ids[at], self._weights[at]
            if not self._complete:
                keep = ids >= 0
                lens = np.bincount(np.repeat(np.arange(len(cand)), lens)[keep], minlength=len(cand))
                ids, weights = ids[keep], weights[keep]
            parts.append((cand, lens, ids, weights))
        if not parts:
            empty = np.empty(0, dtype=np.int64)
            return empty, empty, empty, np.empty(0)
        if len(parts) == 1:
            return parts[0]
        return tuple(np.concatenate(cols) for cols in zip(*parts))

    def _held(self, verts):
        verts = verts[(verts >= 0) & (verts < len(self._pos))]
        verts = verts[self._pos[verts] >= 0]
        if self._gone and len(verts):
            gone = self._gone
            verts = verts[np.fromiter((v not in gone for v in verts.tolist()), dtype=bool,
                                      count=len(verts))]
        return verts

    def _load(self, verts):
        cand = self._held(np.fromiter(verts, dtype=np.int64, count=len(verts)))
        if not len(cand):
            return
        cand, lens, ids, weights = self._source_columns(cand)
        ids, weights = ids.tolist(), weights.tolist()
        rows, at = self._rows, 0
        for v, n in zip(cand.tolist(), lens.tolist()):
            end = at + n
            rows[v] = dict(zip(ids[at:end], weights[at:end]))
            at = end

    def rows_of(self, verts) -> dict:
        if not isinstance(verts, (list, tuple, set, frozenset, Mapping)):
            verts = list(verts)
        rows = self._rows
        missing = [v for v in verts if v not in rows]
        if missing:
            self._load(missing)
        return {v: rows[v] for v in verts if v in rows}

    def coo_of(self, verts, ascending=False):
        out = self._coo_of(verts)
        if ascending and len(out[0]):
            order = np.argsort(out[0], kind="stable")
            out = tuple(col[order] for col in out)
        return out

    def _coo_of(self, verts):
        verts = np.asarray(verts if isinstance(verts, np.ndarray) else list(verts), dtype=np.int64)
        parts = []
        rows = self._rows
        if rows and len(verts):
            as_dict = np.isin(verts, np.fromiter(rows.keys(), dtype=np.int64, count=len(rows)))
            if as_dict.any():
                dict_verts = verts[as_dict].tolist()
                parts.append(_dict_columns(dict_verts, [rows[v] for v in dict_verts]))
                verts = verts[~as_dict]
        cand, lens, ids, weights = self._source_columns(self._held(verts))
        parts.append((np.repeat(cand, lens), ids, weights))
        if len(parts) == 1:
            return parts[0]
        return tuple(np.concatenate(cols) for cols in zip(*parts))

    def assign_coo(self, vert_ids, keys, weights) -> None:
        vert_ids = np.asarray(vert_ids, dtype=np.int64)
        if not len(vert_ids):
            return
        keys = np.asarray(keys, dtype=np.int64)
        weights = np.asarray(weights, dtype=np.float64)
        order, first, uniq, lens = _grouped(vert_ids)
        in_place = (uniq >= 0) & (uniq < len(self._pos))
        in_place[in_place] = self._pos[uniq[in_place]] >= 0
        if self._gone and in_place.any():
            gone = self._gone
            check = np.flatnonzero(in_place)
            in_place[check] = np.fromiter((v not in gone for v in uniq[check].tolist()), dtype=bool,
                                          count=len(check))
        if not in_place.all():
            new = ~np.isin(vert_ids, uniq[in_place])
            self.update(_rows_in_first_order(vert_ids[new], keys[new], weights[new]))
        if not in_place.any():
            return
        verts = uniq[in_place]
        lens = lens[in_place]
        at = _spans(first[in_place], lens)
        self._own_overlay()
        self._drop_overlay(verts)
        self._ov_chunks.append((keys[order][at], weights[order][at]))
        self._ov_ref[verts] = len(self._ov_chunks) - 1
        self._ov_start[verts] = np.concatenate(([0], np.cumsum(lens)[:-1]))
        self._ov_len[verts] = lens
        self._ov_total += len(at)
        self._ov_live += len(at)
        rows = self._rows
        if rows:
            for v in verts[np.isin(verts, np.fromiter(rows.keys(), dtype=np.int64,
                                                      count=len(rows)))].tolist():
                del rows[v]
        if self._ov_total > _OVERLAY_SLACK * self._ov_live + _OVERLAY_MIN_COMPACT:
            self._compact_overlay()

    def get(self, v, default=None):
        row = self._rows.get(v, _MISSING)
        if row is not _MISSING:
            return row
        p = self._index(v)
        if p < 0 or v in self._gone:
            return default
        row = self._rows[v] = self._overlay_row(v) if self._in_overlay(v) else self._row_at(p)
        return row

    def __getitem__(self, v):
        row = self.get(v, _MISSING)
        if row is _MISSING:
            raise KeyError(v)
        return row

    def __contains__(self, v):
        return v in self._rows or (self._index(v) >= 0 and v not in self._gone)

    def __setitem__(self, v, row):
        rows = self._rows
        if v not in rows and self._is_extra(v):
            self._n_extra += 1
        rows[v] = row

    def update(self, other=(), **kwargs):
        if isinstance(other, dict) and not kwargs:
            rows = self._rows
            self._n_extra += sum(1 for v in other if v not in rows and self._is_extra(v))
            rows.update(other)
            return
        super().update(other, **kwargs)

    def __delitem__(self, v):
        rows = self._rows
        p = self._index(v)
        if v in rows:
            if self._is_extra(v):
                self._n_extra -= 1
            del rows[v]
            if p >= 0:
                self._gone.add(v)
                self._drop_overlay(np.array([v], dtype=np.int64))
            return
        if p >= 0 and v not in self._gone:
            self._gone.add(v)
            self._drop_overlay(np.array([v], dtype=np.int64))
            return
        raise KeyError(v)

    def __len__(self):
        return len(self._verts) - len(self._gone) + self._n_extra

    def __iter__(self):
        gone = self._gone
        if gone:
            yield from (v for v in self._verts.tolist() if v not in gone)
        else:
            yield from self._verts.tolist()
        yield from [v for v in self._rows if self._is_extra(v)]

    def _overrides(self):
        in_place, extras = [], []
        for v in self._rows:
            (extras if self._is_extra(v) else in_place).append(v)
        return in_place, extras

    def _overlay_verts(self):
        if self._ov_ref is None:
            return np.empty(0, dtype=np.int64)
        verts = np.flatnonzero(self._ov_ref >= 0)
        if self._rows and len(verts):
            verts = verts[~np.isin(verts, np.fromiter(self._rows.keys(), dtype=np.int64,
                                                      count=len(self._rows)))]
        if self._gone and len(verts):
            gone = self._gone
            verts = verts[np.fromiter((v not in gone for v in verts.tolist()), dtype=bool,
                                      count=len(verts))]
        return verts

    def columns(self, key_dtype=np.int64, weight_dtype=np.float64):
        rows = self._rows
        in_place, extras = self._overrides()
        overlaid = self._overlay_verts()
        row_of = np.repeat(np.arange(len(self._verts)), self._lens)
        keep = self._ids >= 0
        skipped = [self._index(v) for v in chain(in_place, self._gone)]
        if len(overlaid):
            skipped = np.concatenate((np.asarray(skipped, dtype=np.int64), self._pos[overlaid]))
        if len(skipped):
            drop = np.zeros(len(self._verts), dtype=bool)
            drop[skipped] = True
            keep &= ~drop[row_of]
        out_v = self._verts[row_of[keep]]
        out_k = self._ids[keep]
        out_w = self._weights[keep]
        if in_place or len(overlaid):
            dict_v, dict_k, dict_w = _dict_columns(in_place, [rows[v] for v in in_place])
            dict_lens = np.fromiter((len(rows[v]) for v in in_place), dtype=np.int64,
                                    count=len(in_place))
            ov_k, ov_w, ov_lens = (self._overlay_columns(overlaid) if len(overlaid)
                                   else (np.empty(0, np.int64), np.empty(0), np.empty(0, np.int64)))
            placed = np.concatenate((np.asarray(in_place, dtype=np.int64), overlaid))
            lens = np.concatenate((dict_lens, ov_lens))
            ranks = self._pos[placed]
            order = np.argsort(ranks, kind="stable")
            entry_k = np.concatenate((dict_k, ov_k))
            entry_w = np.concatenate((dict_w, ov_w))
            starts = np.concatenate(([0], np.cumsum(lens)[:-1]))
            at = _spans(starts[order], lens[order])
            sorted_lens = lens[order]
            pos = np.searchsorted(row_of[keep], np.repeat(ranks[order], sorted_lens), side="left")
            out_v = np.insert(out_v, pos, np.repeat(placed[order], sorted_lens))
            out_k = np.insert(out_k, pos, entry_k[at])
            out_w = np.insert(out_w, pos, entry_w[at])
        if extras:
            ex_v, ex_k, ex_w = _dict_columns(extras, [rows[v] for v in extras])
            out_v = np.concatenate((out_v, ex_v))
            out_k = np.concatenate((out_k, ex_k))
            out_w = np.concatenate((out_w, ex_w))
        return (out_v.astype(np.int64, copy=False), out_k.astype(key_dtype, copy=False),
                out_w.astype(weight_dtype, copy=False))

    def has_entries(self) -> bool:
        rows, gone = self._rows, self._gone
        if any(rows.values()):
            return True
        if self._live_rows is None:
            filled = np.repeat(np.arange(len(self._verts)), self._lens)[self._ids >= 0]
            self._live_rows = np.flatnonzero(np.bincount(filled, minlength=len(self._verts)))
        live = self._live_rows
        ref = self._ov_ref
        for start in range(0, len(live), 256):
            for v in self._verts[live[start:start + 256]].tolist():
                if v not in rows and v not in gone and (ref is None or ref[v] < 0):
                    return True
        return bool(len(self._overlay_verts()))

    def bones_above(self, threshold) -> set:
        _verts, keys, weights = self.columns()
        heavy = keys[weights > threshold]
        if not len(heavy):
            return set()
        return set(np.flatnonzero(np.bincount(heavy)).tolist())

    def entry_count(self) -> int:
        return len(self.columns()[0])


class CooRows(MutableMapping):
    """``{v_idx: {bone_id: weight}}`` of COO arrays, as `coo_to_int_layer()` builds it. `coo` holds
    the arrays until the first write, which turns this into a plain dict and clears it."""

    __slots__ = ("coo", "_dict")

    def __init__(self, vert_ids, keys, weights):
        vert_ids = np.asarray(vert_ids, dtype=np.int64)
        keys = np.asarray(keys, dtype=np.int64)
        weights = np.asarray(weights, dtype=np.float64)
        real = vert_ids >= 0
        if not real.all():
            vert_ids, keys, weights = vert_ids[real], keys[real], weights[real]
        self.coo = (vert_ids, keys, weights)
        self._dict = None

    def _rows(self):
        if self._dict is None:
            self._dict = _rows_in_first_order(*self.coo)
        return self._dict

    def _written(self):
        rows = self._rows()
        self.coo = None
        return rows

    def restrict(self, verts) -> "CooRows":
        if self.coo is None:
            out = CooRows((), (), ())
            out.coo = None
            out._dict = {v: row for v, row in self._dict.items() if v in verts}
            return out
        vert_ids, keys, weights = self.coo
        wanted = np.fromiter(verts, dtype=np.int64, count=len(verts))
        inside = np.isin(vert_ids, wanted)
        return CooRows(vert_ids[inside], keys[inside], weights[inside])

    def copy(self) -> dict:
        return dict(self._rows())

    def __getitem__(self, v):
        return self._rows()[v]

    def get(self, v, default=None):
        return self._rows().get(v, default)

    def __contains__(self, v):
        return v in self._rows()

    def __iter__(self):
        return iter(self._rows())

    def __len__(self):
        return len(self._rows())

    def __setitem__(self, v, row):
        self._written()[v] = row

    def __delitem__(self, v):
        del self._written()[v]

    def update(self, other=(), **kwargs):
        self._written().update(other, **kwargs)


class RowSlice(Mapping):
    """``{v: layer.get(v, {}) for v in verts}`` of *layer* as it is now, held as COO arrays; rows
    are built only when one is read."""

    __slots__ = ("_verts", "coo", "_dict")

    def __init__(self, layer, verts, ascending=False):
        self._verts = verts
        self.coo = layer.coo_of(verts, ascending)
        self._dict = None

    def columns(self, key_dtype=np.int64, weight_dtype=np.float64):
        vert_ids, keys, weights = self.coo
        return (vert_ids, keys.astype(key_dtype, copy=False), weights.astype(weight_dtype, copy=False))

    def _rows(self):
        if self._dict is None:
            built = _rows_in_first_order(*self.coo)
            self._dict = {v: built.get(v, {}) for v in self._verts}
        return self._dict

    def __getitem__(self, v):
        return self._rows()[v]

    def get(self, v, default=None):
        return self._rows().get(v, default)

    def __contains__(self, v):
        return v in self._rows()

    def __iter__(self):
        return iter(self._rows())

    def __len__(self):
        return len(self._verts)
