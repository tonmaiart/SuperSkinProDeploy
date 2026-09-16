"""Shared "[Object -> Pose -> Edit Bone]" cycle button + "Edit Mask" widget.

Draws ONE button standing in for Object Mode, Pose Mode, and the Edit
Layer Weight session's Bone sub-mode as three positions of a single
forward cycle -- clicking it always advances Object -> Pose -> Edit Bone
-> Object, regardless of which position it starts from (2026-09-13, per
explicit user request merging what used to be a combined "Object"/"Pose"
button plus a separate "Edit Bone" button into one control). "Edit Mask"
(where the caller opts in via ``edit_mask_idname``/``show_edit_mask``)
stays a second, independent button next to the cycle button -- it is not
part of the cycle, since the user's request named only Object/Pose/Edit
Bone. Used identically by ``features/deform_layer_viewer/deform_bone_viewer/`` and
``features/deform_layer_viewer/layer_viewer/`` so the control looks and behaves the same in
both places, per an earlier explicit user request. (First prototyped in a
throwaway ``mode_toggle_experiment`` demo domain, since removed once no
longer needed -- see the doc below for that history.)

**Superseded by the single cycle button above (2026-09-13):** the
following two paragraphs describe the PREVIOUS shape (a combined
"Object"/"Pose" button plus a separate "Edit Bone" button, each with their
own depress/remembered-mode rules) -- retained as historical context for
how the widget arrived at its current shape; the code below no longer
implements either of the two "remembered last mode" or "no-op on matching
target" mechanics described here for the Object/Pose/Edit-Bone segment
(the module-level ``_last_mode`` cache backing this was removed as part of
the merge -- it was only ever needed to know which mode to return to when
"Edit Bone"/"Object"/"Pose" were still separate buttons; the fixed forward
cycle no longer needs it). "Edit Mask" itself is unaffected by any of this
-- it kept its own explicit-target, no-op-on-match dispatch throughout.

**"Edit Mask" moved out, then back, as its own separate/spaced button
(2026-09-13, two explicit user requests the same day):** first moved out
of this row entirely into ``features/weight_apply/``'s own panel; later
moved back to both of ``deform_layer_viewer``'s call sites, this time as a
separate button pushed to the right edge of a fixed-ratio ``split()``
zone (``alignment='RIGHT'`` on that zone), rather than attached to the
cycle button. A first attempt at this used
``layout.separator_spacer()`` for the push instead -- reverted within the
same session after it reproduced a bug this codebase had already hit and
fixed three times elsewhere (``panel_main.py``'s ``_draw_top_row()``,
``widget_preferences.py``'s ``_draw_section_header()``,
``features/tool_socket/tool_socket_feature.py``'s header row):
``separator_spacer()`` makes Blender miscompute the N-panel sidebar's
required width, visibly shifting/overlapping OTHER UI drawn below it.
Both current call sites now pass ``show_edit_mask=False`` here (so this
function draws only the cycle button) and call ``draw_edit_mask_button()``
below directly, into the right-hand zone of their own
``row.split(factor=...)`` -- see that function's own docstring and
``docs/core-interfaces/mode-edit-toggle-widget.md`` for the full
back-and-forth.

``edit_mask_idname``/``edit_mask_text``/``edit_mask_enter_text``/
``show_edit_mask`` are still accepted here too (an older inline "Edit
Mask" branch, attached directly to the cycle button with no caller
opting into it today) for backward compatibility -- when
``show_edit_mask`` is ``True`` and ``edit_mask_idname`` is given, this
function draws that inline "Edit Mask" button itself, right next to the
cycle button in the same ``row``, with the exact same dispatch/depress/
label rules ``draw_edit_mask_button()`` has. Clicking it lands directly in
Mask sub-mode (entering the session first if not already editing), a
no-op if already there. While actually editing, it is drawn
``depress=True`` only while the LIVE sub-mode is actually Mask; while NOT
yet editing, it is never depressed (only the cycle button reflects the
current state then).

**"Edit Mask" now shows only while actually editing, and the cycle button
no longer forces Bone sub-mode on entry (2026-09-13, per explicit user
request):** two related changes to ``draw_edit_mask_button()`` and the
cycle button's Pose-state click respectively --

- ``draw_edit_mask_button()`` (the function both current call sites
  actually use) now draws NOTHING at all while not currently editing --
  previously it stayed visible with a cold-entry label
  (``edit_mask_enter_text``, e.g. "Init & Edit Mask") that could enter the
  session directly into Mask sub-mode. That cold-entry path is gone:
  "Edit Mask" only ever appears once a live Edit Layer Weight session is
  already active. ``edit_mask_enter_text`` is accepted but unused now.
- The cycle button's Pose-state click (see "The Widget" above) used to
  dispatch ``edit_mask_idname`` with ``target='WEIGHT'`` explicitly,
  forcing the session to land in Bone sub-mode every time regardless of
  what was last active. It now dispatches ``enter_edit_idname`` directly
  instead, with no forced target -- the session resumes whichever
  sub-mode (Bone or Mask) ``active_is_mask`` was last left at, since
  ``_enter_edit_mode()`` already preserves that flag on entry (see
  ``docs/domains/controller.md``'s note on this, and this file's
  "Guardrails & Invariants" section below). Together, these two changes
  mean "Edit Mask" only ever appears, and only ever needs to be toggled,
  while a session is already open -- there is no longer a reason for it
  to expose a sub-mode choice at cold-entry time, since cold entry now
  always resumes the remembered one anyway.

Lives here (``interface/utils/``, not duplicated per feature package) per
explicit user request -- this is a plain drawing helper, not panel/registry
internals, the same "closed-subsystem exempt" precedent as
``interface.utils.utils`` / ``interface.utils.icons`` (see
``docs/domains/tool_socket.md``'s discussion of that exemption). Feature
packages importing this module does not violate the Zero Cross-Imports rule
-- that rule only forbids importing between sibling packages under
``features/*``; ``interface/`` is a different top-level layer already
imported by many feature domains.

See ``docs/core-interfaces/mode-edit-toggle-widget.md`` for the full history
of why this shape was chosen (the "remembered last mode" fix in particular,
and the merged-toggle-to-two-buttons back-and-forth) and for a
worked-through code walkthrough.

**Icons, per explicit user request (2026-09-09) reversing the earlier
"text-only, no icons" decision:** the cycle button carries Blender's own
built-in mode icons -- ``'OBJECT_DATAMODE'`` while showing "Object".
**Superseded for the Pose and Edit-Bone states (2026-09-15, per a LATER
explicit user request):** the Edit-Bone state's label changed from
"Weight Paint" to "Edit" and its icon switched from the addon's own custom
bone glyph to Blender's built-in ``'MOD_VERTEX_WEIGHT'``; the Pose state's
icon switched from ``'POSE_HLT'`` to ``'OUTLINER_OB_ARMATURE'``. The same
request also stripped the icon entirely off both "Edit Mask" buttons (the
inline branch below and ``draw_edit_mask_button()``) and shortened their
default label from "Edit Mask" to "Mask" -- neither the addon's custom bone
glyph nor its layer-mask glyph is referenced by this module anymore as a
result, so the ``.icons`` import is gone too.
"""


