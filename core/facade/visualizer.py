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

    def show_report(self, text: str, *, hold: float = 2.0, fade: float = 1.0,
                     color: tuple = (1.0, 0.8, 0.0, 1.0)):
        self.shader_mgr.show_report(text, hold=hold, fade=fade, color=color)
