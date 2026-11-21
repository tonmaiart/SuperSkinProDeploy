
import numpy as np
from mathutils import Vector

from .brush_logic import (
    build_brush_graph, build_reach_graph, build_screen_points, build_world_points,
    coincident_welds, joined_region, keep_reachable, layer_links, posed_array, seam_pin_rows,
    smooth_links, vertex_hide_flags, weld_links,
)
from .brush_stamps import local_brush_radius
from .brush_prefs import get_brush_prefs


class StrokeLookups:
    """Methods of the Weight Brush operator; they read the stroke state its invoke sets."""

    def _get_screen_points(self, context):
        if self._screen_points is None:
            obj = self._facade.get_obj()
            self._screen_points = build_screen_points(
                obj.matrix_world, context.region, context.region_data, self._posed_coords,
                hidden_vertices=vertex_hide_flags(obj.data),
            )
        return self._screen_points

    def _get_world_points(self):
        if self._world_points is None:
            obj = self._facade.get_obj()
            self._world_points = build_world_points(
                obj.matrix_world, self._posed_coords, hidden_vertices=vertex_hide_flags(obj.data),
            )
        return self._world_points

    def _world_coords(self):
        if self._world_coords_cache is None:
            m = np.array(self._facade.get_obj().matrix_world, dtype=np.float64)
            co = posed_array(self._posed_coords)
            self._world_coords_cache = co @ m[:3, :3].T + m[:3, 3]
        return self._world_coords_cache

    def _rest_coords(self):
        coords = self._ctx.get("_coords_cache")
        if coords is None:
            coords = self._ctx["_coords_cache"] = self._facade.get_vertex_coordinates()
        return coords

    def _get_bind_links(self):
        if self._bind_links is None:
            self._bind_links = smooth_links(self._facade, self._layer_bind, self._rest_coords(),
                                            self._facade.get_obj().data)
        return self._bind_links or None

    def _get_welds(self):
        try:
            return coincident_welds(self._facade, self._rest_coords()) or None
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush seam welds failed: {exc!r}")
            return None

    def _get_weld_links(self):
        if not get_brush_prefs().keep_volume:
            return None
        welds = self._get_welds()
        return weld_links(welds) if welds else None

    def _get_layer_links(self, p):
        if self._layer_links is None:
            try:
                self._layer_links = layer_links(self._facade, self._rest_coords(),
                                                local_brush_radius(self._facade.get_obj(), p.brush_radius))
            except Exception as exc:
                self._facade.debug_log("feature_domains", f"weight brush layer links failed: {exc!r}")
                self._layer_links = {}
        return self._layer_links

    def _local_radius(self, p):
        return local_brush_radius(self._facade.get_obj(), self._radius(p))

    def _get_reach_graph(self, p, with_links):
        if self._reach_failed:
            return None
        graph = self._reach_graphs.get(with_links)
        if graph is not None:
            return graph
        try:
            links = (self._get_layer_links(p) or None) if with_links else None
            graph = build_reach_graph(self._facade, self._rest_coords(), links)
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush reach graph failed: {exc!r}")
            self._reach_failed = True
            return None
        self._reach_graphs[with_links] = graph
        return graph

    def _reach_on_hit_piece(self, graph, gathered, seeds, origin, **kwargs):
        hit = graph.piece_of(seeds[0])
        own = keep_reachable(graph, gathered, seeds, origin, piece=hit, **kwargs)
        links = self._get_weld_links()
        if not own or not links or not any(v in links for v in gathered):
            return own
        seam_graph = self._get_seam_graph()
        if seam_graph is None:
            return own
        reach = keep_reachable(seam_graph, gathered, seeds, origin, **kwargs)
        candidates = {v for v in reach if v in own or graph.piece_of(v) != hit}
        region = joined_region(own, candidates, self._facade.get_cached_mesh_neighbors(), links,
                               self._row_reader())
        return {v: reach.get(v, own.get(v)) for v in region}

    def _get_seam_graph(self):
        graph = self._reach_graphs.get("welds")
        if graph is not None or self._reach_failed:
            return graph
        try:
            graph = build_reach_graph(self._facade, self._rest_coords(), self._get_weld_links())
        except Exception as exc:
            self._facade.debug_log("feature_domains", f"weight brush seam graph failed: {exc!r}")
            self._reach_failed = True
            return None
        self._reach_graphs["welds"] = graph
        return graph

    def _row_reader(self):
        ctx = self._ctx
        if ctx["is_mask"]:
            mask, default = ctx["mask_dict"], ctx["mask_default"]
            return lambda v: mask.get(v, default)
        layer = ctx["layer_int"]
        return lambda v: layer.get(v, {})

    def _dab_welds(self, changed):
        links = self._get_weld_links()
        if not links:
            return None
        welds = self._get_welds()
        return {v: welds[v] for v in changed
                if v in links and all(u in changed for u in links[v])} or None

    def _pin_seam(self, changed):
        links = self._get_weld_links()
        if not links:
            return None
        locks = None if self._ctx["is_mask"] else self._ctx["locks_id"]
        return seam_pin_rows(changed, self._row_reader(), links, locks)

    def _joined_piece(self, graph, v):
        if not get_brush_prefs().keep_volume:
            return graph.piece_of(v)
        return self._layer_piece_of(graph)[0](v)

    def _on_stroke_piece(self, graph, v):
        piece = self._joined_piece(graph, v)
        if self._stroke_piece is None:
            self._stroke_piece = piece
        return piece == self._stroke_piece

    def _rest_origin(self, face_verts, hit_world):
        matrix = self._facade.get_obj().matrix_world
        rest = self._rest_coords()
        weights = [
            1.0 / max(1e-9, (matrix @ Vector(self._posed_coords[v]) - hit_world).length)
            for v in face_verts
        ]
        total = sum(weights)
        return tuple(
            sum(rest[v][i] * w for v, w in zip(face_verts, weights)) / total for i in range(3)
        )

    def _get_graph(self):
        if self._graph is None and not self._graph_failed:
            try:
                self._graph = build_brush_graph(self._facade, self._posed_coords)
            except ValueError:
                raise
            except Exception as exc:
                self._facade.debug_log("feature_domains", f"weight brush graph build failed: {exc!r}")
                self._graph = None
            self._graph_failed = self._graph is None
        return self._graph
