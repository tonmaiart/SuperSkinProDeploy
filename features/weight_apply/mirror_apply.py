
from .brush_tool.brush_ops import mirror_axis


def active_map(facade):
    axis = mirror_axis()
    if axis is None:
        return None
    from ..weight_toolkit.public_api import mirror_paint_map
    try:
        return mirror_paint_map(facade, axis)
    except Exception as exc:
        facade.debug_log("feature_domains", f"weight apply mirror map failed: {exc!r}")
        return None


def visible_verts(facade):
    from .brush_tool.brush_prep import visible_vertex_set
    return visible_vertex_set(facade.get_mesh())


class MirrorPlan:
    """How one operation repeats on the far side: over *selected* plus *extra* in one pass
    (`union`), or a second pass with *partner_bone* over *mirrored* from the baseline and over
    *overlap* (vertices both passes reach, seam vertices included) after the first pass."""

    def __init__(self, mmap, selected, source_of, *, extra=(), mirrored=(), overlap=(), partner_bone=None):
        self.mmap = mmap
        self.selected = list(selected)
        self.source_of = source_of
        self.extra = list(extra)
        self.mirrored = list(mirrored)
        self.overlap = list(overlap)
        self.partner_bone = partner_bone
        self.seam = [v for v, s in source_of.items() if v == s]

    @property
    def union(self):
        return self.partner_bone is None


def plan(facade, ctx, action, selected):
    if not selected:
        return None
    mmap = active_map(facade)
    if mmap is None:
        return None
    is_mask = ctx["is_mask"]
    bone = ctx.get("active_vg_id")
    partner_bone = None
    if action in ("add", "scale") and not is_mask and bone is not None:
        other = mmap.id_pairs.get(bone, bone)
        if other != bone:
            partner_bone = other
            side = mmap.bones_positive(facade, (bone,))
            if side is not None:
                kept = mmap.on_side(selected, side)
                if kept:
                    selected = kept
    partner, source = mmap.partners(selected, is_mask)
    allowed = visible_verts(facade)
    source_of = {}
    for p, s in zip(partner.tolist(), source.tolist()):
        if (allowed is None or p in allowed) and p not in source_of:
            source_of[p] = s
    chosen = set(selected)
    if partner_bone is None:
        return MirrorPlan(mmap, selected, source_of, extra=[p for p in source_of if p not in chosen])
    return MirrorPlan(mmap, selected, source_of, partner_bone=partner_bone,
                      mirrored=[p for p in source_of if p not in chosen],
                      overlap=[p for p in source_of if p in chosen])


class MirrorScope:
    """The vertices an operator runs over with Auto Mirror on (*verts* plus their visible mirror
    partners), so the far side is computed from its own weights rather than copied."""

    def __init__(self, facade, verts, plan_=None):
        self._facade = facade
        self._plan = plan_
        self.verts = set(verts) if plan_ is None else set(plan_.selected) | set(plan_.extra)

    @property
    def active(self):
        return self._plan is not None

    @property
    def far(self):
        return set() if self._plan is None else set(self._plan.extra) | set(self._plan.seam)

    def bones(self, names):
        return mirror_bone_names(self._facade, names) if self.active else set(names)

    def split_seam(self, rows, locks=None):
        if self._plan is None or not self._plan.seam:
            return
        if locks is None:
            locks = self._facade.get_locks_by_id()
        self._plan.mmap.symmetrize_seam(rows, self._plan.seam, locks)

    def split_seam_named(self, rows):
        if self._plan is None or not self._plan.seam:
            return
        bone_to_id, id_to_bone = self._facade.get_unified_mapping()
        seam = [v for v in self._plan.seam if v in rows]
        id_rows = {v: {bone_to_id[b]: w for b, w in rows[v].items() if b in bone_to_id} for v in seam}
        self.split_seam(id_rows)
        for v in seam:
            row = {b: w for b, w in rows[v].items() if b not in bone_to_id}
            row.update({id_to_bone[b]: w for b, w in id_rows[v].items() if b in id_to_bone})
            rows[v] = row


def mirror_scope(facade, verts, *, is_mask=False):
    verts = list(verts)
    return MirrorScope(facade, verts, plan(facade, {"is_mask": is_mask}, "union", verts))


def mirror_bone_names(facade, names):
    names = set(names)
    axis = mirror_axis()
    if axis is None or not names:
        return names
    from ..weight_toolkit.public_api import bone_partners
    try:
        partners = bone_partners(facade, axis)
    except Exception as exc:
        facade.debug_log("feature_domains", f"weight apply bone partners failed: {exc!r}")
        return names
    return names | {partners[n] for n in names if n in partners}
