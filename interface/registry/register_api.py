"""Unified Feature Extension API — single source-of-truth for feature domains."""

from abc import ABC, abstractmethod
import bpy


# ==============================================================================
# UnifiedFeatureExtension — Abstract Base Class
# ==============================================================================

class UnifiedFeatureExtension(ABC):
    """Single-class contract that every feature domain must fulfill."""

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
    ``widget_preferences.draw_collapsible_box_ext()``. Has no effect when
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
        """Override class attributes at instance time via keyword arguments."""
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)

    # ── Identity ──────────────────────────────────────────────────────────

    def get_id(self) -> str:
        """Stable domain identifier."""
        if self.domain_id:
            return self.domain_id
        return self.__class__.__name__.lower()

    def get_actions(self) -> list[str]:
        """All action strings this domain handles."""
        return self.actions

    def get_priority(self) -> int:
        """Sort priority within a tab. Lower values render first."""
        return self.priority

    def get_keymaps(self) -> list[dict]:
        """Shortcuts this domain declares for the shortcut-overlay HUD."""
        return self.keymaps

    # ── UI metadata ───────────────────────────────────────────────────────

    def get_section_title(self) -> str:
        """Label text shown in the collapsible section header."""
        return self.section_title

    def get_draw_tabs(self) -> set[str]:
        """Normalized set of workspace tabs this extension renders in."""
        value = self.draw_tab
        if isinstance(value, str):
            return {value} if value else set()
        return set(value)

    def get_json_path(self) -> tuple:
        """JSON key path used for persistence nesting."""
        if self.json_path is not None:
            return self.json_path
        return (self.get_id(),)

    def get_defaults_path(self) -> str | None:
        """Absolute path to ``default_config.json``, or ``None``."""
        return self.defaults_path

    def supports_developer_override(self) -> bool:
        """Whether this domain opts into the "developer override" System Actions button
        (promoting current live values to its own ``default_config.json``)."""
        return self.supports_dev_override

    def supports_reset_to_default_action(self) -> bool:
        """Whether this domain participates in the System Actions "Reset to Default" button."""
        return self.supports_reset_to_default

    def is_collapsible(self) -> bool:
        """Whether the section is wrapped in a collapsible panel header."""
        return self.collapsible

    def is_expanded_by_default(self) -> bool:
        """Whether the collapsible section starts expanded."""
        return self.expanded_by_default

    def is_locked_expanded(self) -> bool:
        """Whether this section is permanently expanded behind a plain label header instead of
        a collapsible one."""
        return self.locked_expanded

    def is_section_label_shown(self) -> bool:
        """Whether ``get_section_title()`` should be drawn as a header/label above this
        section's body."""
        return self.show_section_label

    def get_link(self) -> str | None:
        """URL opened by the section header's icon-only ``INFO`` button, or ``None`` to draw no
        button."""
        return self.link

    def get_keymap_items(self) -> list:
        """Return this domain's registered keymap items for the in-panel shortcut editor."""
        return []

    # ── Action dispatch ───────────────────────────────────────────────────

    @abstractmethod
    def execute(self, action: str, context, core_facade) -> dict:
        """Run *action* and return ``{'status': 'FINISHED'|'CANCELLED', ...}``."""
        ...

    # ── UI layout ─────────────────────────────────────────────────────────

    @abstractmethod
    def draw_section(self, layout, context) -> None:
        """Draw the full section body for this domain."""
        ...

    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        """Draw the section body for a specific *tab_key*."""
        self.draw_section(layout, context)

    # ── JSON persistence hooks ────────────────────────────────────────────

    def populate(self, data: dict) -> None:
        """Write *data* (subsection of user.json) into live WindowManager properties."""
        pass

    def serialize_into(self, full_dict: dict) -> None:
        """Write current live property values back into *full_dict*."""
        pass


# ==============================================================================
# UnifiedRegistry — singleton registration and lookup
# ==============================================================================

class UnifiedRegistry:
    """Central registry mapping ``domain_id`` → ``UnifiedFeatureExtension`` instance."""

    _extensions: dict[str, UnifiedFeatureExtension] = {}
    _action_map: dict[str, str] = {}  # action_str → domain_id
    _expanded_props_registered: set[str] = set()

    # ── Registration ──────────────────────────────────────────────────────

    @classmethod
    def register(cls, extension: UnifiedFeatureExtension) -> None:
        """Register or replace a feature extension."""
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
        """Return all extensions registered for *tab_key*."""
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
        """Forward *action* to the extension identified by *domain_id*."""
        ext = cls._extensions.get(domain_id)
        if not ext:
            raise ValueError(
                f"[UnifiedRegistry] No extension registered for domain_id: {domain_id!r}"
            )
        return ext.execute(action, context, core_facade)

    @classmethod
    def execute_by_action(cls, action: str, context, core_facade) -> dict:
        """Forward *action* to whichever domain registered it."""
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

    # ── Settings / How-to-use toggle row ─────────────────────────────────

    @classmethod
    def draw_settings_toggle_row(cls, layout, context) -> None:
        """Draw the "Settings"/"How to use" toggle-button row (plus their expanded bodies)
        wherever *layout* currently is."""
        from ...core.facade import CoreFacade
        from ..widget_preferences import draw_settings_toggle_row as _draw

        _draw(layout, context, CoreFacade.is_system_activated())

    # ── Collapsible-section chrome (for object_tools_ui / skin_tools_ui) ──

    @classmethod
    def draw_collapsible_section(cls, layout, context, extension: "UnifiedFeatureExtension", tab_key: str, draw_body=None) -> None:
        """Draw *extension*'s section under *tab_key* using the same shared chrome every
        collapsible section in this addon has always used."""
        from ..widget_preferences import draw_collapsible_box_ext as _draw

        _draw(layout, context, extension, tab_key, draw_body=draw_body)


# ==============================================================================
# Universal Action Proxy Operator
# ==============================================================================

class SUPERSKIN_OT_execute_action(bpy.types.Operator):
    """Universal proxy operator that routes UI clicks to the correct feature domain."""
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
