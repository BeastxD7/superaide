# Test modules: run only the tests a change can affect

Status: built 2026-10-24 (PR "ci: run only the tests of the modules a PR changes"). Extends [ADR 0013](../decisions/0013-ci-pipeline.md).

## What and why

Every pull request used to run the whole backend suite (186 test files, about 2,900 tests, two shards of 5 to 6 minutes) and, whenever the frontend or the API contract moved, all 80 browser specs. A change to the mirror page paid for the Telegram tests.

Now each part of the code is a **module** with its own tests. A pull request runs the tests of the modules it changes, plus the tests of every module that imports them. The whole suite still runs on `main`, in the scheduled Full run (every two hours), with the `full-suite` label, and for any change that could affect everything.

The rule that shaped the design, from the owner: **a wrong skip is worse than a slow run.** Every doubt selects the full suite.

## The module map: `ci/modules.toml`

One file, plain data (TOML, read with the standard library). Each `[modules.<name>]` has:

| Key | Meaning |
| --- | --- |
| `src` | Files the module owns: globs from the repo root (`**` crosses directories). A backend package, or a frontend feature group. |
| `tests` | The module's own tests: backend pytest files, or frontend vitest files. A test file belongs to exactly one module. |
| `e2e` | Playwright specs that exercise it. A spec may be listed under several modules. |
| `core = true` | A change here runs everything (`config`, `db`, `audit`, `tenancy`, `web-shell`). |
| `always = true` | Its tests run with every backend selection (`guards`: route-wide authorisation, the API contract, the impostor scenarios). |

Besides the modules there are three lists: `[ignore]` (documentation: runs nothing), `[full]` (paths that run everything: Alembic, every `models.py`, `conftest.py`, `pyproject.toml` and the lockfiles, the OpenAPI snapshot, `.github/**`, `scripts/**`, `ci/**`, the Makefile, `package.json`, shared frontend and e2e files) and `[settings] expansion`.

### Nothing can rot

`backend/tests/test_select_tests.py` fails when:

- a file under `backend/src/superaide` or `frontend/src` is owned by no module, or by two;
- a backend test file is owned by no module or by two (a new test can declare itself on its first lines with `# ci-module: memory` instead of editing the map, which avoids merge conflicts on one shared file);
- an e2e spec is listed by no module;
- a vitest file is owned by none or two;
- a pattern in the map matches no file (a rename or delete the map did not follow).

So a new package, test or spec cannot slip through unselected. The selector additionally treats anything unmapped as "run everything" at run time.

## Who depends on whom: computed, not written

`scripts/select_tests.py` parses every file of `backend/src/superaide` with `ast` and builds the **file-level import graph**: lazy imports inside functions and `TYPE_CHECKING` imports count, because they are real dependencies. Nothing is hand-listed, so the graph cannot go stale. The one deliberate exception: `db/models.py` imports every package's `models.py` only to register tables on the metadata; those edges are ignored (a change to any `models.py` runs everything anyway, see `[full]`).

For a changed file the selector finds the files that import it, transitively (`expansion = "transitive"`), maps those files to modules, and takes those modules' tests. It also adds every test file that imports an affected file directly, so a test cannot be missed because it sits in the wrong module.

Why file level and not package level: at package level the graph is one blob (the reverse closure of 40 of 58 packages is 52 of 58). At file level one cycle of 34 files remains (`pipeline`, `factory`, `app.routes`, `brain.loop`, `policy.actions`, `knowledge.*`, `triage.service`, `skills.registry`, `reception.service`, `voice.gateway` and a few more, joined by lazy imports). A change to any of those files selects about 150 of the 186 test files; a change to a leaf (`mirror`, `passport`, `school`, `push`, `rehearsal`, `memsocket`, `brainshare`, `dissent`, `mandate`, `imports`) selects 65 to 80, mostly because `app` and `console` (which import almost everything) and the route-wide guards come along. Breaking that cycle is the biggest lever for making selection sharper; it is out of scope here.

`expansion = "direct"` (only the first importer, not the whole chain) is available as an owner decision: faster, can miss a break that travels through two hops. The dry run below shows both.

## What the selector decides

