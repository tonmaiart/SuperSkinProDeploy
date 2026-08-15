# SuperSkinPro

Professional weight painting layers system for Blender (Python + Rust hybrid).

## Development Environment Setup

New-machine checklist — everything needed to build `rust_logic` and trigger
the release workflows. Full detail for each item lives in the linked
README; this is just the ordered list to get a fresh machine working.

1. **Rust toolchain** — install via [rustup.rs](https://rustup.rs/). On
   Windows this also needs an MSVC linker: if `rustup-init` doesn't offer
   it automatically, install the **"Desktop development with C++"**
   workload via Visual Studio Build Tools. See `rust_logic/README.md`'s
   "Building" section for the full prerequisite list and a troubleshooting
   table (missing `cargo`, missing linker, Python ABI mismatches, etc.).
2. **GitHub CLI (`gh`)** — lets you trigger and watch CI workflow runs from
   a terminal instead of the GitHub web UI. Install via
   `winget install --id GitHub.cli` (Windows) or see
   [cli.github.com](https://cli.github.com/) for other platforms, then run
   `gh auth login` once (interactive — choose GitHub.com → HTTPS → "Login
   with a web browser"). After that:
   ```sh
   gh workflow run dev-build.yml -f os=windows   # trigger a build
   gh run watch <run-id>                         # follow it to completion
   ```
3. **`git commit-release` alias** — one-time setup after cloning, since
   git aliases live in `.git/config` and aren't tracked by the repo:
   ```sh
   git config alias.commit-release '!bash "$(git rev-parse --show-toplevel)/scripts/commit-release.sh"'
   ```
   See `scripts/README.md` for what it does and its safety checks.

With all three in place, the three ways to produce a `rust_logic` binary
are:

| Command | Produces | Where it lands |
|---|---|---|
| `python3 rust_logic/build.py` | Your current OS only, using whatever Python `build.py` can find/detect | Local working tree only |
| `gh workflow run dev-build.yml -f os=<os>` | One chosen OS, built on a native CI runner with an exact pinned Python version | Committed straight to `main` |
| `git commit-release` | All 3 OSes, cleaned of dev-only files | Force-pushed to the `release` branch |

See `rust_logic/README.md` for the full build-system writeup (why there
are 3 paths, what each does under the hood, and why Python version
matching matters here specifically).

## Architecture

Features communicate with Core exclusively via `CoreFacade`. `UIController` is a private implementation detail of `core/` — feature code must never import or depend on it directly.

### Unified Component Architecture

Every feature domain under `features/<name>/` is a self-contained package with a single entry-point class inheriting from `UnifiedFeatureExtension` (defined in `interface/registry/register_api.py`).

```
[UI Layout Click] → SUPERSKIN_OT_execute_action (domain_id, action_id)
                  → UnifiedRegistry.get_by_id(domain_id).execute(action_id, ctx, facade)
```

Each extension owns:
- **Action dispatch** — `execute(action, context, core_facade)` routes to domain logic.
- **UI layout** — `draw_section(layout, context)` renders the N-panel section body.
- **JSON persistence** — `populate(data)` / `serialize_into(full_dict)` handle load/save.
- **PropertyGroups** — Blender RNA properties registered on `WindowManager`.
- **Collapsible control** — `is_collapsible()` controls whether the section is wrapped in a collapsible header.

### Tab Assignment

| Tab | Domains |
|---|---|
| `LAYER` | `layer_viewer` (non-collapsible), `tool_socket` (dropdown slot; currently hosts `weight_transfer`, which owns Export/Import JSON too — see `features/tool_socket/README.md`) |
| `SKINNING` | `deform_bone_viewer` (non-collapsible), `weight_apply`, `mirror`, `circle_tool_adjust`, `controller`, `tool_socket` (dropdown slot; currently hosts `clipboard`, `in_mesh_transfer`, and `auto_block_weight`) |
| `PREFERENCE` | `bone_picker`, `multi_color_preview` (hosted in the sidebar's "Preference" section — see `interface/panel_main.py`) |

A `draw_tab` value may also be a list/tuple/set of the above tokens for an extension that needs to render in more than one tab (normalized via `get_draw_tabs()`). By default the same `draw_section()` body is repeated in each registered tab; to draw different content per tab, override `draw_section_for_tab(layout, context, tab_key)` instead — see `interface/registry/register_api.py`.

### Registration Flow

1. `features/<name>/<name>_feature.py` — defines a `UnifiedFeatureExtension` subclass and a module-level `register()` that calls `UnifiedRegistry.register(MyFeature())`.
2. `features/<name>/__init__.py` — imports `<name>_feature` and calls `<name>_feature.register()`.
3. `features/__init__.py` — imports each domain package; order controls tab rendering priority.
4. `__init__.py` (top-level) — calls `interface.registry.register_operator()` to register `SUPERSKIN_OT_execute_action`.

### Example: Mirror Feature

The mirror domain (`features/mirror/`) is fully self-contained:

- **`mirror_feature.py`** — `MirrorFeature(UnifiedFeatureExtension)` with action dispatch, UI layout, `SSPrefMirror` PropertyGroup, `MirrorPreferencesService`, and JSON persistence hooks.
- **`ops.py`** — Operator dispatch only. Performs early pair-existence check, sets the transaction flag, then delegates to `execute_mirror_pipeline`.
- **`logic.py`** — Full pipeline (`execute_mirror_pipeline`), pair generation, Rust-accelerated apply for layer and mask channels.

Rust math is invoked via `CoreFacade.get_rust_gateway()`.

## Adding a New Feature Domain

See `interface/registry/register_api.py` for the `UnifiedFeatureExtension` base class and `interface/registry/__init__.py` for the full quick-start guide with annotated template code.

Quick checklist:
1. Create `features/<name>/<name>_feature.py` extending `UnifiedFeatureExtension`.
2. Implement `get_id()`, `get_actions()`, `get_section_title()`, `get_draw_tabs()`, `execute()`, `draw_section()`.
3. Add `register()` / `unregister()` calling `UnifiedRegistry.register()`.
4. Wire up `features/<name>/__init__.py` to call `<name>_feature.register()`.
5. Add `from . import <name>` to `features/__init__.py` and append to `_modules`.

## Invariants

- **ST_STRICT**: `core/` is read-only for feature code. All access routes through `CoreFacade`.
- **Naming stability**: Existing `bl_idname` values, RNA property names, and operator class names must not be renamed unless the refactoring pipeline explicitly requires it.
- **Code language**: All comments, docstrings, and documentation must be written in professional English only.
