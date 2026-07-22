# v2.6 Q2 module rename — P2.5 mission review record

Date: 2026-07-22

Scope: `fable_lite` → `smtw` canonical Python package rename with a deprecated
legacy shim. Implementation specification:
`docs/specs/v2.6-q2-module-rename.md`.

## Review rounds

- Round 1: codex@영진 58/100 REJECT (4 critical); agy 88/100 REJECT
  (2 critical).
- Round 2: agy FINAL APPROVE; codex 90/100 REJECT because a module-qualified
  pytest warning filter could not match a `stacklevel=2` warning attributed to
  its caller.
- Round 3: codex 98/100, agy 97/100, orchestrator 96/100; critical 0 and mission
  gate passed.

## Decisions closed by review

1. The compatibility package uses one central `sys.modules` alias registry and
   eagerly registers all eleven canonical submodules. Physical re-export files
   were rejected because they create distinct module objects and duplicate
   mutable state.
2. The warning text is fixed as
   `fable_lite is deprecated; import smtw instead`. Pytest uses a message-based
   filter; a child Python subprocess proves both default success and intentional
   `PYTHONWARNINGS=error` failure.
3. CI and release-quality scan both package trees and smoke-test both module
   entry points, both console scripts, and installed-wheel object identity.
4. Packaging starts from explicit clean build directories, checks the wheel
   RECORD, and installs into an isolated environment outside the source tree.
5. Source-checkout execution mixed with stale global distribution metadata is
   documented as unsupported. Gates use a dedicated clean virtual environment
   and never uninstall the global distribution.
6. The Windows move is isolated in a move-only commit. The canonical module
   inventory excludes `__init__.py` and the deliberately unaliased
   `__main__.py`.

No review round authorized a distribution rename, state-directory rename,
environment-variable removal, automatic migration, repository rename, or
legacy probe-schema rename.
