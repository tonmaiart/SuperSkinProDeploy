
import numpy as np

_EPS = 1e-4
_SMOOTH_EPS = 1e-7

NO_DISTRIBUTE = "No target bones to distribute weight, falling back to zero, some bones may be locked"
NO_PULL = "No target bones to pull weight from, some bones may be locked"
NO_SMOOTH = "No weight to smooth, some bones may be locked"


def _others_total(row, active):
    return sum(w for b_id, w in row.items() if b_id != active)


def _row_changed(old, new):
    for b_id in old.keys() | new.keys():
        if abs(new.get(b_id, 0.0) - old.get(b_id, 0.0)) > _SMOOTH_EPS:
            return True
    return False


def _slots(selected, vert_ids):
    if not len(selected):
        return np.zeros(len(vert_ids), dtype=np.int64), np.zeros(len(vert_ids), dtype=bool)
    order = np.argsort(selected, kind="stable")
    ranked = selected[order]
    at = np.minimum(np.searchsorted(ranked, vert_ids), len(ranked) - 1)
    return order[at], ranked[at] == vert_ids


def _smooth_moved(baseline, diff, selected):
    dv, db, dw = diff
    on_sel = np.isin(dv, selected)
    if not on_sel.any():
        return False
    dv, db, dw = dv[on_sel], db[on_sel], dw[on_sel]
    bv, bb, bw = baseline
    old = np.isin(bv, dv)
    span = int(max(db.max(initial=0), bb.max(initial=0))) + 1
    keys = np.concatenate((bv[old] * span + bb[old], dv * span + db))
    deltas = np.concatenate((-bw[old], dw))
    order = np.argsort(keys, kind="stable")
    keys = keys[order]
    starts = np.flatnonzero(np.concatenate(([True], keys[1:] != keys[:-1])))
    return bool((np.abs(np.add.reduceat(deltas[order], starts)) > _SMOOTH_EPS).any())


def _outcome_from_arrays(action, baseline, diff, selected, active, steps):
    if action == "smooth":
        if not (steps > 0.0).any():
            return None
        return None if _smooth_moved(baseline, diff, selected) else NO_SMOOTH
    n = len(selected)

    def per_slot(coo):
        v, b, w = coo
        slot, inside = _slots(selected, v)
        slot, b, w = slot[inside], b[inside], w[inside]
        is_active = b == active
        own = np.bincount(slot[is_active], w[is_active], minlength=n)
        others = np.bincount(slot[~is_active], w[~is_active], minlength=n)
        present = np.zeros(n, dtype=bool)
        present[slot] = True
        return own, others, present

    old_a, old_others, _ = per_slot(baseline)
    new_a, new_others, changed = per_slot(diff)
    new_a = np.where(changed, new_a, old_a)
    new_others = np.where(changed, new_others, old_others)
    freed = old_a - new_a
    released = freed > _EPS
    if (released & (new_others - old_others < freed - _EPS)).any():
        return NO_DISTRIBUTE
    wanted = np.minimum(old_a + steps if action == "add" else old_a * steps, 1.0)
    pull_failed = ~released & (wanted > old_a + _EPS) & (new_a < wanted - _EPS)
    return NO_PULL if pull_failed.any() else None


def outcome_warning(action, baseline, diff, selected, active, step_of, steps=None):
    base_coo, diff_coo = getattr(baseline, "coo", None), getattr(diff, "coo", None)
    if (steps is not None and base_coo is not None and diff_coo is not None
            and (action == "smooth" or (action in ("add", "scale") and active is not None))):
        sel = np.asarray(selected, dtype=np.int64)
        if len(np.unique(sel)) == len(sel):
            return _outcome_from_arrays(action, base_coo, diff_coo, sel, active,
                                        np.asarray(steps, dtype=np.float64))
    if action == "smooth":
        if not any(step_of(i) > 0.0 for i in range(len(selected))):
            return None
        for v in selected:
            old = baseline.get(v, {})
            if _row_changed(old, diff.get(v, old)):
                return None
        return NO_SMOOTH

    if action not in ("add", "scale") or active is None:
        return None
    pull_failed = False
    for i, v in enumerate(selected):
        step = step_of(i)
        old = baseline.get(v, {})
        new = diff.get(v, old)
        old_a = old.get(active, 0.0)
        new_a = new.get(active, 0.0)
        freed = old_a - new_a
        if freed > _EPS:
            gained = _others_total(new, active) - _others_total(old, active)
            if gained < freed - _EPS:
                return NO_DISTRIBUTE
            continue
        wanted = min(old_a + step, 1.0) if action == "add" else min(old_a * step, 1.0)
        if wanted > old_a + _EPS and new_a < wanted - _EPS:
            pull_failed = True
    return NO_PULL if pull_failed else None
