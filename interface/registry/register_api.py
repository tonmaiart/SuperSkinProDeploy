
from abc import ABC, abstractmethod
import bpy



class UnifiedFeatureExtension(ABC):
    """Single-class contract that every feature domain must fulfill."""


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
    """HUD-only shortcut declarations; schema in ``docs/core-interfaces/interface.md``
    ("Shortcut HUD")."""


    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key) and value is not None:
                setattr(self, key, value)


    def get_id(self) -> str:
        if self.domain_id:
            return self.domain_id
        return self.__class__.__name__.lower()

    def get_actions(self) -> list[str]:
        return self.actions

    def get_priority(self) -> int:
        return self.priority

    def get_keymaps(self) -> list[dict]:
        return self.keymaps


    def get_section_title(self) -> str:
        return self.section_title

    def get_draw_tabs(self) -> set[str]:
        value = self.draw_tab
        if isinstance(value, str):
            return {value} if value else set()
        return set(value)

    def get_json_path(self) -> tuple:
        if self.json_path is not None:
            return self.json_path
        return (self.get_id(),)

    def get_defaults_path(self) -> str | None:
        return self.defaults_path

    def supports_developer_override(self) -> bool:
        return self.supports_dev_override

    def supports_reset_to_default_action(self) -> bool:
        return self.supports_reset_to_default

    def is_collapsible(self) -> bool:
        return self.collapsible

    def is_expanded_by_default(self) -> bool:
        return self.expanded_by_default

    def is_locked_expanded(self) -> bool:
        return self.locked_expanded

    def is_section_label_shown(self) -> bool:
        return self.show_section_label

    def get_link(self) -> str | None:
        return self.link

    def get_keymap_items(self) -> list:
        return []


    @abstractmethod
    def execute(self, action: str, context, core_facade) -> dict:
        ...


    @abstractmethod
    def draw_section(self, layout, context) -> None:
        ...

    def draw_section_for_tab(self, layout, context, tab_key: str) -> None:
        self.draw_section(layout, context)


    def populate(self, data: dict) -> None:
        pass

    def serialize_into(self, full_dict: dict) -> None:
        pass



class UnifiedRegistry:
    """Central registry mapping ``domain_id`` → ``UnifiedFeatureExtension`` instance."""

    _extensions: dict[str, UnifiedFeatureExtension] = {}
    _action_map: dict[str, str] = {}
    _expanded_props_registered: set[str] = set()


    @classmethod
    def register(cls, extension: UnifiedFeatureExtension) -> None:
        did = extension.get_id()

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
        ext = cls._extensions.pop(domain_id, None)
        if ext:
            for action in ext.get_actions():
                cls._action_map.pop(action, None)
        cls._remove_expanded_prop(domain_id)


    @classmethod
    def get_by_id(cls, domain_id: str) -> UnifiedFeatureExtension | None:
        return cls._extensions.get(domain_id)

    @classmethod
    def get_by_tab(cls, tab_key: str) -> list[UnifiedFeatureExtension]:
        if tab_key == "CUSTOMIZE":
            tab_key = "PREFERENCE"
        matching = [e for e in cls._extensions.values() if tab_key in e.get_draw_tabs()]
        matching.sort(key=lambda e: (0 if not e.is_collapsible() else 1, e.get_priority(), e.get_id()))
        return matching

    @classmethod
    def get_all(cls) -> list[UnifiedFeatureExtension]:
        return list(cls._extensions.values())

    @classmethod
    def has_action(cls, action: str) -> bool:
        return action in cls._action_map

    @classmethod
    def get_all_actions(cls) -> list[str]:
        return list(cls._action_map.keys())


    @classmethod
    def execute(cls, domain_id: str, action: str, context,
                core_facade) -> dict:
        ext = cls._extensions.get(domain_id)
        if not ext:
            raise ValueError(
                f"[UnifiedRegistry] No extension registered for domain_id: {domain_id!r}"
            )
        return ext.execute(action, context, core_facade)

    @classmethod
    def execute_by_action(cls, action: str, context, core_facade) -> dict:
        did = cls._action_map.get(action)
        if not did:
            raise ValueError(
                f"[UnifiedRegistry] No domain registered for action: {action!r}"
            )
        return cls._extensions[did].execute(action, context, core_facade)


    @classmethod
    def _ensure_expanded_prop(cls, domain_id: str) -> None:
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
        prop_name = f"superskin_{domain_id}_expanded"
        if hasattr(bpy.types.WindowManager, prop_name):
            try:
                delattr(bpy.types.WindowManager, prop_name)
            except Exception:
                pass
        cls._expanded_props_registered.discard(prop_name)

    @classmethod
    def is_expanded(cls, context, domain_id: str) -> bool:
        prop_name = f"superskin_{domain_id}_expanded"
        return bool(getattr(context.window_manager, prop_name, False))


    @classmethod
    def draw_settings_toggle_row(cls, layout, context) -> None:
        from ..widget_preferences import draw_settings_toggle_row as _draw

        _draw(layout, context)


    @classmethod
    def draw_collapsible_section(cls, layout, context, extension: "UnifiedFeatureExtension", tab_key: str, draw_body=None) -> None:
        from ..widget_preferences import draw_collapsible_box_ext as _draw

        _draw(layout, context, extension, tab_key, draw_body=draw_body)



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



_operator_class = SUPERSKIN_OT_execute_action


def register_operator():
    bpy.utils.register_class(_operator_class)


def unregister_operator():
    bpy.utils.unregister_class(_operator_class)
