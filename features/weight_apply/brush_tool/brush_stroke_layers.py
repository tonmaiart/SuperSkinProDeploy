
import math

import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector

from .brush_logic import (
    brush_islands, carry_bound_layers, depth_leader, layer_depths, lead_rows, raycast_at,
    stroke_layer_bind, vertex_hide_flags, weld_joined_pieces,
)
from .brush_stamps import _ISLAND_MIN_SEEN, _ISLAND_PROBES, follow_share


class StrokeLayers:
    """Methods of the Weight Brush operator; they read the stroke state its invoke sets."""

    def _carry_layers(self, gathered, p, distance_of):
        if self._bind_failed:
            return gathered
        try:
            obj = self._facade.get_obj()
            if self._layer_bind is None:
                with self._facade.profile_section("weight_brush.layer_bind"):
                    self._layer_bind = stroke_layer_bind(self._facade, self._rest_coords(),
                                                         self._posed_coords, obj.data)
                self._hidden = vertex_hide_flags(obj.data)
            graph = self._get_reach_graph(p, with_links=True)
            self._dab_pieces = graph
            piece_of = home = None
            if graph is not None:
                piece_of, piece_root = self._layer_piece_of(graph)
                if gathered:
                    home = piece_root(self._home_piece(graph, gathered))
            carried = carry_bound_layers(
                self._layer_bind, gathered, self._radius(p), distance_of, self._world_coords(),
                self._hidden, piece_of=piece_of, home_piece=home,
            )
            self._dab_leader = None if home is None else self._inner_leader(piece_of, piece_root, carried, home)
            return carried
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush layer bind failed: {exc!r}")
            self._bind_failed = True
            return gathered

    def _screen_distance(self, context, center_2d, radius_px, p):
        persp = np.array(context.region_data.perspective_matrix, dtype=np.float64)
        half_w, half_h = context.region.width / 2.0, context.region.height / 2.0
        scale = self._radius(p) / max(1e-9, radius_px)

        def distance_of(point):
            x, y, _z, w = persp @ np.append(point, 1.0)
            if w <= 0.0:
                return math.inf
            return math.hypot(half_w + half_w * x / w - center_2d[0],
                              half_h + half_h * y / w - center_2d[1]) * scale
        return distance_of

    def _follow_layers(self, changed, diff, mirror=None):
        bind, leader, graph = self._layer_bind, self._dab_leader, self._dab_pieces
        if not bind or leader is None or graph is None:
            return None
        piece_of = self._layer_piece_of(graph)[0]
        sides = [(changed, leader, self._dab_strength)]
        if mirror:
            far_leader = next((piece_of(p) for p, s in mirror.items() if piece_of(s) == leader), None)
            if far_leader is not None:
                strength = self._dab_strength
                sides.append((set(mirror), far_leader,
                              {p: strength[s] for p, s in mirror.items() if s in strength}))
        out = {}
        for verts, side_leader, strength in sides:
            rows = self._lead_rows(verts, diff, piece_of, side_leader, strength)
            if rows is None:
                return None
            out.update({v: row for v, row in rows.items() if v in verts and not self._hidden[v]})
        return out

    def _lead_rows(self, changed, diff, piece_of, leader, strength):
        strength = dict(strength)
        travel_share = follow_share(self._follow_travel)

        def progress(v, corners):
            reach = max([strength.get(v, 0.0)] + [strength.get(c, 0.0) for c in corners])
            strength[v] = reach
            return reach * travel_share

        ctx = self._ctx
        try:
            if ctx["is_mask"]:
                mask, default = ctx["mask_dict"], ctx["mask_default"]
                base = lambda v: mask.get(v, default)
                return lead_rows(self._layer_bind, changed, lambda v: diff[v] if v in diff else base(v),
                                 piece_of, leader, progress, before=base)
            layer = ctx["layer_int"]
            base = lambda v: layer.get(v, {})
            return lead_rows(self._layer_bind, changed, lambda v: diff[v] if v in diff else base(v),
                             piece_of, leader, progress, locks=ctx["locks_id"], before=base)
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush layer follow failed: {exc!r}")
            return None

    def _keep_seen_islands(self, context, gathered, p):
        volume = p.brush_projection == 'VOLUME'
        links = (self._get_layer_links(p) or None) if volume else None
        islands = brush_islands(gathered, self._facade.get_cached_mesh_neighbors(), links,
                                self._get_weld_links())
        if len(islands) < 2:
            return gathered
        seen = [self._island_seen(context, island) for island in islands]
        best = max(seen)
        if best <= 0.0:
            return gathered
        keep = set().union(*(island for island, share in zip(islands, seen)
                             if share >= _ISLAND_MIN_SEEN * best))
        return {v: d for v, d in gathered.items() if v in keep}

    def _island_seen(self, context, island):
        region, rv3d = context.region, context.region_data
        world = self._world_coords()
        ids = np.fromiter(island, dtype=np.int64, count=len(island))
        ids.sort()
        probes = ids[np.unique(np.linspace(0, len(ids) - 1, _ISLAND_PROBES).astype(np.int64))]
        obj = self._facade.get_obj()
        faces = self._bm.polygons
        seen = tried = 0
        for v in probes:
            coord = view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(world[v]))
            if coord is None:
                continue
            tried += 1
            hit, face_index, _hit_world, _normal = raycast_at(context, coord, obj, self._bvh)
            if hit and face_index is not None and 0 <= face_index < len(faces) and any(
                    u in island for u in faces[face_index].vertices):
                seen += 1
        return seen / tried if tried else 0.0

    def _inner_leader(self, piece_of, piece_root, carried, home):
        if self._layer_depths is None:
            self._layer_depths = layer_depths(self._layer_bind, piece_of, self._rest_coords(),
                                              self._facade.get_obj().data)
        return depth_leader(self._layer_depths, set(map(piece_root, set(map(piece_of, carried)))), home)

    def _home_piece(self, graph, gathered):
        if self._stroke_piece is not None:
            return self._stroke_piece
        if self._stroke_leader is None:
            faces = self._bm.polygons
            face = self._click_face
            if face is not None and 0 <= face < len(faces):
                self._stroke_leader = graph.piece_of(faces[face].vertices[0])
            else:
                self._stroke_leader = graph.piece_of(min(gathered, key=gathered.get))
        return self._stroke_leader

    def _note_click(self, context, cursor_hit):
        if self._click_face is not None:
            return
        if cursor_hit[0] == 'SCREEN':
            hit, face, _hit_world, _normal = raycast_at(context, cursor_hit[1], self._facade.get_obj(), self._bvh)
            self._click_face = face if hit and face is not None else -1
        else:
            self._click_face = -1 if cursor_hit[1] is None else cursor_hit[1]

    def _layer_piece_of(self, graph):
        if self._layer_pieces is None:
            self._layer_pieces = weld_joined_pieces(graph.piece_of, self._get_welds(),
                                                    len(self._rest_coords()))
        return self._layer_pieces
