# Official results store

Canonical, **durable** benchmark envelopes. Everything published — the table in
the top-level `README.md` and the trend charts on the GitHub Pages site — is
rendered from the `*.json` files in this directory. This is the source of truth;
the top-level `results/` directory is gitignored local scratch.

## Curation policy

Only commit envelopes that are **official numbers**, i.e. produced by a full,
representative suite run on a consistent, quiet machine (the maintainer's
benchmark PC — not a shared CI runner; see
[the design plan](../../docs/plans/2026-06-10-results-representation.md)).

Do **not** commit single-checker spot-checks, refactor-verification runs, or
runs from heterogeneous/noisy hardware — they pollute the trend lines.

## Maintainer flow

```sh
# 1. Run an official suite on the benchmark PC (writes to local results/).
typebench suite --corpus corpus/suite.toml --output results/$(date +%F)-official.json
# 2. Promote the envelope into the durable store.
cp results/<date>-official.json data/official/
# 3. (optional) Preview locally, then push.
mise run render          # regenerates README block + site/data/trends.json
git add data/official/<date>-official.json README.md
git commit -m "data(official): add <date> suite"
git push                 # publish.yml renders + deploys Pages
```

Naming convention: `YYYY-MM-DD-<slug>.json`. Envelopes are sorted by their
embedded `generated_at`, so filenames are for humans, not ordering.
