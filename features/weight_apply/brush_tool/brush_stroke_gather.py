
import math

import numpy as np
from bpy_extras import view3d_utils

from .brush_logic import (
    LazyVertexNormals, REACH_MAX_DETOUR, faces_view, gather_brush_vertices,
    gather_brush_vertices_screen, gather_brush_vertices_volume, gather_many, keep_connected,
    keep_connected_from, keep_reachable, nearest_on_ray, posed_array, raycast_at,
    screen_mode_radius_px, stroke_side_mask, vertex_hide_flags, welded_normals,
)
from . import brush_hover
from .brush_stamps import _CIRCLE_SAMPLES, _keeps_volume


class StrokeGather:
    """Methods of the Weight Brush operator; they read the stroke state its invoke sets."""

    def _hit_at(self, context, coord, p):
        if p.brush_projection == 'SCREEN':
            return ('SCREEN', coord, screen_mode_radius_px(self._radius(p)))
        obj = self._facade.get_obj()
        hit, face_index, hit_world, hit_normal = raycast_at(context, coord, obj, self._bvh)
        if hit:
            return (p.brush_projection, face_index, hit_world, hit_normal)
        return self._air_hit(context, coord, obj, p)

    def _air_hit(self, context, coord, obj, p):
        anchor = brush_hover.off_mesh_cursor(context, coord, obj)
        if anchor is None:
            return None
        radius = self._radius(p)
        radius_px = brush_hover.off_mesh_radius_px(context, anchor[0], radius)
        if radius_px is None:
            return None
        return ('AIR', coord, radius_px, anchor[0], anchor[1], self._nearest_surface(context, coord, obj, radius))

    def _nearest_surface(self, context, coord, obj, radius):
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None or self._bvh is None:
            return None
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        if origin is None or direction is None:
            return None
        inv = obj.matrix_world.inverted()
        scale = max(1e-9, sum(obj.matrix_world.to_scale()) / 3.0)
        if self._posed_box is None:
            co = posed_array(self._posed_coords)
            self._posed_box = (co.min(axis=0), co.max(axis=0))
        pad = radius / scale
        found = nearest_on_ray(self._bvh, inv @ origin, (inv.to_3x3() @ direction).normalized(),
                               self._posed_box[0] - pad, self._posed_box[1] + pad)
        if found is None:
            return None
        location, normal, face_index, gap = found
        fade = 1.0 - gap * scale / max(1e-9, radius)
        if fade <= 0.0 or face_index is None:
            return None
        world_normal = (obj.matrix_world.to_3x3().inverted_safe().transposed() @ normal).normalized()
        return face_index, obj.matrix_world @ location, world_normal, fade

    def _keep_connected(self, gathered, face_index):
        faces = self._bm.polygons
        if face_index is not None and 0 <= face_index < len(faces):
            seeds = faces[face_index].vertices
        else:
            seeds = (min(gathered, key=gathered.get),)
        return keep_connected(gathered, seeds, self._facade.get_cached_mesh_neighbors())

    def _volume_reach(self, gathered, face_index, hit_world, p):
        faces = self._bm.polygons
        if face_index is None or not 0 <= face_index < len(faces):
            return {}
        face_verts = list(faces[face_index].vertices)
        graph = self._get_reach_graph(p, with_links=False)
        if graph is None:
            return self._keep_connected(gathered, face_index)
        if not self._on_stroke_piece(graph, face_verts[0]):
            return {}
        return self._reach_on_hit_piece(
            graph, gathered, face_verts, self._rest_origin(face_verts, hit_world),
            max_reach=REACH_MAX_DETOUR * self._local_radius(p),
        )

    def _surface_reach(self, gathered, face_index, hit_world, p):
        faces = self._bm.polygons
        if face_index is None or not 0 <= face_index < len(faces):
            return {}
        face_verts = list(faces[face_index].vertices)
        graph = self._get_reach_graph(p, with_links=False)
        if graph is None:
            return gathered
        if not self._on_stroke_piece(graph, face_verts[0]):
            return {}
        return keep_reachable(
            graph, gathered, face_verts, self._rest_origin(face_verts, hit_world),
            max_reach=REACH_MAX_DETOUR * self._local_radius(p),
        )

    def _projected_reach(self, context, center_2d, gathered, p, radius_px):
        graph = self._get_reach_graph(p, with_links=False)
        if graph is None:
            return self._projected_connected_fallback(context, center_2d, gathered)
        hits = self._circle_hits(context, center_2d, radius_px, graph,
                                 center_only=self._stroke_piece is not None)
        if self._stroke_piece is not None and not any(h[1] == self._stroke_piece for h in hits):
            hits = self._circle_hits(context, center_2d, radius_px, graph)
        if self._stroke_piece is None and hits:
            self._stroke_piece = self._dominant_piece(hits)
        on_piece = [h for h in hits if self._stroke_piece is None or h[1] == self._stroke_piece]
        if on_piece:
            _off, piece, face_index, hit_world, _depth = min(on_piece, key=lambda h: h[0])
            seeds = list(self._bm.polygons[face_index].vertices)
            self._on_stroke_piece(graph, seeds[0])
            return self._reach_on_hit_piece(graph, gathered, seeds, self._rest_origin(seeds, hit_world))
        seed = next(
            (v for v in sorted(gathered, key=gathered.get)
             if self._stroke_piece is None or self._joined_piece(graph, v) == self._stroke_piece),
            None,
        )
        if seed is None:
            return {}
        self._on_stroke_piece(graph, seed)
        return self._reach_on_hit_piece(graph, gathered, [seed], self._rest_coords()[seed])

    def _circle_hits(self, context, center_2d, radius_px, graph, center_only=False):
        obj = self._facade.get_obj()
        faces = self._bm.polygons
        origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, center_2d)
        hits = []
        for offset, angle in _CIRCLE_SAMPLES[:1] if center_only else _CIRCLE_SAMPLES:
            coord = (center_2d[0] + offset * radius_px * math.cos(angle),
                     center_2d[1] + offset * radius_px * math.sin(angle))
            hit, face_index, hit_world, _normal = raycast_at(context, coord, obj, self._bvh)
            if not hit or face_index is None or not 0 <= face_index < len(faces):
                continue
            piece = self._joined_piece(graph, faces[face_index].vertices[0])
            hits.append((offset, piece, face_index, hit_world, (hit_world - origin).length))
        return hits

    @staticmethod
    def _dominant_piece(hits):
        nearest = min(h[4] for h in hits)
        score = {}
        for offset, piece, _face, _hit, depth in hits:
            weight = (1.0 - 0.75 * offset * offset) * (nearest / max(depth, 1e-9))
            score[piece] = score.get(piece, 0.0) + weight
        return max(score, key=score.get)

    def _projected_connected_fallback(self, context, center_2d, gathered):
        if self._screen_island is not None:
            return gathered
        hit, face_index, _hit_world, _normal = raycast_at(
            context, center_2d, self._facade.get_obj(), self._bvh,
        )
        return self._keep_connected(gathered, face_index if hit else None)

    def _gather(self, context, cursor_hit, p):
        self._gather_fade = 1.0
        if cursor_hit[0] == 'AIR':
            nearest = cursor_hit[5]
            if nearest is None:
                return {}
            face_index, point, normal, fade = nearest
            self._off_mesh = True
            try:
                gathered = self._gather(context, (p.brush_projection, face_index, point, normal), p)
            finally:
                self._off_mesh = False
            self._gather_fade = fade
            return gathered
        if _keeps_volume(p):
            self._note_click(context, cursor_hit)
        if cursor_hit[0] == 'SCREEN':
            _kind, center_2d, radius_px = cursor_hit
            gathered = gather_brush_vertices_screen(
                self._get_screen_points(context), center_2d, radius_px, self._radius(p),
            )
            if p.connected_only and gathered:
                gathered = self._keep_seen_islands(context, gathered, p)
                if gathered:
                    gathered = self._projected_reach(context, center_2d, gathered, p, radius_px)
                if gathered and self._screen_island is not None:
                    gathered = keep_connected_from(
                        gathered, self._screen_island, self._facade.get_cached_mesh_neighbors(),
                        self._get_weld_links(),
                    )
                if gathered:
                    self._screen_island = set(gathered)
            if gathered and _keeps_volume(p):
                gathered = self._carry_layers(
                    gathered, p, self._screen_distance(context, center_2d, radius_px, p),
                )
            return gathered
        kind, face_index, hit_world, hit_normal = cursor_hit
        if kind == 'VOLUME':
            gathered = gather_brush_vertices_volume(self._get_world_points(), hit_world, self._radius(p))
            gathered = self._keep_stroke_side(context, gathered, face_index, hit_world, hit_normal)
            if gathered and p.connected_only:
                gathered = self._volume_reach(gathered, face_index, hit_world, p)
                if gathered:
                    gathered = self._keep_seen_islands(context, gathered, p)
            if gathered and _keeps_volume(p):
                center = np.asarray(hit_world, dtype=np.float64)
                gathered = self._carry_layers(
                    gathered, p, lambda point: float(np.linalg.norm(point - center)),
                )
            return gathered
        obj = self._facade.get_obj()
        gathered = gather_brush_vertices(
            self._facade, self._bm, face_index, hit_world, obj.matrix_world, self._radius(p),
            self._posed_coords, graph=self._get_graph(),
        )
        gathered = self._keep_stroke_side(context, gathered, face_index, hit_world, hit_normal)
        if gathered and p.connected_only:
            gathered = self._surface_reach(gathered, face_index, hit_world, p)
        return gathered

    def _keep_stroke_side(self, context, gathered, face_index, hit_world, hit_normal):
        if not gathered:
            return gathered
        ids = np.fromiter(gathered, dtype=np.int64, count=len(gathered))
        keep = self._stroke_side_mask(context, ids, face_index, hit_world, hit_normal)
        if keep is None:
            return gathered
        dist = np.fromiter(gathered.values(), dtype=np.float64, count=len(gathered))
        return dict(zip(ids[keep].tolist(), dist[keep].tolist()))

    def _stroke_side_mask(self, context, ids, face_index, hit_world, hit_normal):
        rv3d = context.region_data
        if hit_normal is None or rv3d is None or self._off_mesh:
            return None
        view = rv3d.view_matrix.inverted()
        if rv3d.is_perspective:
            eye, view_dir = np.asarray(view.translation, dtype=np.float64), None
        else:
            eye = None
            view_dir = -np.asarray(view.to_3x3().col[2], dtype=np.float64)
        if self._stroke_front is None:
            self._stroke_front = faces_view(np.asarray(hit_normal, dtype=np.float64), hit_world, eye, view_dir)
        if self._posed_normals is None:
            obj = self._facade.get_obj()
            normals = LazyVertexNormals(obj.data, self._posed_coords, obj.matrix_world)
            welds = self._get_welds() if self._get_weld_links() else None
            if welds:
                normals = welded_normals(normals.full(), welds)
            self._posed_normals = normals
        rest = reference = None
        if face_index is not None:
            rest = self._rest_normals_of()
            corners = self._facade.get_obj().data.polygons[face_index].vertices
            reference = rest[np.asarray(corners, dtype=np.int64)].sum(axis=0)
        return stroke_side_mask(ids, self._posed_normals, self._world_coords(), eye, view_dir,
                                self._stroke_front, rest, reference)

    def _rest_normals_of(self):
        if self._rest_normals is None:
            welds = self._get_welds() if self._get_weld_links() else None
            key = "_rest_normals_welded" if welds else "_rest_normals"
            coords = self._rest_coords()
            cached = self._ctx.get(key)
            if cached is None or cached[0] is not coords:
                obj = self._facade.get_obj()
                normals = LazyVertexNormals(obj.data, coords, np.identity(4))
                if welds:
                    normals = welded_normals(normals.full(), welds)
                cached = self._ctx[key] = (coords, normals)
            self._rest_normals = cached[1]
        return self._rest_normals

    def _batched_surface(self, p):
        return (p.brush_projection == 'SURFACE' and not p.connected_only and not _keeps_volume(p)
                and self._get_graph() is not None)

    def _surface_walks(self, hits, p):
        surface = [i for i, hit in enumerate(hits) if hit is not None and hit[0] == 'SURFACE']
        out = [None] * len(hits)
        if not surface:
            return out
        walked = gather_many(
            self._get_graph(), self._bm, [(hits[i][1], hits[i][2]) for i in surface],
            self._facade.get_obj().matrix_world, self._radius(p), self._posed_coords,
        )
        for i, found in zip(surface, walked):
            out[i] = found
        return out

    def _gathered_arrays(self, context, cursor_hit, p, walked, visible_only=True):
        if walked is not None:
            self._gather_fade = 1.0
            ids, dist = walked
            if len(ids):
                keep = self._stroke_side_mask(context, ids, cursor_hit[1], cursor_hit[2], cursor_hit[3])
                if keep is not None:
                    ids, dist = ids[keep], dist[keep]
        else:
            gathered = self._gather(context, cursor_hit, p)
            ids = np.fromiter(gathered.keys(), dtype=np.int64, count=len(gathered))
            dist = np.fromiter(gathered.values(), dtype=np.float64, count=len(gathered))
        if visible_only and self._visible_restrict is not None and len(ids):
            if self._visible_flags is None:
                self._visible_flags = ~vertex_hide_flags(self._bm)
            keep = self._visible_flags[ids]
            ids, dist = ids[keep], dist[keep]
        return ids, dist
