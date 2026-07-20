# Contributing

## Before Editing

- Keep `core/` platform-neutral. Adapters translate host payloads into `dict` inputs and call core functions.
- Keep state inside the target project's `.fable-lite/` directory.
- Hooks must fail open on parser/runtime errors unless the contract explicitly says to block.
- Run tests from the repo root with:

```powershell
python -m pytest tests/
```

`pyproject.toml` pins pytest collection to `tests/` so local research clones under `tmp/` are not collected.

## Dependency Locking

This repo does not commit a `uv.lock`. The documented workflow installs and runs with
plain `pip`/`python -m pytest` (see above), no CI job in `.github/workflows/` installs
via `uv`, and no script reads a lockfile. A previously-committed `uv.lock` had drifted
to a stale package version relative to `pyproject.toml` (REL-01) precisely because
nothing kept it in sync — an unused lockfile is worse than no lockfile. `uv.lock` is
git-ignored; `tests/test_release_hygiene.py::test_all_release_version_surfaces_are_synchronized`
and `python scripts/sync_version.py --check` both fail if one is committed again. If the
project adopts `uv` as the real install/CI workflow, remove it from `.gitignore`, add a
version-sync check for it, and update both of those.

## Adding Packs

1. Add the Korean and English pack files together under `packs/`.
2. Keep marker wording aligned with the parser contract in `core/compliance.py`.
3. Add or update tests that prove the parser accepts the pack's required output shape.
4. Document any new marker in `README.ko.md` when it changes user-visible behavior.

## Adapter Contributions

1. Treat adapters as thin wrappers: parse the host payload, call `core/`, and emit the host-specific response.
2. Add realistic fixture payload tests for every event the adapter supports.
3. Include malformed payload tests that prove fail-open behavior.
4. Do not duplicate core policy in adapters unless the host schema requires a translation step.

## Test Template

Use focused tests first, then the full suite:

```powershell
python -m pytest tests/test_release_hygiene.py -q
python -m pytest tests/ -q
python eval/run_probes.py --strict
```

For hook changes, include at least one test that runs the hook script as a subprocess with stdin JSON, because that is the real integration boundary.
