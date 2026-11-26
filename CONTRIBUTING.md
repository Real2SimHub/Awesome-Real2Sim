# Contributing to Awesome-Real2Sim

Thanks for helping keep the map complete. Everything in the README, the figures and the browse page
is generated from the files in `data/`, so a contribution is an edit to `data/works.json`.

## Is it in scope?

A work belongs here when it takes **observations of the real world** (video, images, depth, scans,
interaction) and delivers something that can be **run**, or when it measures how well that is done
(benchmark) or provides data for it (dataset). Simulations built only from text or from simulated data
go under *Adjacent directions*. Reviews go under *Surveys*.

## Where does it go?

Methods are sorted by **what they deliver**. Follow the questions in order until an answer assigns a category.

1. Does the input include real observations? *No* → Adjacent directions.
2. Do the dynamics live in network weights? → Learned simulator (`m5-learned`).
3. Is the deliverable an executable program? → Simulation program (`m4-program`).
4. Is it a whole scene or environment? → Scene twin (`m3-scene`).
5. Is it a single object in URDF / MJCF / USD? → Sim-ready asset (`m2-asset`).
6. Otherwise it is a physical twin: were the parameters fitted to the object's own motion?
   Yes → `m1a-identified`; no → `m1b-assigned`.

Benchmarks are sorted by what the system under test hands in: a runnable simulation (`b1-executable`),
answers or predictions (`b2-reasoning`), generated video (`b3-generated`), or nothing because the
simulator itself is tested (`b4-sim-real`).

## Two ways to contribute

- **Open an issue** with the *Add a paper* form. A maintainer adds the entry.
- **Send a pull request**: add an object to `data/works.json` (copy a neighbour in the same category
  for the fields), then run

  ```bash
  python scripts/build.py          # regenerates README.md, the themed figures and site/
  python scripts/build.py --check  # what CI runs: fails if a generated file is out of date
  ```

  Commit `data/works.json` together with the regenerated files. Do not edit `README.md` by hand.

## Fields

| Field | What to write |
|---|---|
| `id` | lowercase slug, unique (`phystwin`) |
| `name`, `title`, `year`, `venue` | short name, full paper title, year, venue (`arXiv` if not yet published) |
| `kind`, `category` | `method` / `benchmark` / `dataset` / `survey`, and the category key above |
| `paper`, `code`, `project` | links; at least one |
| `institution` | the first author's first affiliation, written exactly as its key in `data/institutions.json` (`Stanford University`) |
| `summary` | one sentence on what it delivers |
| `how` | one or two sentences on how it does it |
| `input`, `output`, `physics`, `builder_tag`, `simulator` | see neighbouring entries |

## A new institution

The icon in front of each work in the figures is the logo of its first institution. If the institution is not yet
in `data/institutions.json`, add it there with its homepage (`url`), a square PNG logo of at least 96 px saved as
`assets/institutions/<slug>.png` (`logo`), and where the logo came from (`logo_source`). The build fails when a work
names an institution that is missing, so nothing is drawn without a logo.

The roadmap, `assets/figures/roadmap.svg`, is drawn by hand. `data/milestones.json` lists its works for the
table under it, and the build fails if the two name different works. News and the simulator table are in
`data/resources.json`.
