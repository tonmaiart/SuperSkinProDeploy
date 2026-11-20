
import numpy as np

_MIN_CONTRIB = 1e-4
_DENSE_CELLS = 4_000_000


def primary_armature(obj):
    for md in obj.modifiers:
        if (md.show_viewport and md.type == 'ARMATURE' and md.object is not None
                and md.object.type == 'ARMATURE'):
            return md
    return None


def _np(matrix):
    return np.array(matrix, dtype=np.float64)


def _dual_quats(mats):
    m = mats[:, :3, :3]
    det = np.linalg.det(m)
    scale = np.linalg.norm(m, axis=1)
    rigid = (det > 0.0) & (np.abs(scale - 1.0).max(axis=1) < 1e-4)
    q = np.zeros((len(m), 4))
    tr = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    for k in range(len(m)):
        r = m[k]
        if tr[k] > 0:
            s = np.sqrt(tr[k] + 1.0) * 2
            q[k] = (0.25 * s, (r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s)
        elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
            s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
            q[k] = ((r[2, 1] - r[1, 2]) / s, 0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s)
        elif r[1, 1] > r[2, 2]:
            s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
            q[k] = ((r[0, 2] - r[2, 0]) / s, (r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s)
        else:
            s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
            q[k] = ((r[1, 0] - r[0, 1]) / s, (r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    t = mats[:, :3, 3]
    w, x, y, z = q.T
    trans = np.stack((-0.5 * (t[:, 0] * x + t[:, 1] * y + t[:, 2] * z),
                      0.5 * (t[:, 0] * w + t[:, 1] * z - t[:, 2] * y),
                      0.5 * (-t[:, 0] * z + t[:, 1] * w + t[:, 2] * x),
                      0.5 * (t[:, 0] * y - t[:, 1] * x + t[:, 2] * w)), axis=1)
    return q, trans, rigid


def _apply_dual_quats(co, q, t):
    w, x, y, z = q.T
    t0, t1, t2, t3 = t.T
    rot = np.empty((len(co), 3, 3))
    rot[:, 0] = np.stack((w * w + x * x - y * y - z * z, 2 * (x * y - w * z), 2 * (x * z + w * y)), 1)
    rot[:, 1] = np.stack((2 * (x * y + w * z), w * w + y * y - x * x - z * z, 2 * (y * z - w * x)), 1)
    rot[:, 2] = np.stack((2 * (x * z - w * y), 2 * (y * z + w * x), w * w + z * z - x * x - y * y), 1)
    move = np.stack((2 * (-t0 * x + w * t1 - t2 * z + y * t3),
                     2 * (-t0 * y + t1 * z - x * t3 + w * t2),
                     2 * (-t0 * z + x * t2 + w * t3 - t1 * y)), 1)
    length = np.einsum('ij,ij->i', q, q)
    inv = np.where(length > 0.0, 1.0 / np.maximum(length, 1e-30), 0.0)
    return (np.einsum('kij,kj->ki', rot, co) + move) * inv[:, None]


class _Bone:
    """One deform bone: its object-space deform matrix, or a bent B-Bone's joint matrices, and
    its envelope in object space."""

    def __init__(self, pb, pre, post):
        bone = pb.bone
        rest_inv = bone.matrix_local.inverted()
        self.mat = _np(post @ pb.matrix @ rest_inv @ pre)
        self.joints = None
        if bone.bbone_segments > 1:
            joints = []
            for i in range(bone.bbone_segments + 1):
                posed = pb.bbone_segment_matrix(i, rest=False)
                rest = pb.bbone_segment_matrix(i, rest=True)
                joints.append(_np(post @ pb.matrix @ posed @ rest.inverted() @ rest_inv @ pre))
            joints = np.stack(joints)
            if np.abs(joints - joints[0]).max() > 1e-9:
                self.joints = joints
                self.to_bone = _np(rest_inv @ pre)
                self.length = max(bone.length, 1e-12)
        self.to_arm = _np(pre)
        self.head, self.tail = np.array(bone.head_local), np.array(bone.tail_local)
        self.rad_head, self.rad_tail = bone.head_radius, bone.tail_radius
        self.dist, self.env_weight = bone.envelope_distance, bone.envelope_weight
        self.env_multiply = bone.use_envelope_multiply

    def envelope(self, co):
        p = co @ self.to_arm[:3, :3].T + self.to_arm[:3, 3]
        axis = self.tail - self.head
        length = np.linalg.norm(axis)
        axis = axis / length if length > 0 else axis
        rel = p - self.head
        a = rel @ axis
        hsqr = np.einsum('ij,ij->i', rel, rel)
        dist_sq = np.where(a < 0.0, np.einsum('ij,ij->i', rel, rel),
                           np.where(a > length, np.einsum('ij,ij->i', p - self.tail, p - self.tail),
                                    hsqr - a * a))
        f = np.clip(a / length, 0.0, 1.0) if length > 0 else np.zeros_like(a)
        rad = np.where(a < 0.0, self.rad_head,
                       np.where(a > length, self.rad_tail, f * self.rad_tail + (1.0 - f) * self.rad_head))
        out = np.zeros(len(p))
        inside = dist_sq < rad * rad
        out[inside] = 1.0
        if self.dist > 0.0:
            reach = (~inside) & (dist_sq < (rad + self.dist) ** 2)
            gap = np.sqrt(dist_sq[reach]) - rad[reach]
            out[reach] = 1.0 - gap * gap / (self.dist * self.dist)
        return out


class StrokeSkin:
    """Positions of a stroke's vertices for given deform rows, as the Armature modifier gives them.

    *base* is what the Armature receives (shape keys and the modifiers before it applied). With
    modifiers after the Armature, a vertex keeps the offset those modifiers gave it before the
    stroke, read on its first move, so the preview follows the Armature and stays seamless."""

    def __init__(self, obj, md, base, shown):
        self.obj = obj
        self.base = base
        self.shown = shown
        arm = md.object
        pre = arm.matrix_world.inverted() @ obj.matrix_world
        post = pre.inverted()
        self.table = []
        index = {}
        for pb in arm.pose.bones:
            if pb.bone.use_deform:
                index[pb.name] = len(self.table)
                self.table.append(_Bone(pb, pre, post))
        n_groups = len(obj.vertex_groups)
        self.group_bone = np.full(n_groups, -1, dtype=np.int64)
        if md.use_vertex_groups:
            for i, vg in enumerate(obj.vertex_groups):
                self.group_bone[i] = index.get(vg.name, -1)
        self.has = self.group_bone >= 0
        self.mats = np.stack([b.mat for b in self.table]) if self.table else np.zeros((0, 4, 4))
        self.bent = {i for i, b in enumerate(self.table) if b.joints is not None}
        self.envelopes = md.use_bone_envelopes
        self.multiply = np.array([b.env_multiply for b in self.table], dtype=bool)
        self.dual = md.use_deform_preserve_volume
        self.joint_base = {}
        columns = [self.mats]
        for b in sorted(self.bent):
            self.joint_base[b] = sum(len(c) for c in columns)
            columns.append(self.table[b].joints)
        all_mats = np.concatenate(columns)
        self.lbs_table = all_mats[:, :3, :].reshape(-1, 12)
        if self.dual and self.table:
            self.dq_q, self.dq_t, rigid = _dual_quats(all_mats)
            self.dq_dots = self.dq_q @ self.dq_q.T
            self.dual = bool(rigid.all())
        self.residual = None
        if _has_modifiers_after(obj, md):
            self.residual = np.full(base.shape, np.nan)

    def positions(self, verts, vert_ids, groups, weights):
        verts = np.asarray(verts, dtype=np.int64)
        moved = self._skin(verts, vert_ids, groups, weights)
        if self.residual is None:
            return moved
        fresh = verts[np.isnan(self.residual[verts, 0])]
        if len(fresh):
            old = _group_rows(self.obj.data, fresh, self.has)
            self.residual[fresh] = self.shown[fresh] - self._skin(fresh, *old)
        return moved + self.residual[verts]

    def _rows(self, verts, vert_ids, groups, weights):
        vert_ids = np.asarray(vert_ids, dtype=np.int64)
        groups = np.asarray(groups, dtype=np.int64)
        weights = np.asarray(weights, dtype=np.float64)
        slot = np.zeros(len(self.base), dtype=np.int64)
        slot[verts] = np.arange(len(verts))
        local = slot[vert_ids]
        keep = (groups >= 0) & (groups < len(self.has)) & (weights > 0.0)
        keep &= self.has[np.where(keep, groups, 0)]
        local, bones, weights = local[keep], self.group_bone[groups[keep]], weights[keep]
        if not self.envelopes or not self.table:
            return local, bones, weights
        co = self.base[verts]
        if self.multiply.any():
            weights = weights.copy()
            for b in np.unique(bones[self.multiply[bones]]).tolist():
                sel = bones == b
                weights[sel] *= self.table[b].envelope(co[local[sel]])
        bare = np.flatnonzero(np.bincount(local, minlength=len(verts)) == 0)
        if not len(bare):
            return local, bones, weights
        extra = [(local, bones, weights)]
        for b, bone in enumerate(self.table):
            fac = bone.envelope(co[bare]) * bone.env_weight
            hit = fac > 0.0
            if hit.any():
                extra.append((bare[hit], np.full(int(hit.sum()), b), fac[hit]))
        return tuple(np.concatenate(parts) for parts in zip(*extra))

    def _segments(self, b, p):
        bone = self.table[b]
        y = (p @ bone.to_bone[1, :3] + bone.to_bone[1, 3]) / bone.length
        segments = len(bone.joints) - 1
        pre_blend = np.clip(y, 0.0, 1.0) * segments
        index = np.clip(np.floor(pre_blend).astype(np.int64), 0, segments - 1)
        return index, np.clip(pre_blend - index, 0.0, 1.0)

    def _skin(self, verts, vert_ids, groups, weights):
        verts = np.asarray(verts, dtype=np.int64)
        local, bones, weights = self._rows(verts, vert_ids, groups, weights)
        co = self.base[verts]
        n = len(verts)
        parts_local, parts_col, parts_w = [], [], []
        plain = np.ones(len(bones), dtype=bool)
        for b in sorted(self.bent & set(np.unique(bones).tolist())):
            sel = bones == b
            plain &= ~sel
            index, blend = self._segments(b, co[local[sel]])
            for joint, share in ((index, 1.0 - blend), (index + 1, blend)):
                parts_local.append(local[sel])
                parts_col.append(self.joint_base[b] + joint)
                parts_w.append(weights[sel] * share)
        parts_local.append(local[plain])
        parts_col.append(bones[plain])
        parts_w.append(weights[plain])
        loc = np.concatenate(parts_local)
        cols = np.concatenate(parts_col)
        w = np.concatenate(parts_w)
        contrib = np.bincount(loc, weights=w, minlength=n)
        ok = contrib > _MIN_CONTRIB
        out = co.copy()
        if self.dual:
            order = np.argsort(loc, kind="stable")
            ordered = loc[order]
            starts = np.flatnonzero(np.r_[True, ordered[1:] != ordered[:-1]]) if len(loc) else order
            first_col = np.zeros(n, dtype=np.int64)
            first_col[ordered[starts]] = cols[order[starts]]
            sign = np.where(self.dq_dots[cols, first_col[loc]] < 0.0, -1.0, 1.0)
            sq = _blend(loc, cols, w * sign, n, self.dq_q)
            st = _blend(loc, cols, w * sign, n, self.dq_t)
            out[ok] = _apply_dual_quats(co[ok], sq[ok], st[ok])
            return out
        mat = _blend(loc, cols, w, n, self.lbs_table).reshape(n, 3, 4)
        moved = np.einsum('nij,nj->ni', mat[:, :, :3], co) + mat[:, :, 3]
        out[ok] += (moved[ok] - contrib[ok][:, None] * co[ok]) / contrib[ok][:, None]
        return out


def _blend(loc, cols, w, n, table):
    width = table.shape[1]
    if not len(loc):
        return np.zeros((n, width))
    ncol = len(table)
    if n * ncol <= min(_DENSE_CELLS, 16 * len(loc)):
        dense = np.bincount(loc * ncol + cols, weights=w, minlength=n * ncol).reshape(n, ncol)
        return dense @ table
    out = np.empty((n, width))
    for c in range(width):
        out[:, c] = np.bincount(loc, weights=w * table[cols, c], minlength=n)
    return out


def _has_modifiers_after(obj, md) -> bool:
    after = False
    for m in obj.modifiers:
        if m == md:
            after = True
        elif after and m.show_viewport:
            return True
    return False


def _group_rows(mesh, verts, has):
    vert_ids, groups, weights = [], [], []
    vertices = mesh.vertices
    for v in np.asarray(verts).tolist():
        for g in vertices[v].groups:
            if g.group < len(has) and has[g.group]:
                vert_ids.append(v)
                groups.append(g.group)
                weights.append(g.weight)
    return (np.array(vert_ids, dtype=np.int64), np.array(groups, dtype=np.int64),
            np.array(weights, dtype=np.float64))


def armature_input(obj, md, rest, depsgraph):
    before = []
    for m in obj.modifiers:
        if m == md:
            break
        if m.show_viewport:
            before.append(m)
    if obj.data.shape_keys is None and not before:
        return rest
    off = []
    seen = False
    for m in obj.modifiers:
        if m == md:
            seen = True
        if seen and m.show_viewport:
            off.append(m)
    try:
        for m in off:
            m.show_viewport = False
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph).data
        if len(evaluated.vertices) != len(rest):
            return rest
        co = np.empty(len(rest) * 3)
        evaluated.vertices.foreach_get("co", co)
        return co.reshape(-1, 3)
    finally:
        for m in off:
            m.show_viewport = True
        depsgraph.update()
