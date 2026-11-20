
import numpy as np

_CONVENTION_THRESHOLD = 0.6
_ALIGNED_DOT = 0.9
_MIN_OFFSET = 1e-6
_LEAF_NEIGHBOR_FACTOR = 3.0
_NEAREST_CHUNK = 256
_JOINT_TOUCH_RATIO = 0.05

_cache: dict = {}


def _flat_matrices(collection, attr):
    flat = np.empty(len(collection) * 16, dtype=np.float32)
    collection.foreach_get(attr, flat)
    return flat.reshape(-1, 4, 4).transpose(0, 2, 1)


def _capped_leaf_lengths(heads, lengths, children):
    leaves = np.array([i for i, kids in enumerate(children) if not kids], dtype=np.int64)
    capped = lengths.copy()
    if not len(leaves) or len(heads) < 2:
        return capped
    nearest = np.full(len(leaves), np.inf, dtype=np.float32)
    for start in range(0, len(leaves), _NEAREST_CHUNK):
        rows = leaves[start:start + _NEAREST_CHUNK]
        dist = np.linalg.norm(heads[rows, None, :] - heads[None, :, :], axis=2)
        dist[dist <= _MIN_OFFSET] = np.inf
        nearest[start:start + len(rows)] = dist.min(axis=1)
    cap = nearest * _LEAF_NEIGHBOR_FACTOR
    capped[leaves] = np.where(np.isfinite(cap), np.minimum(lengths[leaves], cap), lengths[leaves])
    return capped


def _loose_deform_mask(bones, lengths, deform):
    n = len(bones)
    heads = np.empty(n * 3, dtype=np.float32)
    tails = np.empty(n * 3, dtype=np.float32)
    bones.foreach_get("head_local", heads)
    bones.foreach_get("tail_local", tails)
    idx = np.flatnonzero(deform)
    loose = np.zeros(n, dtype=bool)
    if not len(idx):
        return loose
    ends = np.stack([heads.reshape(-1, 3)[idx], tails.reshape(-1, 3)[idx]], axis=1)
    lens = lengths[idx]
    touching = np.zeros(len(idx), dtype=bool)
    for start in range(0, len(idx), _NEAREST_CHUNK):
        rows = slice(start, start + _NEAREST_CHUNK)
        dist = np.linalg.norm(ends[rows, None, :, None, :] - ends[None, :, None, :, :], axis=-1).min(axis=(2, 3))
        dist[np.arange(dist.shape[0]), np.arange(start, start + dist.shape[0])] = np.inf
        tol = _JOINT_TOUCH_RATIO * np.minimum(lens[rows, None], lens[None, :])
        touching[rows] = (dist <= tol).any(axis=1)
    loose[idx] = ~touching
    return loose


def _rest_local_vectors(bones, rest, lengths):
    n = len(bones)
    rot = rest[:, :3, :3]
    heads = rest[:, :3, 3]
    index = {b.name: i for i, b in enumerate(bones)}
    parent = [index[b.parent.name] if b.parent else -1 for b in bones]
    children = [[] for _ in range(n)]
    for i, p in enumerate(parent):
        if p >= 0:
            children[p].append(i)

    def offsets(i):
        kids = children[i]
        if not kids:
            return np.zeros((0, 3), dtype=np.float32)
        return (heads[kids] - heads[i]) @ rot[i]

    counts = np.zeros(3, dtype=np.int64)
    for i in range(n):
        if len(children[i]) == 1:
            off = offsets(i)[0]
            if np.linalg.norm(off) > _MIN_OFFSET:
                counts[int(np.argmax(np.abs(off)))] += 1
    total = int(counts.sum())
    axis = int(np.argmax(counts))
    if not total or axis == 1 or counts[axis] / total < _CONVENTION_THRESHOLD:
        return None

    lengths = _capped_leaf_lengths(heads, lengths, children)
    local = np.zeros((n, 3), dtype=np.float32)
    local[:, 1] = lengths
    sign = np.zeros(n, dtype=np.float32)
    stack = [i for i in range(n) if parent[i] < 0]
    while stack:
        i = stack.pop()
        stack.extend(children[i])
        off = offsets(i)
        dist = np.linalg.norm(off, axis=1)
        valid = dist > _MIN_OFFSET
        off, dist = off[valid], dist[valid]
        on_axis = np.argmax(np.abs(off), axis=1) == axis if len(off) else np.zeros(0, dtype=bool)
        if not on_axis.any() and len(off) and (off[:, 1] / dist > _ALIGNED_DOT).any():
            continue
        if on_axis.any():
            cand = off[on_axis]
            best = cand[int(np.argmax(np.abs(cand[:, axis]) / dist[on_axis]))]
            s, length = float(np.sign(best[axis])), float(abs(best[axis]))
        elif parent[i] >= 0 and sign[parent[i]]:
            s, length = float(sign[parent[i]]), float(lengths[i])
        else:
            continue
        sign[i] = s
        local[i] = 0.0
        local[i, axis] = s * length

    for i in range(n):
        if children[i]:
            continue
        p = parent[i]
        if p >= 0:
            off = (heads[i] - heads[p]) @ rot[p]
            dist = np.linalg.norm(off)
            reach = np.linalg.norm(local[p])
            if dist > _MIN_OFFSET and reach > 0.0 and off @ local[p] / (dist * reach) > _ALIGNED_DOT:
                continue
        local[i] = 0.0
    return local


def _local_vectors(armature, pose_bones):
    bones = armature.data.bones
    rest = _flat_matrices(bones, "matrix_local")
    lengths = np.empty(len(bones), dtype=np.float32)
    bones.foreach_get("length", lengths)
    deform = np.empty(len(bones), dtype=bool)
    bones.foreach_get("use_deform", deform)

    key = armature.as_pointer()
    hit = _cache.get(key)
    if (hit is not None and np.array_equal(hit[0], rest) and np.array_equal(hit[1], lengths)
            and np.array_equal(hit[2], deform)):
        return hit[3], hit[4]

    local = _rest_local_vectors(bones, rest, lengths)
    loose = None
    if local is not None:
        index = {b.name: i for i, b in enumerate(bones)}
        local = local[[index[name] for name in pose_bones.keys()]]
    else:
        loose = _loose_deform_mask(bones, lengths, deform)
        if loose.any():
            index = {b.name: i for i, b in enumerate(bones)}
            loose = loose[[index[name] for name in pose_bones.keys()]]
        else:
            loose = None
    _cache[key] = (rest, lengths, deform, local, loose)
    return local, loose


def display_tails(armature, pose_bones):
    if not len(pose_bones):
        return None
    local, loose = _local_vectors(armature, pose_bones)
    if local is None:
        if loose is None:
            return None
        tails = np.empty(len(pose_bones) * 3, dtype=np.float32)
        pose_bones.foreach_get("tail", tails)
        tails = tails.reshape(-1, 3)
        heads = np.empty(len(pose_bones) * 3, dtype=np.float32)
        pose_bones.foreach_get("head", heads)
        tails[loose] = heads.reshape(-1, 3)[loose]
        return tails
    pose = _flat_matrices(pose_bones, "matrix")
    return np.einsum("nij,nj->ni", pose[:, :3, :3], local) + pose[:, :3, 3]


def clear_cache():
    _cache.clear()