For the changed files of a pull request (`git diff` of the PR merged into its base, both sides of a rename):

1. Matches `[ignore]`: nothing to run for that file.
2. Matches `[full]`, or no module owns it, or it is a test or spec nobody lists: **everything**.
3. Belongs to a core module: **everything**.
4. Otherwise the file's module, its dependents and the tests that import it directly are selected; `guards` always come along.
5. Frontend groups select their e2e specs and vitest files; a backend module selects the specs it lists. Specs tagged `@smoke` always join a narrowed browser run.
6. A selector crash, a parse error (including running it on a Python older than 3.14), or `git` failing: **everything**.

Outputs: backend test files, e2e specs, vitest files, with the reason, printed in the job summary of every run.

### How CI uses it

- The `Changed paths` job checks out the merged PR, runs the selector with `uv run --python 3.14`, and exposes `selection_mode`, `backend_tests`, `backend_files`, `e2e_only` and `vitest_files`. The existing filters still decide which jobs run; the selection only narrows what runs inside them.
- `Backend tests (shard i/2)` passes the selected files to `pytest -n auto --shard i/2`. The selected files are still split over the two shards. A shard that gets none exits 5, which is accepted only when a selection is in force. When the selector says no backend test is needed (only documentation under `backend/`), the test jobs are skipped; `Backend (lint, types, migrations)` still runs.
- `End-to-end (shard i/4)`: `e2e/scripts/shard.mjs` reads `E2E_ONLY`; the selected specs are balanced by measured time. The browser job itself is chosen as before: full matrix when `frontend/**`, `e2e/**` or the API contract changed, smoke subset for backend-only changes (ADR 0013); only the full matrix is narrowed. The proxy contract tests run only on a full run.
- `Frontend`: `vitest run <selected files>`; all unit tests when the selection has none for the group or the code is shared. Lint, types and build always run (they cannot be split).
- Pushes to `main`, `merge_group`, `workflow_dispatch` and the scheduled Full run (`full.yml`, every two hours, which calls `ci.yml`) select **everything**. `CI result` insists on it: on any event other than a pull request it fails unless `backend_tests` is true and `selection_mode` is `full`.
- The `full-suite` label forces everything. Labels are read live from the API, so add the label and re-run the `Changed paths` job (as with `tests-not-needed`).
- `CI result` semantics are unchanged: skipped is fine, failed or cancelled fails, a job whose output said it was needed must have succeeded. The seven check names are unchanged.

## Running one module locally

```bash
make test MODULE=memory      # the memory tests and those of the modules that import it
make test-changed            # what CI would pick for your branch (git diff origin/main...HEAD)
cd backend && uv run pytest -m mod_memory    # only the memory module's own tests (marker from the map)
uv run --project backend python scripts/select_tests.py --changed backend/src/superaide/mirror/service.py
uv run --project backend python scripts/select_tests.py --module mirror --format summary
```

`backend/tests/modules.py` tags every test with `mod_<module>` from the map (hooked in `conftest.py`). No test file was moved or renamed, so open pull requests are unaffected.

## Adding a module

1. Add `[modules.<name>]` to `ci/modules.toml` with `src`, `tests` and `e2e`. Name backend modules after the package.
2. Put each test file under exactly one module's `tests` (or start it with `# ci-module: <name>`).
3. List the e2e specs that exercise it.
4. Run `uv run pytest tests/test_select_tests.py`; it names whatever is still unmapped.

## What this accepts

- A bug that travels through code the graph cannot see (dynamic imports by string, behaviour that depends on a data file read at run time, two modules coupled only through the database) can be missed on a pull request. `main` runs the whole suite on every push and Full runs every two hours, so it is caught after the merge, not before. The `full-suite` label is the escape hatch for a pull request the author knows is risky. Data files inside a package (prompts, skills) count as changes to every file of that package.
- Selection works on files, not tests: a module whose tests are slow stays slow.
- e2e narrowing for backend-only pull requests is unchanged (the `@smoke` subset); only a pull request that also changes the frontend or the specs gets a narrowed full run.

## Evidence

See the pull request body for the dry run over the last 15 merged pull requests and the CI timings; regenerate the table with `scripts/dry_run.py`.
