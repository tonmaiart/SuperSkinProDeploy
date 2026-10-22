
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


def outcome_warning(action, baseline, diff, selected, active, step_of):
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
