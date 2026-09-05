# DEVELOPMENT.md — how we build today

**TBX · Bessemer Tech Catalyst, Bengaluru · 5 September 2026**
**Contributors:** Mridul ([@mkvmridul](https://github.com/mkvmridul)) · Ganesh (`@___`)
**Repo:** `git@github.com:mkvmridul/tbx.git` · **Deadline:** demo — fill the clock in below.

Two people, one day, one repo. The enemy is not difficulty, it's **collision** — a big PR that
lands late and breaks the other person's work, or both of us editing `engine.py` at the same
time. Everything here exists to make that impossible.

> **Golden rule:** the repo is the source of truth. Any change to the seam between our two lanes
> goes **[Decisions log](#decisions-log--append-only-newest-at-top) → code → tell the other person**.
> Never a side conversation.

---

## 0. Day zero — DONE. Start here instead.

~~This repo is empty.~~ **The build is in.** `finassist/`, `api/`, `ui/`, `tests/`, `data/`,
`teamkit/` and the docs were ported from `../` and the repo runs green from a clean clone.
Both graders pass: enrichment **PASS**, answer key **20/20 with 0 model calls**.

**Ganesh — this is your setup, run it now:**

```bash
git clone git@github.com:mkvmridul/tbx.git && cd tbx
python generate_dataset.py                        # ~3 s, seeded, byte-identical every run
python -m finassist.enrich data/finance.sqlite    # ~10 s, builds the derived tables
python -m tests.eval_answers                      # must print 20/20
python -m api.server                              # http://localhost:8720
```

No `pip install` — the engine, API, UI and both graders are Python standard library.
`requirements.txt` holds one optional package (`openai`), needed only if you want the model to
rewrite the prose. The numbers are identical either way.

**Mridul runs once:** `gh repo add-collaborator mkvmridul/tbx <ganesh-handle> --permission push`

**Both of us must have a green local run before the first branch.** Ten minutes now saves an
hour of "works on mine" at 4pm.

<details><summary>How the seed was done (kept for the record)</summary>

```bash
cd /Users/mridul_rec/mridul/bessemer/tbx

# .gitignore FIRST — the sqlite file and caches must never land in git
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.env
.venv/
data/*.sqlite
data/*.csv
*.parquet
.DS_Store
EOF

rsync -av --exclude '__pycache__' --exclude '*.sqlite' --exclude '*.csv' \
  ../finassist ../api ../ui ../tests ../data \
  ../generate_dataset.py ../build_answer_key.py \
  ../README.md ../ARCHITECTURE.md ../SAMPLE_ANSWERS.md .

git add -A && git commit -m "Port TBX finassist build into repo" && git push -u origin main
```

Then Ganesh gets access and clones:

```bash
gh repo add-collaborator mkvmridul/tbx <ganesh-handle> --permission push   # Mridul runs this
git clone git@github.com:mkvmridul/tbx.git && cd tbx                        # Ganesh runs this
python generate_dataset.py && python -m finassist.enrich data/finance.sqlite
python -m api.server        # http://localhost:8720 — confirm it runs before writing any code
```

</details>

---

## 1. Lanes — one owner per directory

This is the whole conflict-avoidance strategy. Stay in your lane and merges are free.

| Lane | Owner | Files | What it owns |
|---|---|---|---|
| **Engine** | **Mridul** | `finassist/` · `data/` · `generate_dataset.py` · `build_answer_key.py` | Narration parsing, enrichment, intents, planning, guardrails, the numeric validator, the dataset and the answer key |
| **Surface** | **Ganesh** | `api/` · `ui/` · `tests/` · `SAMPLE_ANSWERS.md` | HTTP server, chat page, CSV export, the two graders, transcripts, demo flow |
| **Shared** | announcement only | `README.md` · `ARCHITECTURE.md` · `DEVELOPMENT.md` · `.gitignore` | Say it in chat + log it before you edit |

**Never edit a file outside your lane.** If you need something from the other lane, that's a
**contract change** (§4), not a quiet edit.

### The Now board — keep this current

Two people don't need a task tracker; they need to know what the other is touching *right now*.
Edit these two lines in your own PR whenever you switch tasks.

```
Mridul  →  (idle)
Ganesh  →  (idle)
```

---

## 2. The loop — every task, no exceptions

```bash
git checkout main && git pull              # 1. start from the other person's latest
# 2. skim the Decisions log below — what changed since my last pull?
git checkout -b engine/vendor-collision    # 3. branch off main, prefixed with your lane
# 4. build ONE small thing
git pull --rebase origin main              # 5. rebase on latest before opening the PR
gh pr create --fill                        # 6. small, green, merged within ~60–90 min
```

**Pull `main` before you start anything.** Building on a three-hour-old `main` is how you get
conflicts and duplicated work.

**Branch naming** encodes the owner: `engine/…` (Mridul) · `surface/…` (Ganesh) · `infra/…`
(shared, announce first). Examples: `engine/anomaly-threshold`, `surface/csv-export`,
`infra/gitignore`.

---

## 3. PRs — small, green, merged the same hour

- **One PR = one thing.** "Add the anomaly z-score guard" — not "anomaly + a UI tweak + a fix."
- Target **under ~200 changed lines**. Bigger? Split it: land the query, then the wiring, then the test.
- **Reviewable in 5 minutes, merged in the hour it opened.** Stale branches rot.
- Many small merges beat one perfect big one. **Incremental > complete.**
- **Squash-merge**, so `main` is one line per change and easy to bisect.
- **1 approval is enough.** With two of us, if the other is heads-down: **self-merge an in-lane
  change** and drop the diff link in chat. Never self-merge a contract or shared-file change.
- **Red doesn't merge.** `main` staying runnable is sacred — it's the demo.

### PR checklist — paste into the description

```
- [ ] Branched off latest main; rebased before opening
- [ ] One logical change, < ~200 lines
- [ ] Only touches my lane (or it's a contract change: announced + logged in DEVELOPMENT.md)
- [ ] main still runs: `python -m api.server` boots and answers a question
- [ ] `python -m tests.eval_answers` still 20/20
- [ ] Docs updated in THIS PR if behaviour or an interface changed
- [ ] No secrets, no data files (.env, *.sqlite, *.csv)
```

---

## 4. The seam — freeze it early, change it loudly

Our two lanes meet at exactly two places. **Freeze both within the first hour.** Everything else
either of us can change freely without telling the other.

**A. `finassist.engine` → the API.** The answer object returned to `api/server.py`: the prose,
the breakdown rows, the evidence ledger, the SQL + params, the confidence/clarification signal.

**B. `POST /ask` → the UI.** The JSON shape the chat page renders, and `GET /export.csv`.

**Changing either one:**

1. Write the new shape in the Decisions log below (one line, what and why).
2. Tell the other person in chat — not in the PR body, they won't read it in time.
3. Then code to it. Both sides, same hour. Never leave the seam broken across a lunch break.

If a PR from both of us touches the same file, that's a **missing contract**, not bad luck. Add
the contract; don't both edit the file.

---

## 5. Before you push — the green bar

```bash
python -m finassist.enrich data/finance.sqlite   # rebuild derived tables (~10 s)
python -m tests.eval_enrichment                  # grades the derived tables
python -m tests.eval_answers                     # 20/20 on the answer key
python -m api.server                             # boots, answers one question by hand
```

`eval_answers` at **20/20 with 0 model calls** is the number the demo rests on. If your change
drops it, that's the PR's problem — fix it in the branch, don't merge and file a follow-up.

The graders read `data/truth/` — the answer key. **The engine never reads it.** Don't "fix" a
grader failure by editing the key.

---

## 6. Sync rhythm

- **Every ~90 minutes:** both pull `main`. Two-minute "what merged since?" — no one should be
  building on a stale mental model.
- **T-3h before demo:** feature freeze on the seam. Contracts stop changing.
- **T-2h:** stop opening new feature PRs. Only fixes that make the demo and the graders solid.
- **T-1h:** `main` is the demo build. Anything risky goes on a branch and stays there.

---

## Decisions log — append-only, newest at top

One line per change that touches a contract, the schema, the seam, or scope. Cheaper than a
meeting. **If you changed something the other person depends on and it isn't here, they don't
know about it.**

Format: `HH:MM · who · what changed · which file · why`

<!-- Add new entries directly under this line -->

- `11:10 · Mridul · .gitignore also excludes data/seed_mysql.sql (25 MB) and data/truth/transaction_truth.csv (11 MB) · .gitignore · the drafted rule only caught data/*.sqlite and data/*.csv, which misses both — the nested truth CSV is one directory deeper than the pattern reaches. All of it is rebuilt byte-for-byte by generate_dataset.py (seed 20260905), so none of it belongs in git. Kept in git: answer_key.json, counterparty.csv, entity.csv, DATASET.md, schema.sql. Repo is 428 KB; 155 MB stays local.`
- `11:06 · Mridul · Ported the build in; added teamkit/ (demo beat sheet), requirements.txt and docs/ (problem.pdf + the TBX schema doc) beyond the drafted rsync list · repo root · "move everything" meant everything. clickathon-inmobi-2026-main deliberately NOT moved — it is a separate git repo and would nest.`

- `--:-- · Mridul · Repo seeded from ../bessemer; lanes assigned (engine/surface); DEVELOPMENT.md is the process doc · initial commit`
