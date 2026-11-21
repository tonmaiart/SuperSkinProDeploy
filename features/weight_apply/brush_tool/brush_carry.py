

_BIND_MAX_HOPS = 4


def _lerp(a, b, t):
    if isinstance(a, dict):
        return {k: a.get(k, 0.0) * (1.0 - t) + b.get(k, 0.0) * t for k in set(a) | set(b)}
    return a * (1.0 - t) + b * t


def _add_scaled(a, d, s):
    if isinstance(a, dict):
        return {k: a.get(k, 0.0) + d.get(k, 0.0) * s for k in set(a) | set(d)}
    return a + d * s


def _keep_locks(row, own, locks):
    if not isinstance(row, dict):
        return min(1.0, max(0.0, row))
    row = {b: x for b, x in row.items() if x > 1e-9}
    if not locks:
        return row
    locked = {b: x for b, x in own.items() if locks.get(b)}
    free = {b: x for b, x in row.items() if not locks.get(b)}
    room = max(0.0, sum(own.values()) - sum(locked.values()))
    total = sum(free.values())
    scale = room / total if total > 1e-12 else 0.0
    return {**{b: x * scale for b, x in free.items() if x * scale > 1e-9}, **locked}


def _shift(row, old, new):
    if not isinstance(row, dict):
        return row + new - old
    shifted = {k: max(0.0, row.get(k, 0.0) + new.get(k, 0.0) - old.get(k, 0.0))
               for k in set(row) | set(old) | set(new)}
    total = sum(shifted.values())
    scale = sum(row.values()) / total if total > 1e-12 else 0.0
    return {k: x * scale for k, x in shifted.items()}


def _blend(corners, weights, row_of):
    goal = None
    for c, w in zip(corners, weights):
        part = row_of(c)
        goal = _add_scaled(goal if goal is not None else ({} if isinstance(part, dict) else 0.0), part, w)
    return goal


SEAM_CLOSED_TOL = 1e-3


def _rows_match(a, b, tol):
    if isinstance(a, dict):
        return all(abs(a.get(k, 0.0) - b.get(k, 0.0)) <= tol for k in a.keys() | b.keys())
    return abs(a - b) <= tol


def joined_region(start, candidates, adjacency, weld_links, rows_of, tol=SEAM_CLOSED_TOL):
    out = set(start)
    stack = list(out)
    while stack:
        v = stack.pop()
        for n in adjacency.get(v, ()):
            if n in candidates and n not in out:
                out.add(n)
                stack.append(n)
        for n in weld_links.get(v, ()):
            if n in candidates and n not in out and _rows_match(rows_of(v), rows_of(n), tol):
                out.add(n)
                stack.append(n)
    return out


def seam_pin_rows(changed, rows_of, weld_links, locks=None):
    out = {}
    for v in changed:
        others = [u for u in weld_links.get(v, ()) if u not in changed]
        if not others:
            continue
        rows = [rows_of(u) for u in others]
        if not isinstance(rows[0], dict):
            out[v] = sum(rows) / len(rows)
            continue
        mean = {}
        for row in rows:
            for b, w in row.items():
                mean[b] = mean.get(b, 0.0) + w / len(rows)
        out[v] = _keep_locks(mean, rows_of(v), locks)
    return out


def lead_rows(bind, changed, rows_of, piece_of, leader, progress, locks=None, before=None):
    out = {}
    prior = before if before is not None else rows_of

    def row(v):
        return out[v] if v in out else rows_of(v)

    settled = {leader}
    frontier = {v for v in changed if piece_of(v) == leader}
    done = set()
    for _hop in range(_BIND_MAX_HOPS):
        targets = {}
        for c in frontier:
            for v, corners, weights in bind.get(c, ()):
                if v not in done and v not in targets and piece_of(v) not in settled:
                    targets[v] = (corners, weights)
        if not targets:
            break
        moved = set()
        for v, (corners, weights) in targets.items():
            t = min(1.0, progress(v, corners))
            if t <= 0.0:
                continue
            old_goal = _blend(corners, weights, prior)
            moved_row = _shift(_lerp(prior(v), old_goal, t), old_goal, _blend(corners, weights, row))
            own = rows_of(v)
            out[v] = _keep_locks(moved_row, own if isinstance(own, dict) else moved_row, locks)
            moved.add(v)
        done |= set(targets)
        settled |= {piece_of(v) for v in moved}
        frontier = moved
    return out


def carry_bound_layers(bind, gathered, radius, distance_of, world, hidden=None, piece_of=None,
                       home_piece=None):
    out = dict(gathered)
    frontier = list(gathered)
    for _hop in range(_BIND_MAX_HOPS):
        found = {}
        for u in frontier:
            for v, corners, weights in bind.get(u, ()):
                if v in out or v in found or (hidden is not None and hidden[v]):
                    continue
                if home_piece is not None and piece_of(v) == home_piece:
                    continue
                if distance_of(world[v]) >= radius:
                    continue
                point = sum(w * world[c] for c, w in zip(corners, weights))
                d = distance_of(point)
                if d < radius:
                    found[v] = d
        if not found:
            break
        out.update(found)
        frontier = list(found)
    return out
