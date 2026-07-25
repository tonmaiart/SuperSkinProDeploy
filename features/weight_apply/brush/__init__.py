"""Weight Brush -- hold-to-paint entry point into Weight Apply's own
Add/Scale/Smooth/Sharpen math.

Deliberately NOT a separate registered `UnifiedFeatureExtension` domain --
it lives inside this package specifically so `brush_ops.py` can call
`WeightApplyFeature.snapshot_context()` / `apply_action()` directly as an
ordinary same-package import, instead of crossing the "Zero Cross-Imports"
boundary that forbids one `features/*` package from importing another's
internals. See `features/weight_apply/README.md`'s "Weight Brush" section
for the full rationale and dataflow.

Reload cascade and register()/unregister() wiring for this subpackage's
modules live in `features/weight_apply/__init__.py` (the Deep Matrix Reload
Rule's bottom-up cascade already lives there for the rest of the domain) --
this file only marks the directory as a package.
"""

# Temporary kill switch -- set to False to fully disable and hide the
# Weight Brush feature (no Toolbar tool, no operator, no hover cursor, no
# N-panel row, brush settings dropped from JSON persistence) WITHOUT
# deleting or unregistering any of this subfolder's code. Checked by:
#   - `../__init__.py`'s register()/unregister() (skips brush_ops/
#     brush_tool/brush_hover entirely when False)
#   - `../weight_apply_feature.py`'s populate()/serialize_into() (skips the
#     "brush" JSON sub-key delegation)
#   - `../ui.py`'s draw_section() (skips draw_brush_row())
# Flip back to True and F3 Reload Scripts to re-enable -- nothing else
# needs to change.
BRUSH_ENABLED = False