def _is_editing(obj) -> bool:
    """Shared `is_edit` resolution for `draw_mode_edit_toggle()` and
    `draw_edit_mask_button()` -- being in Blender's native Edit Mode on a
    mesh is not sufficient on its own (see
    `docs/core-interfaces/facade-api.md`'s `is_editing_weights()` note);
    both callers need this exact check to agree, or the two "Edit
    Mask"-shaped buttons they draw could disagree about whether a live
    session is in progress."""
    from ...core.facade import CoreFacade
    return bool(
        obj and obj.type == 'MESH' and obj.mode == 'EDIT'
        and CoreFacade.is_editing_weights()
    )


def draw_mode_edit_toggle(
    row,
    context,
    *,
    pose_toggle_idname: str = "object.mw_force_pose_mode",
    object_exit_idname: str = "superskin.save_weights",
    enter_edit_idname: str = "object.mw_toggle_edit_mode",
    enter_edit_text: str = "Edit Bone",
    edit_mask_idname: str = None,
    edit_mask_text: str = "Mask",
    edit_mask_enter_text: str = None,
    mask_state_obj=None,
    show_edit_mask: bool = True,
    target_mesh_obj=None,
) -> None:
    """Draw the single Object/Pose/Edit-Bone cycle button, plus (when
    ``edit_mask_idname`` is given and ``show_edit_mask`` is ``True``) a
    second, independent "Edit Mask" button next to it.

    Draws directly into *row* -- this function does NOT create its own
    ``layout.row(align=True)``, so callers can nest it into an already
    aligned row (or into a fresh one they create themselves). Callers are
    responsible for ``row.scale_y`` and any preceding widgets in that same
    row. One ``row.operator()`` call for the cycle button, plus one more
    for "Edit Mask" when it is drawn.

    **The cycle button** always shows the CURRENT state and always
    advances to the NEXT one on click, in fixed forward order:
    Object -> Pose -> Edit Bone -> Object -> ... (2026-09-13, per explicit
    user request merging the former combined "Object"/"Pose" button and
    the separate "Edit Bone" button into this one control).
    ``depress`` is ``False`` for the Object/Pose states and ``True`` only
    for the Edit Bone state (2026-09-14, per explicit user request): the
    button previously drew ``depress=True`` unconditionally in every
    state, reading as permanently "pressed" even while merely idling in
    Object/Pose Mode; now the toggle color only appears once an actual
    Edit Skin Weight session is live, which is the one state that isn't
    trivially reversible with another plain click.

    - Object state (not posing, not editing): text "Object", icon
      ``'OBJECT_DATAMODE'``, ``depress=False``. Click dispatches
      ``pose_toggle_idname``, advancing to Pose.
    - Pose state (Armature in Pose Mode): text "Pose", icon
      ``'OUTLINER_OB_ARMATURE'`` (changed from ``'POSE_HLT'`` 2026-09-15,
      per explicit user request), ``depress=False``. Click advances into
      the Edit Layer Weight session by dispatching ``enter_edit_idname``
      directly -- landing in whichever sub-mode (Bone or Mask) was last
      active on this mesh (``active_is_mask`` persists across sessions,
      see ``docs/core-interfaces/mode-edit-toggle-widget.md``'s
      "Guardrails & Invariants"), NOT forced into Bone sub-mode
      (2026-09-13, per explicit user request that the widget remember the
      last "Edit Mask" toggle rather than resetting it on re-entry).
    - Edit Bone state (live Edit Layer Weight session, either sub-mode):
      text "Edit" (changed from "Weight Paint" 2026-09-15, itself renamed
      from "Edit Bone" 2026-09-14 -- both explicit user requests; the
      state's internal name in this docstring is unchanged, only the
      label text shown on the button), icon ``'MOD_VERTEX_WEIGHT'``
      (Blender's built-in icon, replacing the addon's own custom bone
      glyph, same 2026-09-15 request), ``depress=True``. Click dispatches
      ``object_exit_idname``, which saves/bakes and exits back to Object
      Mode -- completing the cycle. This is unconditional: unlike the
      widget's previous "remembered last mode" behavior, the cycle always
      returns to Object regardless of whether this session was entered
      from Object or Pose. The label reads "Edit" the same whether the
      live sub-mode is actually Bone or Mask -- "Edit Mask" (see below)
      is the only indicator of the live sub-mode; this button just tracks
      "a session is active" at this position in the cycle.

    **"Edit Mask" (when ``edit_mask_idname`` is given and ``show_edit_mask``
    is ``True``):** an independent, real TOGGLE button drawn next to the
    cycle button, unaffected by it. Clicking it dispatches
    ``edit_mask_idname`` with ``target='TOGGLE'`` -- a blind flip of
    whatever sub-mode is currently active (NOT an explicit ``target='MASK'``
    landing -- see ``draw_edit_mask_button()``'s own docstring for why an
    explicit target is a real bug here: it's a guaranteed no-op once
    already in that sub-mode, with no other button left to switch back).
    Drawn ``depress=True`` only while actually editing AND the live
    sub-mode is Mask; never depressed while NOT editing. Label:
    ``edit_mask_text`` while editing, ``edit_mask_enter_text`` (falling
    back to ``edit_mask_text``) while NOT editing.

    Args:
        row: the row (or any layout) to draw the buttons into.
        context: the active ``bpy.context``.
        pose_toggle_idname: operator dispatched by the cycle button in the
            Object state, advancing to Pose. Must behave like
            ``object.mw_force_pose_mode``: enter Pose Mode from Object
            Mode (the only state this widget calls it from).
        object_exit_idname: operator dispatched by the cycle button in the
            Edit Bone state, advancing back to Object. Must behave like
            ``superskin.save_weights``: bake the in-progress layer edit and
            exit to Object Mode, keeping the sidebar panel open.
        enter_edit_idname: operator dispatched directly by the cycle
            button in the Pose state, and passed THROUGH to
            ``edit_mask_idname`` as its own ``enter_edit_idname`` property
            for the "Edit Mask" button's own cold-entry fallback (dead in
            practice now that "Edit Mask" is only ever drawn while already
            editing, see ``draw_edit_mask_button()`` below -- kept for
            back-compat since ``SUPERSKIN_OT_toggle_mask_mode`` still
            declares the property). Must behave like
            ``object.mw_toggle_edit_mode``'s enter branch /
            ``superskin.enter_layer_edit``.
        enter_edit_text: currently UNUSED -- accepted for backward
            compatibility only (both call sites still pass their dynamic
            "Init & Edit Bone" text here, harmlessly). The cycle button's
            Pose-state label is always plain "Pose", not this override --
            merging "Edit Bone" into the cycle removed the standalone
            pre-entry button this text used to label.
        edit_mask_idname: operator dispatched by the "Edit Mask" button
            (with ``target='MASK'``) when ``show_edit_mask`` is ``True``,
            e.g. ``superskin.toggle_mask_mode``. Must accept a ``target``
            ``EnumProperty`` (``'WEIGHT'``/``'MASK'``) it lands in
            explicitly (a no-op if already there), and an
            ``enter_edit_idname`` ``StringProperty`` it dispatches by name
            first when not already editing. No longer used by the cycle
            button itself (see ``enter_edit_idname`` above) -- only by the
            inline "Edit Mask" branch below.
        edit_mask_text: label for the "Edit Mask" button once already
            editing (and the fallback for its NOT-editing label too, if
            ``edit_mask_enter_text`` isn't given). "Edit Mask" by default.
        edit_mask_enter_text: label for the "Edit Mask" button while NOT
            editing (e.g. ``layer_viewer``'s dynamic "Init & Edit Mask" vs
            plain "Edit Mask"). Falls back to ``edit_mask_text`` if
            ``None``. Ignored once already editing.
        mask_state_obj: currently UNUSED -- accepted for backward
            compatibility only (``layer_viewer_feature.py`` still passes
            it, harmlessly). See ``docs/core-interfaces/mode-edit-toggle-widget.md``
            for the history of what this used to do.
        show_edit_mask: whether to draw the inline "Edit Mask" button
            (attached directly to the cycle button) when ``edit_mask_idname``
            is given (default ``True``). Both current call sites pass
            ``False`` -- they draw "Edit Mask" separately via
            ``draw_edit_mask_button()`` below instead, into the right-hand
            zone of their own ``row.split(factor=...)`` (with
            ``alignment='RIGHT'`` on that zone) rather than attaching it
            here. Ignored when ``edit_mask_idname`` is ``None`` (nothing to
            hide).
        target_mesh_obj: the mesh the Pose-state click should explicitly
            enter, e.g. ``layer_viewer``'s already-resolved
            ``object_selector.get_effective_mesh(context)`` result. When
            given, set on the dispatched operator's own ``mesh_name``
            property (only if that operator declares one --
            ``superskin.enter_layer_edit`` does, ``object.mw_toggle_edit_mode``
            doesn't, so this is a silent no-op at call sites that don't pass
            an ``enter_edit_idname`` supporting it). Without this,
            ``_enter_edit_mode()`` resolves its own target mesh from
            ``context.active_object`` (the Armature, while posing) via a
            per-armature scene cache that can point at a stale, different
            mesh than the one actually shown as selected -- see
            ``docs/domains/controller.md``'s "Force Pose Mode: stale
            per-armature 'last mesh' cache" section.
    """
    obj = context.active_object
    is_edit = _is_editing(obj)
    is_pose = bool(obj and obj.type == 'ARMATURE' and obj.mode == 'POSE')

    if is_edit:
        # Only the live Edit Skin Weight session reads as a "pressed"
        # toggle -- Object/Pose are plain, uncolored buttons (per explicit
        # user request the cycle button no longer looks toggled while
        # merely idling in Object/Pose Mode).
        row.operator(
            object_exit_idname, text="Edit",
            icon='MOD_VERTEX_WEIGHT', depress=True,
        )
    elif is_pose:
        # Dispatches enter_edit_idname directly -- no forced target, so
        # the session resumes whichever sub-mode (Bone/Mask) was last
        # active on this mesh instead of always landing in Bone.
        pose_props = row.operator(enter_edit_idname, text="Pose", icon='OUTLINER_OB_ARMATURE', depress=False)
        if target_mesh_obj is not None and hasattr(pose_props, 'mesh_name'):
            pose_props.mesh_name = target_mesh_obj.name
    else:
        row.operator(pose_toggle_idname, text="Object", icon='OBJECT_DATAMODE', depress=False)

    if edit_mask_idname and show_edit_mask:
        if is_edit:
            # is_edit already guarantees obj is the Mesh itself.
            mask_label = edit_mask_text
            mask_depress = bool(obj) and obj.superskin_storage.active_is_mask
        else:
            mask_label = edit_mask_enter_text or edit_mask_text
            mask_depress = False

        mask_props = row.operator(
            edit_mask_idname, text=mask_label, depress=mask_depress,
        )
        mask_props.enter_edit_idname = enter_edit_idname
        # 'TOGGLE', not the explicit 'MASK' this branch used before -- see
        # draw_edit_mask_button()'s own docstring for why an explicit
        # target is a real bug here (a guaranteed no-op once already in
        # that sub-mode, with no button left to switch back).
        mask_props.target = 'TOGGLE'


