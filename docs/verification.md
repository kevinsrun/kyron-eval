# Final verification

Implementation frozen and checked at approximately **2:47 p.m. Eastern, November 22, 2026**, well before the user's **8:00 p.m.** cutoff. The candidate still needs to record and submit the video.

## Actual results

| Check | Actual result | Evidence |
|---|---|---|
| Unchanged upstream relevant tests | 31 passed in 0.44s | `docs/engineering-log.md`; source revision `c708acb` |
| Final local test suite | 68 passed, 1 warning in 0.22s | `artifacts/verification/pytest.txt` |
| Ruff | All checks passed | `uv run --frozen ruff check src/kyron_eval tests scripts`; clean log also includes result |
| Dependency lock | `uv lock --check` succeeded | Checked-in `uv.lock`; 27 locked packages including build/dev dependencies |
| JavaScript syntax | `node --check src/kyron_eval/static/app.js` exited 0 | Final command execution |
| Package build | Source distribution and Python wheel built successfully | `uv build` |
| Clean source setup | Fresh source directory, `uv sync --frozen --offline`, 68 tests, Ruff, and a new phantom-booking replay passed | `artifacts/verification/clean-setup.txt` |
| Isolated installed wheel | Fresh Python 3.11 environment; full 16-trace CLI run; UI routes, run page, JS/CSS and sanitized audit all returned HTTP 200 | `artifacts/verification/wheel-install.txt` |
| Browser → API → artifact → UI | Suite button created `run-20261122T192723-a27a5fe1aa` with 16 results; selected B/expired-offer control created `run-20261122T194330-5bea013033` with one result | Persisted artifacts; browser checks |
| Browser inspection | A/B comparison, book ACK and unchanged state, metric links, four human labels, 2/4 vs 4/4 calibration and findings rendered; console error list was empty | `artifacts/verification/workbench.jpg`, `calibration.jpg` |
| Final artifact integrity | `run-20261122T194538-db7d99d3f5`: 16 completed traces, implementation hash matches current runtime Python sources, dataset hash matches current fixtures, four human labels remain valid, no ERROR/UNKNOWN metric outcomes in this selected suite | `artifacts/experiment.json`, final run and calibration artifacts |
| Upstream isolation | Upstream `git diff --exit-code` and `git status --short` were empty; new repository has no remote | Final repository checks |

The one test warning is Starlette's deprecation of its `httpx` TestClient integration in favor of `httpx2`. Tests pass on the locked versions; it is not an evaluator or agent failure. No warning suppression was added.

An initial optional `uv pip install --offline` wheel check failed because that environment's dependency cache was incomplete. Normal installation then succeeded; the final installed-wheel check also succeeded. The core source setup was separately verified offline using its available cache. The submission does **not** promise a universally network-free first dependency installation. Runtime replay/evaluation requires no external service.

The screenshots show a valid earlier final-version run (`run-20261122T194255-ef3723e993`); the report points to the last run after replacing metadata-copy assignment with full domain revalidation. Metric outcomes are identical. The change does not weaken any domain validator. Screen layouts were inspected at the narrow default viewport and a desktop comparison viewport; the temporary viewport override was reset.

No real phone call, external EHR operation, deployment, push or video recording occurred. Temporary verification servers are stopped after checks. Run `./start.sh` to open the application again.
