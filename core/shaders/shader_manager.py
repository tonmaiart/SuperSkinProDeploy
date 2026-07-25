"""Shader manager for SuperSkinPro — stateless HUD toast dispatcher and viewport redraw helper.

After the Phase 2 refactor, this module no longer owns any GPU draw handles for
bone weight or mask visualization.  Those responsibilities have been moved to:
  - Blender's native Vertex Group Weight Overlay (Single-Bone and Mask modes)
  - features/multi_color_preview/ (MULTI rainbow mode)

What remains here:
  - HUD toast notifications (show_toast)
  - invalidate_color_only / invalidate_and_redraw  (tag_redraw wrappers)
  - bump_deform_generation / get_deform_generation (staleness counter for feature draw callbacks)
  - force_viewport_redraw
"""

import bpy
import blf


# ═══════════════════════════════════════════════════════════════════════════
#  Transient HUD Toast
# ═══════════════════════════════════════════════════════════════════════════

_toast_state = {"text": None, "draw_handle": None}


def _toast_draw_callback():
    text = _toast_state["text"]
    if not text:
        return
    context = bpy.context
    if not context.space_data or context.space_data.type != 'VIEW_3D':
        return

    font_id = 0
    blf.size(font_id, 20)
    region_width = context.region.width
    text_w, _ = blf.dimensions(font_id, text)
    cx = max(0, region_width // 2 - int(text_w) // 2)
    cy = context.region.height - 60

    blf.position(font_id, cx + 2, cy - 2, 0)
    blf.color(font_id, 0.0, 0.0, 0.0, 0.85)
    blf.draw(font_id, text)
    blf.position(font_id, cx, cy, 0)
    blf.color(font_id, 1.0, 0.3, 0.3, 1.0)
    blf.draw(font_id, text)


def _toast_clear(expected_text):
    if _toast_state["text"] != expected_text:
        return
    _toast_state["text"] = None
    if _toast_state["draw_handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_toast_state["draw_handle"], 'WINDOW')
        _toast_state["draw_handle"] = None
    ShaderManager._tag_viewport_redraw()


# ═══════════════════════════════════════════════════════════════════════════
#  ShaderManager
# ═══════════════════════════════════════════════════════════════════════════

class ShaderManager:
    """Stateless macro-dispatcher for viewport redraws and HUD toasts."""

    # Deform-generation counter — formerly in visualizer_base.py.
    # Incremented by pipeline.finish() so GPU draw callbacks can detect
    # weight changes at the same Blender frame without importing from core.
    _deform_generation: int = 0

    # ── Public API ──

    @classmethod
    def bump_deform_generation(cls) -> int:
        cls._deform_generation += 1
        return cls._deform_generation

    @classmethod
    def get_deform_generation(cls) -> int:
        return cls._deform_generation

    @classmethod
    def invalidate_color_only(cls):
        cls._tag_viewport_redraw()

    def invalidate_and_redraw(self):
        self.__class__._tag_redraw_all_areas()

    @staticmethod
    def force_viewport_redraw():
        ShaderManager._tag_viewport_redraw()

    def show_toast(self, text, duration=1.0):
        """Display a short-lived auto-dismissing HUD notification."""
        _toast_state["text"] = text
        if _toast_state["draw_handle"] is None:
            _toast_state["draw_handle"] = bpy.types.SpaceView3D.draw_handler_add(
                _toast_draw_callback, (), 'WINDOW', 'POST_PIXEL'
            )
        self.__class__._tag_viewport_redraw()
        bpy.app.timers.register(lambda: _toast_clear(text), first_interval=duration)

    # ── Internal helpers ──

    @staticmethod
    def _tag_viewport_redraw():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    @staticmethod
    def _tag_redraw_all_areas():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'PROPERTIES', 'UI'}:
                    area.tag_redraw()


def register():
    pass


def unregister():
    if _toast_state["draw_handle"] is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_toast_state["draw_handle"], 'WINDOW')
        except Exception:
            pass
        _toast_state["draw_handle"] = None
    _toast_state["text"] = None
    ShaderManager._deform_generation = 0
