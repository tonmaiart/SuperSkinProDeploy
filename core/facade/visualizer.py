"""VisualizerFacadeMixin — viewport HUD rendering and GPU cache invalidation.

Methods here control the shader manager and on-screen toast notifications.
They delegate to self.shader_mgr (direct CoreFacade attribute).
"""


class VisualizerFacadeMixin:
    """Mixin providing GPU draw cache invalidation and HUD toast controls."""

    def invalidate_color_only(self):
        self.shader_mgr.invalidate_color_only()

    def invalidate_and_redraw(self):
        self.shader_mgr.invalidate_and_redraw()

    def show_toast(self, text: str, duration: float = 1.0):
        self.shader_mgr.show_toast(text, duration)