def draw_edit_mask_button(
    layout,
    context,
    *,
    enter_edit_idname: str = "object.mw_toggle_edit_mode",
    edit_mask_idname: str = "superskin.toggle_mask_mode",
    edit_mask_text: str = "Mask",
    edit_mask_enter_text: str = None,
) -> None:
    """Draw a single, standalone "Edit Mask" button -- the exact same
    ``edit_mask_idname``/``target='MASK'`` dispatch ``draw_mode_edit_toggle()``'s
    own inline "Edit Mask" branch uses, extracted so a caller can place it
    somewhere other than directly attached to the cycle button.

    Added 2026-09-13, per explicit user request to move the "Edit Mask"
    button out of ``deform_layer_viewer``'s shared widget row entirely and
    into ``features/weight_apply/``'s own panel instead. Per a LATER
    explicit user request the same day, that move was reversed -- "Edit
    Mask" is back at both of ``deform_layer_viewer``'s call sites
    (``deform_layer_viewer_feature.py``, ``layer_viewer_feature.py``), now
    called directly via this function, into the right-hand zone of a
    fixed-ratio ``row.split(factor=...)`` (``alignment='RIGHT'`` on that
    zone) so it hugs the row's right edge without stretching to fill it --
    rather than attached to the cycle button the way
    ``draw_mode_edit_toggle()``'s own inline ``show_edit_mask=True`` branch
    draws it. (A first attempt used ``layout.separator_spacer()`` for the
    push instead of ``split()`` -- reverted the same session after it
    reproduced a bug already hit and fixed three times elsewhere in this
    codebase; see this module's own docstring above for the details and
    why ``split()`` + ``alignment='RIGHT'`` is used instead everywhere.)
    Both call sites pass ``show_edit_mask=False`` to
    ``draw_mode_edit_toggle()`` and call this function separately instead
    -- see ``docs/domains/weight_apply.md``'s "Edit Mask button" section
    and ``docs/core-interfaces/mode-edit-toggle-widget.md`` for the full
    back-and-forth history.

    **Only drawn while actually inside a live Edit Layer Weight session
    (2026-09-13, per explicit user request):** draws nothing at all (a
    silent no-op) when not currently editing -- previously this button
    was also drawn while NOT editing, labelled via ``edit_mask_enter_text``,
    so a cold click could enter the session AND land directly in Mask
    sub-mode in one step. That cold-entry path is gone; "Edit Mask" now
    only ever appears once a session is already active, matching how a
    caller building a fixed-ratio `split()` around this call (both current
    call sites do -- see this function's own docstring above) wants an
    empty right-hand zone rather than a visible button while not editing.
    ``edit_mask_enter_text`` is therefore unused now (kept for backward
    compatibility only -- both call sites still pass it, harmlessly).

    While editing: depressed exactly when
    ``obj.superskin_storage.active_is_mask``, labelled ``edit_mask_text``.

    **Dispatches ``edit_mask_idname`` with ``target='TOGGLE'``, a genuine
    blind flip (2026-09-13, per explicit user request -- FIXES a real bug
    the previous ``target='MASK'`` had):** with an explicit target
    (``'WEIGHT'``/``'MASK'``), ``SUPERSKIN_OT_toggle_mask_mode.execute()``
    is a GUARANTEED no-op once the mesh is already in that exact sub-mode
    (see its own ``is_explicit and storage.active_is_mask == want_mask``
    early-return) -- so with ``target='MASK'`` fixed, once mask mode was
    entered there was literally no way to press this button again to
    leave it; the "Cannot get back to Bone" version, reported as "I can't
    toggle the Edit Mask button." Removing the now-vanished "Edit Bone"
    button (folded into the Object/Pose/Edit-Bone cycle button, whose own
    "Edit Bone" state exits the whole session rather than switching
    sub-mode) took away the only OTHER way back to Bone sub-mode too, so
    this button had to become the real toggle now that it's the sole
    Bone<->Mask control while editing. ``target='TOGGLE'`` makes
    ``execute()`` skip that no-op guard entirely and flip
    ``active_is_mask`` unconditionally, matching this button's own
    ``depress`` state (which already reflected the live sub-mode either
    way) and its "Edit Mask" name reading as an actual toggle.
    ``enter_edit_idname`` is still passed through as a property on the
    operator (``SUPERSKIN_OT_toggle_mask_mode`` still declares it) but is
    never actually exercised now that this function only draws while
    ``CoreFacade.is_editing_weights()`` is already ``True``.

    Draws directly into *layout* -- no row/scale of its own, same
    convention as ``draw_mode_edit_toggle()``.
    """
    obj = context.active_object
    if not _is_editing(obj):
        return

    mask_props = layout.operator(
        edit_mask_idname, text=edit_mask_text,
        depress=bool(obj) and obj.superskin_storage.active_is_mask,
    )
    mask_props.enter_edit_idname = enter_edit_idname
    mask_props.target = 'TOGGLE'
