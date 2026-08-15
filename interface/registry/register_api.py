"""Unified Feature Extension API — single source-of-truth for feature domains.

Located in ``interface.registry.register_api`` — the canonical registration
API for the Interface subsystem.

This module collapses the dual-registry system (BaseDomain/DomainRegistry +
PrefsExtensionSpec/PrefsExtensionRegistry) into one cohesive interface pattern.

Architecture:
  [UI Layout Click] → SUPERSKIN_OT_execute_action (domain_id, action_id)
                    → UnifiedRegistry.get_by_id(domain_id).execute(action_id, ctx, facade)

Each feature domain implements exactly one class inheriting from
UnifiedFeatureExtension, which owns its actions, draw layout, preferences
PropertyGroup, and JSON persistence hooks in a single file.
"""

from abc import ABC, abstractmethod
import bpy


# ==============================================================================
# UnifiedFeatureExtension — Abstract Base Class
# ==============================================================================

class UnifiedFeatureExtension(ABC):
    """Single-class contract that every feature domain must fulfill.

    Subclasses own:
      - Action dispatch (what BaseDomain used to do)
      - UI layout drawing (what PrefsExtensionSpec.draw_section_fn used to do)
      - JSON persistence (populate / serialize_into)
      - Collapsible wrapper control

    Metadata is defined via **class attributes** on the subclass::

        class MyFeature(UnifiedFeatureExtension):
            domain_id = "my_feature"
            actions = ["my_action"]
            section_title = "My Feature"
            draw_tab = "SKINNING"
            collapsible = True

    Alternatively, override attributes at instance time via ``**kwargs``::

        UnifiedRegistry.register(MyFeature(section_title="Custom Title"))

    Each feature registers ONE instance via UnifiedRegistry.register().
    """

    # ── Class-level metadata attributes ────────────────────────────────────

    domain_id: str = ""
    """Stable domain identifier. Falls back to ``__class__.__name__.lower()`` if empty."""

    actions: list[str] = []
    """All action strings this domain handles. Empty list = viewer-only domain."""

    section_title: str = ""
    """Label text shown in the collapsible section header."""

    draw_tab: str | list[str] | tuple[str, ...] | set[str] = ""
    """Target workspace tab(s): ``'LAYER'``, ``'SKINNING'``, or ``'PREFERENCE'``.

    Accepts either a single string (the common case) or an iterable of
    strings for an extension that needs to render in more than one tab
    (e.g. ``draw_tab = ('LAYER', 'SKINNING')``). Normalized via
    ``get_draw_tabs()``.
    """

    json_path: tuple = None
    """JSON key path for persistence nesting. Defaults to ``(domain_id,)`` when left unset."""

    defaults_path: str | None = None
    """Absolute path to ``default_config.json``, or ``None`` for no persistence."""

    supports_dev_override: bool = False
    """``True`` if this domain's live settings may be promoted to its own
    ``default_config.json`` via the System Actions "developer override"
    action (``superskin.override_dev_defaults``). Opt-in per domain --
    default ``False`` means the domain is skipped by that action entirely."""

    supports_reset_to_default: bool = True
    """``False`` opts this domain OUT of the System Actions "Reset to
    Default" action (``superskin.reset_prefs``) -- its live settings persist
    through a reset instead of being discarded back to
    ``default_config.json``. Default ``True`` means every domain with a
    ``defaults_path`` participates, matching the pre-existing behavior;
    domains that always live-save straight to ``user.json`` (no separate
    "promote to shipped default" workflow) opt out with this flag."""

    priority: int = 100
    """Sort order within a tab. Lower values render first."""

    collapsible: bool = True
    """``True`` wraps in a collapsible panel. ``False`` for full-width viewers."""

    expanded_by_default: bool = False
    """Whether the collapsible section starts expanded. Ignored when
    ``locked_expanded`` is ``True``."""

    locked_expanded: bool = False
    """``True`` renders ``get_section_title()`` as a plain, non-interactive
    label -- no collapse arrow, not clickable -- with the body always drawn
    and never hidden. Independent of ``collapsible``/``expanded_by_default``,
    which only apply to genuinely toggleable sections. Use this for a
    section that should always stay visible but still wants a header naming
    it, instead of writing a one-off draw_section() workaround per domain."""

    show_section_label: bool = True
    """``False`` suppresses ``get_section_title()`` entirely -- no label is
    drawn above ``draw_section()``'s body, whether the section is
    ``locked_expanded`` (plain caption) or a normal collapsible panel (its
    header row is still drawn for the disclosure arrow, just without the
    title text). Use this for a full-width viewer domain that should read
    as bare artwork with no caption breaking it up (``layer_viewer``,
    ``deform_bone_viewer``)."""

    link: str | None = None
    """Optional URL. When set, an icon-only ``INFO`` button is drawn next to
    this extension's section header (in the same row as
    ``get_section_title()``, both for a normal collapsible panel header and
    for a ``locked_expanded`` plain-label header) that opens it via
    Blender's native ``wm.url_open`` operator -- see
    ``widget_preferences._draw_collapsible_box_ext()``. Has no effect when
    ``show_section_label`` is ``False`` (no header row to attach it to).
    ``None`` (the default) draws no button at all."""

    keymaps: list[dict] = []
    """Shortcuts this domain owns, for the centralized shortcut-overlay HUD
    (``interface/utils/shortcut_overlay.py``). Each entry:
    ``{"key": "Alt+3", "label": "Multi Color Preview", "mode": "Toggle"}``.
    ``"mode"`` is optional -- ``"Hold"`` for a press-and-hold/modal gesture,
    ``"Toggle"`` for a plain press-to-toggle, or omitted for anything else
    (one-shot actions, scroll steps).

    Two more optional fields make an entry dynamic for a running modal
    operator: ``"is_active"`` (a zero-arg callable returning ``bool``,
    checked on every HUD redraw) and ``"sub_keymaps"`` (a list of entries in
    this same shape). While ``is_active()`` returns ``True``, the HUD shows
    ``sub_keymaps`` in place of this entry -- e.g. bone_picker's "Alt+2"
    entry expands into its live Left/Middle/Right-click and Release
    sub-gestures only while the modal is actually running. Purely
    declarative -- does NOT register the keymap itself; the domain's own
    ``keymap.py`` (or modal operator) still owns the actual input handling.
    This list only feeds the HUD."""

    # ── Constructor ────────────────────────────────────────────────────────

    def __init__(self, **kwargs):
        """Override class attributes at instance time via keyword arguments.

        Only attributes that already exist on the class are set; unknown keys
        are silently ignored so that subclasses with custom attributes can
        pass them through without error.
        """
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)

    # ── Identity ──────────────────────────────────────────────────────────

    def get_id(self) -> str:
        """Stable domain identifier.

        Reads from ``self.domain_id``. Falls back to the lowercased class name
        when the attribute is empty.
        """
        if self.domain_id:
            return self.domain_id
        return self.__class__.__name__.lower()

    def get_actions(self) -> list[str]:
        """All action strings this domain handles.

        Reads from ``self.actions``. Return an empty list for viewer-only
        domains with no executable actions.
        """
        return self.actions

    def get_priority(self) -> int:
        """Sort priority within a tab. Lower values render first.

        Reads from ``self.priority``.
        """
        return self.priority

    def get_keymaps(self) -> list[dict]:
        """Shortcuts this domain declares for the shortcut-overlay HUD.

        Reads from ``self.keymaps``.
        """
        return self.keymaps

    # ── UI metadata ───────────────────────────────────────────────────────

    def get_section_title(self) -> str:
        """Label text shown in the collapsible section header.

        Reads from ``self.section_title``.
        """
        return self.section_title

    def get_draw_tabs(self) -> set[str]:
        """Normalized set of workspace tabs this extension renders in.

        Reads from ``self.draw_tab``, which may be a single string (e.g.
        ``'SKINNING'``) or an iterable of strings (e.g.
        ``('LAYER', 'SKINNING')``) for extensions that need to appear in
        more than one interface state. An empty string yields an empty set
        (the extension renders nowhere).
        """
        value = self.draw_tab
        if isinstance(value, str):
            return {value} if value else set()
        return set(value)

    def get_json_path(self) -> tuple:
        """JSON key path used for persistence nesting.

        Reads from ``self.json_path`` when set, otherwise defaults to
        ``(self.get_id(),)``. Override with a nested tuple like
        ``("customize", "bone_picker")``.
        """
        if self.json_path is not None:
            return self.json_path
        return (self.get_id(),)

    def get_defaults_path(self) -> str | None:
        """Absolute path to ``default_config.json``, or ``None``.

        Reads from ``self.defaults_path``.
        """
        return self.defaults_path

    def supports_developer_override(self) -> bool:
        """Whether this domain opts into the "developer override" System
        Actions button (promoting current live values to its own
        ``default_config.json``).

        Reads from ``self.supports_dev_override``.
        """
        return self.supports_dev_override

    def supports_reset_to_default_action(self) -> bool:
        """Whether this domain participates in the System Actions "Reset to
        Default" button.

        Reads from ``self.supports_reset_to_default``.
        """
        return self.supports_reset_to_default

    def is_collapsible(self) -> bool:
        """Whether the section is wrapped in a collapsible panel header.

        Reads from ``self.collapsible``. Return ``False`` for primary viewer
        domains that need full-width rendering.
        """
        return self.collapsible

    def is_expanded_by_default(self) -> bool:
        """Whether the collapsible section starts expanded.

        Reads from ``self.expanded_by_default``.
        """
        return self.expanded_by_default

    def is_locked_expanded(self) -> bool:
        """Whether this section is permanently expanded behind a plain
        label header instead of a collapsible one.

        Reads from ``self.locked_expanded``.
        """
        return self.locked_expanded

    def is_section_label_shown(self) -> bool:
        """Whether ``get_section_title()`` should be drawn as a header/label
        above this section's body.

        Reads from ``self.show_section_label``.
        """
        return self.show_section_label

    def get_link(self) -> str | None:
        """URL opened by the section header's icon-only ``INFO`` button, or
        ``None`` to draw no button.

        Reads from ``self.link``.
        """
        return self.link

    # ── Action dispatch ───────────────────────────────────────────────────

    @abstractmethod
    def execute(self, action: str, context, core_facade) -> dict:
        """Run *action* and return ``{'status': 'FINISHED'|'CANCELLED', ...}``."""
        ...

    # ── UI layout ─────────────────────────────────────────────────────────

    @abstractmethod
    def draw_section(self, layout, context) -> None:
        """Draw the full section body for this domain.

        Called inside the collapsible wrapper (or flat, depending on
        ``is_collapsible()``). The implementation may access
        ``context.window_manager.superskin_<id>_prefs`` as needed.

        For an extension registered under more than one tab (``draw_tab``
        as an iterable) that needs DIFFERENT content per tab, override
        ``draw_section_for_tab()`` instead — its default implementation
        just calls this method unchanged, so single-tab extensions (the
        common case) never need to know it exists.
        """
        ...

    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        """Draw the section body for a specific *tab_key*.

        Default implementation ignores *tab_key* and delegates to
        ``draw_section()``. Override this instead of ``draw_section()``
        when an extension is registered under multiple tabs (see
        ``draw_tab``) and needs to render different content in each one —
        e.g.::

            def draw_section_for_tab(self, layout, context, tab_key):
                if tab_key == 'LAYER':
                    layout.label(text="Layer-only controls")
                elif tab_key == 'SKINNING':
                    layout.label(text="Skinning-only controls")
                else:
                    self.draw_section(layout, context)
        """
        self.draw_section(layout, context)

    # ── JSON persistence hooks ────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write *data* (subsection of user.json) into live WindowManager properties.

        Called by PreferencesService on load. Default is a no-op.
        """
        pass

    def serialize_into(self, full_dict: dict) -> None:
        """Write current live property values back into *full_dict*.

        Called by PreferencesService on save. Default is a no-op.
        """
        pass


# ==============================================================================
# UnifiedRegistry — singleton registration and lookup
# ==============================================================================

class UnifiedRegistry:
    """Central registry mapping ``domain_id`` → ``UnifiedFeatureExtension`` instance.

    Replaces both ``DomainRegistry`` (action dispatch) and
    ``PrefsExtensionRegistry`` (UI layout + persistence).

    Usage::

        UnifiedRegistry.register(MyFeature())

        # In widget_preferences:
        for ext in UnifiedRegistry.get_by_tab('SKINNING'):
            if ext.is_collapsible():
                _draw_collapsible_box(layout, ext.get_section_title(),
                                      lambda box: ext.draw_section_for_tab(box, context, 'SKINNING'))
    """

    _extensions: dict[str, UnifiedFeatureExtension] = {}
    _action_map: dict[str, str] = {}  # action_str → domain_id
    _expanded_props_registered: set[str] = set()

    # ── Registration ──────────────────────────────────────────────────────

    @classmethod
    def register(cls, extension: UnifiedFeatureExtension) -> None:
        """Register or replace a feature extension.

        Automatically creates a ``WindowManager.superskin_<id>_expanded``
        BoolProperty so that collapsible-panel state persists across
        redraw cycles without manual property management.
        """
        did = extension.get_id()

        # Remove old action mappings
        if did in cls._extensions:
            old = cls._extensions[did]
            for a in old.get_actions():
                cls._action_map.pop(a, None)

        cls._extensions[did] = extension
        for action in extension.get_actions():
            cls._action_map[action] = did

        cls._ensure_expanded_prop(did)

    @classmethod
    def unregister(cls, domain_id: str) -> None:
        """Remove a feature extension and its action mappings."""
        ext = cls._extensions.pop(domain_id, None)
        if ext:
            for action in ext.get_actions():
                cls._action_map.pop(action, None)
        cls._remove_expanded_prop(domain_id)

    # ── Lookup ────────────────────────────────────────────────────────────

    @classmethod
    def get_by_id(cls, domain_id: str) -> UnifiedFeatureExtension | None:
        """Return the registered extension instance for *domain_id*, or None."""
        return cls._extensions.get(domain_id)

    @classmethod
    def get_by_tab(cls, tab_key: str) -> list[UnifiedFeatureExtension]:
        """Return all extensions registered for *tab_key*.

        Viewer domains (``is_collapsible() == False``) are returned first
        so they render at the top of the tab.

        Backward compatibility: ``'CUSTOMIZE'`` is automatically mapped to
        ``'PREFERENCE'`` so that extensions registered with the legacy tab
        key continue to resolve correctly.
        """
        # Backward compatibility: map legacy CUSTOMIZE → PREFERENCE
        if tab_key == "CUSTOMIZE":
            tab_key = "PREFERENCE"
        matching = [e for e in cls._extensions.values() if tab_key in e.get_draw_tabs()]
        matching.sort(key=lambda e: (0 if not e.is_collapsible() else 1, e.get_priority(), e.get_id()))
        return matching

    @classmethod
    def get_all(cls) -> list[UnifiedFeatureExtension]:
        """Return every registered extension."""
        return list(cls._extensions.values())

    @classmethod
    def has_action(cls, action: str) -> bool:
        """True if *action* is registered by any domain."""
        return action in cls._action_map

    @classmethod
    def get_all_actions(cls) -> list[str]:
        """Return every registered action string."""
        return list(cls._action_map.keys())

    # ── Execution ─────────────────────────────────────────────────────────

    @classmethod
    def execute(cls, domain_id: str, action: str, context,
                core_facade) -> dict:
        """Forward *action* to the extension identified by *domain_id*.

        Raises ValueError if the domain_id is not registered.
        """
        ext = cls._extensions.get(domain_id)
        if not ext:
            raise ValueError(
                f"[UnifiedRegistry] No extension registered for domain_id: {domain_id!r}"
            )
        return ext.execute(action, context, core_facade)

    @classmethod
    def execute_by_action(cls, action: str, context, core_facade) -> dict:
        """Forward *action* to whichever domain registered it.

        Raises ValueError if no domain handles the action.
        """
        did = cls._action_map.get(action)
        if not did:
            raise ValueError(
                f"[UnifiedRegistry] No domain registered for action: {action!r}"
            )
        return cls._extensions[did].execute(action, context, core_facade)

    # ── Expanded-state helpers ────────────────────────────────────────────

    @classmethod
    def _ensure_expanded_prop(cls, domain_id: str) -> None:
        """Dynamically register ``superskin_<id>_expanded`` on WindowManager."""
        prop_name = f"superskin_{domain_id}_expanded"
        if prop_name in cls._expanded_props_registered:
            return
        if hasattr(bpy.types.WindowManager, prop_name):
            cls._expanded_props_registered.add(prop_name)
            return
        setattr(bpy.types.WindowManager, prop_name,
                bpy.props.BoolProperty(
                    name=f"{domain_id} Expanded",
                    default=False,
                    options={'SKIP_SAVE'},
                ))
        cls._expanded_props_registered.add(prop_name)

    @classmethod
    def _remove_expanded_prop(cls, domain_id: str) -> None:
        """Remove the dynamically registered expanded prop if it exists."""
        prop_name = f"superskin_{domain_id}_expanded"
        if hasattr(bpy.types.WindowManager, prop_name):
            try:
                delattr(bpy.types.WindowManager, prop_name)
            except Exception:
                pass
        cls._expanded_props_registered.discard(prop_name)

    @classmethod
    def is_expanded(cls, context, domain_id: str) -> bool:
        """Read the expanded state for *domain_id* from WindowManager."""
        prop_name = f"superskin_{domain_id}_expanded"
        return bool(getattr(context.window_manager, prop_name, False))


# ==============================================================================
# Universal Action Proxy Operator
# ==============================================================================

class SUPERSKIN_OT_execute_action(bpy.types.Operator):
    """Universal proxy operator that routes UI clicks to the correct feature domain.

    Layout buttons use this single operator with ``domain_id`` and ``action_id``
    properties instead of defining one operator class per action.

    Example::

        op = layout.operator("superskin.execute_action", text="Mirror Weights")
        op.domain_id = "mirror"
        op.action_id = "mirror"
    """
    bl_idname = "superskin.execute_action"
    bl_label = "Execute Feature Action"
    bl_options = {'REGISTER', 'UNDO'}

    domain_id: bpy.props.StringProperty(
        name="Domain ID",
        description="Feature domain identifier (e.g. 'mirror', 'clipboard')",
    )
    action_id: bpy.props.StringProperty(
        name="Action ID",
        description="Action to execute within the domain (e.g. 'mirror', 'copy')",
    )

    def execute(self, context):
        from ...core.facade import CoreFacade

        if not self.domain_id:
            self.report({'ERROR'}, "domain_id is required")
            return {'CANCELLED'}

        context.scene.superskin_internal_transaction = True
        try:
            facade = CoreFacade(context)
            result = UnifiedRegistry.execute(
                self.domain_id, self.action_id, context, facade,
            )
            if result.get("status") == "CANCELLED":
                msg = result.get("message", "")
                if msg:
                    self.report({'WARNING'}, msg)
                return {'CANCELLED'}
            return {'FINISHED'}
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            context.scene.superskin_internal_transaction = False


# ==============================================================================
# Registration helpers
# ==============================================================================

_operator_class = SUPERSKIN_OT_execute_action


def register_operator():
    """Register the universal proxy operator. Called from __init__.py."""
    bpy.utils.register_class(_operator_class)


def unregister_operator():
    """Unregister the universal proxy operator. Called from __init__.py."""
    bpy.utils.unregister_class(_operator_class)
