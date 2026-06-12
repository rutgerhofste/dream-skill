---
name: dream
description: "Reinforcement-gated memory consolidation for Claude Code. Runs a five-stage sleep cycle over persistent memory: orient, gather + reinforce, consolidate + decay (via a retention score, not hard day-thresholds), associate (REM linking), and a non-destructive review that applies only after approval. Format-neutral - works on any Claude Code auto-memory. Auto-triggers via the Stop hook."
tags: [memory, consolidation, decay, reinforcement, autonomous, hook]
---

# Dream - Reinforcement-Gated Memory Consolidation

> Your agent dreams like you do. Not just tidying notes - *consolidating*: replaying
> what got used, downscaling what didn't, weaving new associations, and letting the
> rest fade. Forgetting is a feature, and it is never silent.

This skill is a derivative of [grandamenium/dream-skill](https://github.com/grandamenium/dream-skill)
(MIT). It replaces that project's simple dedup/prune pass with a **reinforcement-gated
decay** model grounded in recent sleep-consolidation research. See README.md for the
lineage and DESIGN.md for the formula and the research citations.

---

## The model in one paragraph

Memory does not get pruned on a fixed schedule. Each memory carries a **retention
score** `S = b1*importance + b2*r(dt)`, where `r(dt) = exp(-lambda*dt)` is the
Ebbinghaus/ACT-R retained fraction and `dt` is days since the memory was last
*reinforced* (genuinely used or mentioned). Reinforcement resets the clock
(`dt -> 0`) but does **not** restore detail that already faded - decay is lossy, like
real memory. Low-scoring memories become decay candidates; identity and core-workflow
memories are pinned and never decay. Every trim, merge, supersede, or delete surfaces
in the Phase 5 review, and anything lossy waits for explicit approval.

---

## The sleep cycle: five phases

Run them in order. Nothing is written to the live memory directory until Phase 5.

```
1 ORIENT             (light NREM)  survey the store, tag strays, measure always-loaded cost
2 GATHER + REINFORCE (NREM)        mine transcripts for new facts AND reinforcement signals
3 CONSOLIDATE + DECAY (deep NREM)  fold in facts; score retention; clear out junk + dead links
4 ASSOCIATE          (REM)         weave missing [[links]], surface genuine cross-memory insight
5 REVIEW + APPLY     (waking)      non-destructive: show the diff, apply only on approval, clean up
```

---

## Phase 0: Locate memory (format-neutral)

Dream targets **native Claude Code auto-memory** by default and adapts to whatever
layout it finds. Do not hardcode a single project; do not assume a `sessions/` subdir.

```bash
cat ~/.claude/skills/dream/.dream-config 2>/dev/null || echo "DREAM_MEMORY_TYPE=native"
```

Resolve the store and the transcripts:

```bash
# Memory dirs (native): one per project that has memory.
ls -d ~/.claude/projects/*/memory/ 2>/dev/null

# Transcripts live DIRECTLY in the project dir as *.jsonl (NO sessions/ subdir).
ls ~/.claude/projects/*/*.jsonl 2>/dev/null | head
```

| Layout | Memory dir | Transcripts |
|--------|-----------|-------------|
| native (default) | `~/.claude/projects/<project>/memory/` | `~/.claude/projects/<project>/*.jsonl` |
| custom (from `.dream-config`) | `DREAM_MEMORY_PATH` | `~/.claude/projects/<project>/*.jsonl` |

The native memory **format** is one fact per file:

```markdown
---
name: <kebab-slug>          # MUST equal the filename stem so [[links]] resolve
description: <one-liner>     # used for recall relevance
metadata:
  type: user | feedback | project | reference
---

<the durable kernel of the fact>. Link related memories with [[other-name]].
```

`MEMORY.md` is the **index**, loaded into context every session - its line count is a
permanent per-session token cost. One pointer line per memory, no memory content in it.

> If you find a different shape (topic files like `preferences.md`, daily logs,
> a project-root `MEMORY.md`), adapt the same five phases to it. The model is about
> reinforcement and decay, not about a specific file naming convention.

Work on **one project's memory store at a time**.

---

## Phase 1: ORIENT  (light NREM)

**Goal:** map the store before touching anything.

1. List every memory file and read `MEMORY.md`. Record `wc -l MEMORY.md` - this is the
   always-loaded cost. If it is bloated, that is a Phase 3/5 trim target.
2. Read each memory file's frontmatter (`name`, `description`, `type`) and body.
   Build a table: name, type, description, last-modified date, body size.
3. **Tag strays and junk** without ingesting them into context:
   - Files whose `name` != filename stem (broken `[[link]]` targets).
   - Binaries, images, logs, build artifacts that drifted into the memory dir.
     **NEVER read a binary into context** - identify by extension / `file`:
     ```bash
     find <memory_dir> -type f ! -name '*.md' 2>/dev/null
     ```
   - Empty files, `MEMORY.md` pointers to files that no longer exist.
4. Note `metadata.type` for each memory - you will need it in Phase 3 to decide what is
   protected (`user`, core-workflow `feedback`) versus decay-eligible (`project`,
   `reference`).
5. **Read the link graph.** The `[[links]]` between memories are a lightweight knowledge
   graph; read it as one with the helper (read-only, no deps):
   ```bash
   python3 ~/.claude/skills/dream/backlinks.py "<memory_dir>"
   ```
   Note the `broken_links` (dead `[[targets]]` to repair/remove in Phase 3), the `orphans`
   (memories with no links in or out - candidates for weaving in Phase 4), and each node's
   `connectivity` score (how load-bearing it is - feeds importance in Phase 3).

Output: a mental map - what exists, what is stray, what the link graph looks like, and
what the always-loaded cost is.

---

## Phase 2: GATHER + REINFORCE  (NREM)

**Goal:** two passes over recent transcripts at once - (a) extract genuinely new facts,
and (b) compute a **reinforcement signal** for every *existing* memory.

### Find transcripts (direct, no sessions/ subdir)

```bash
find ~/.claude/projects/*/ -maxdepth 1 -name "*.jsonl" -mtime -14 2>/dev/null | sort -r
```

Adjust `-mtime` to taste; 14 days is a reasonable window. JSONL = one JSON object per
line; look at user turns and the assistant turn that follows.

### Pass A - mine new facts (targeted grep, not full reads)

```bash
# Corrections (highest signal)
grep -il "actually\|no,\|wrong\|incorrect\|not right\|stop doing\|don't do\|i said\|i meant\|that's not" ~/.claude/projects/*/*.jsonl 2>/dev/null
# Preferences / standing instructions
grep -il "i prefer\|always\|never\|from now on\|going forward\|remember that\|make sure to\|default to\|we use\|we're using" ~/.claude/projects/*/*.jsonl 2>/dev/null
# Decisions
grep -il "let's go with\|i decided\|we agreed\|switch to\|move to\|the plan is\|chosen" ~/.claude/projects/*/*.jsonl 2>/dev/null
```

For each hit, read only the surrounding lines (`grep -n ... | head`). Extract: the fact,
its date (from the transcript mtime / session date - **convert all relative dates to
absolute**), its likely `type`, and whether it contradicts existing memory.

### Pass B - reinforcement signal (the hard, honest part)

There is **no reliable "this memory was recalled" signal.** Claude Code's recall does not
write back to disk that a memory was retrieved. Naive keyword overlap is far too coarse -
in real testing, generic terms produced ~140/140 false hits. So we use a deliberately
conservative proxy and we are explicit about its uncertainty (see README "Limitations").

For each existing memory, derive a reinforcement signal in this priority order:

1. **Explicit mention** - the memory's *distinctive* terms (proper nouns, file paths,
   tool names, the specific subject - not stopwords like "stripe"/"billing" used in
   passing) appear in a recent transcript. Require a specific, multi-token match, not a
   single common word. This is the strongest available proxy for "was used".
2. **Topical activity** - the user worked in the area this memory governs (e.g. the repo,
   the workflow) even without naming the memory. Weaker; treat as partial reinforcement.
3. **None** - no evidence of use in the window. The clock keeps running (the memory
   decays), but absence of a signal is *not* evidence of uselessness - it is just silence.

Record, per existing memory: a reinforcement verdict (`mentioned` / `topical` / `none`)
and the most recent date of any reinforcement. That date becomes `last_access` in Phase 3.

> **Protected from this proxy:** `user` (identity) memories and core-workflow `feedback`
> memories are treated as permanent regardless of signal. Never let a noisy proxy erode
> who the user is or how they fundamentally want to work.

---

## Phase 3: CONSOLIDATE + DECAY  (deep NREM)

**Goal:** fold in the new facts, then score every memory's retention and identify (not yet
apply) the decay. Still nothing written to live memory.

### 3a. Fold in new facts (Hebbian strengthening)

- **No duplicates.** If a fact already exists, reinforce it (reset its `last_access`,
  optionally sharpen the wording) rather than adding a second file.
- New durable fact -> draft a new one-fact file with proper frontmatter (`name` ==
  filename stem, `description`, `type`) and `[[links]]` to related memories.
- **Supersede via invalidate-not-discard (Zep/Graphiti).** When a new fact replaces an
  old one, do **not** hard-delete the old memory in this phase. Mark it with a validity
  note and point forward, e.g. append to the body:
  `> Superseded 2026-06-12 by [[new-fact]] (previously: develop was the default branch).`
  This preserves the history and the audit trail; hard removal, if any, is a Phase 5
  decision requiring approval.

### 3b. Score retention (the decay model)

Build a JSON array of every decay-eligible memory and score it with the helper. The
helper is pure and gets `--now` passed in, so a resume is deterministic:

```bash
# Build memories.json: [{name,type,last_access,importance:{novelty,relevance,repetition}}, ...]
# last_access = most recent reinforcement date from Phase 2 (or the file's own date).
# importance components in [0,1]: novelty (how non-obvious/unique), relevance (how
# load-bearing for current work), repetition (how often the user has restated it).
# Let Phase 1 connectivity inform relevance: a memory many others link to is
# load-bearing, so raise its relevance (A-MEM / HippoRAG - see DESIGN.md).
python3 ~/.claude/skills/dream/retention.py --now "$(date +%F)" -i /tmp/dream-memories.json
```

`S = b1*importance + b2*r(dt)`, `r(dt) = exp(-lambda*dt)`. Output is sorted weakest-first
and tiered:

- **`keep`** (S >= 0.55) - healthy, leave it.
- **`review`** (0.35 <= S < 0.55) - surface it; consider merging with a neighbor.
- **`decay-candidate`** (S < 0.35) - propose archive/trim in Phase 5 (approval required).
- **`protected`** - identity / core feedback, pinned at S = 1.0, never a candidate.

Reinforcement **resets the clock** (a recently-mentioned memory has small `dt`, high
`r`), but it does **not** restore detail that already decayed. If a memory has already
been trimmed to its kernel, reinforcement keeps the kernel alive - it does not
resurrect the lost specifics. Lossy, like real memory.

### 3c. Glymfatic clear-out

Queue for removal (proposals only, shown in Phase 5):
- Stray/junk files tagged in Phase 1 (build artifacts, stray binaries).
- `MEMORY.md` pointers to files that no longer exist (dead links).
- Empty or content-free memory files.

### 3d. Merge related entries (SleepGate)

Where several `review`-tier memories cover the same theme, draft a single compact
merged memory that keeps each distinct fact, and propose retiring the fragments. Merging
is lossy at the margins, so it is a Phase 5 proposal, not an automatic write.

---

## Phase 4: ASSOCIATE  (REM)

**Goal:** the dreaming pass - form new connections, not new facts. This is the
Zettelkasten/A-MEM step: the value of the store is in its links, not just its notes.

Re-run the graph helper on the draft (post-3a) state to see what is connected:

```bash
python3 ~/.claude/skills/dream/backlinks.py "<memory_dir>"   # or the Phase 5 work copy
```

1. **Repair broken links.** For each entry in `broken_links`, either fix the `[[target]]`
   to the correct existing `name`, or remove the dead link. (Drafted now, shown in Phase 5.)
2. **Weave missing `[[links]]`, bidirectionally.** For each `orphan` and each
   under-connected memory, add `[[other-name]]` where a *real* relationship exists. Make
   links **two-way**: if A meaningfully links to B, ensure B links back to A (unless the
   relation is genuinely directional, e.g. "superseded by"). Verify every target's `name`
   exists (or is a deliberate forward-link to a planned memory).
3. **Memory evolution (A-MEM).** A new fact from this cycle can change how an *old* memory
   should read or link. Where a new memory makes an existing one more precise, update the
   old memory's links (and, lightly, its wording) to reflect the new connection. Keep it
   conservative - this is re-linking, not rewriting history.
4. **Surface genuine cross-memory insight.** If two memories together imply something
   neither states alone (e.g. a preference + a decision that jointly define a workflow),
   draft a short new memory capturing it and link it both ways. **Do not invent.** If
   nothing genuine emerges, add nothing - an empty REM phase is a valid outcome.
   Confabulation is the failure mode to avoid here.

---

## Phase 5: REVIEW + APPLY  (waking)

**Goal:** non-destructive application. The user sees exactly what changes before it lands.

### 5a. Work on a dated copy, never in place

```bash
DREAM_TS="$(date +%Y%m%d-%H%M%S)"
WORK="/tmp/dream-work-$DREAM_TS"
cp -r "<memory_dir>" "$WORK"
```

Apply all drafted changes (new files, reinforcements, supersede notes, link weaving,
proposed merges, the rebuilt `MEMORY.md` index) to the **copy**.

### 5b. Show the diff and classify every change

Produce a review that buckets changes by reversibility:

```bash
diff -ru "<memory_dir>" "$WORK"
```

- **Non-destructive (safe to auto-apply):** new memory files, reinforcement
  (clock resets), added `[[links]]`, supersede *validity notes* (invalidate-not-discard),
  the rebuilt index. These lose no information.
- **Lossy (requires explicit approval):** hard deletes, merges that drop fragments,
  trimming a memory body, archiving a decay-candidate, removing a stray file.

Print a summary: facts added, memories reinforced, superseded (with validity notes),
links woven, decay-candidates proposed, junk/dead-links queued - each with its bucket.

### 5c. Apply

- **Interactive session:** present the diff. Apply non-destructive changes; for each
  lossy change, get explicit approval before applying. Decay is lossy but **never
  silent** - every removal appears here.
- **Headless / auto-trigger (no human present):** apply only the non-destructive bucket.
  Write the lossy proposals to `<memory_dir>/.dream-pending-review.md` so the next
  interactive session can approve or reject them. Never hard-delete unattended.

### 5d. Clean up - leave no temp artifacts

```bash
date +%s > "<memory_dir>/.last-dream"      # reset the 24h trigger clock
rm -rf "$WORK" /tmp/dream-memories.json    # remove ALL working copies / scratch files
rm -f ~/.claude/.dream-pending             # clear the trigger flag
```

Verify nothing was left in `/tmp` and no backup/scratch dir remains in the memory store.

---

## Safety invariants

- **Decay is lossy but never silent.** Every trim/merge/archive/delete appears in the
  Phase 5 review. Hard deletion requires explicit approval.
- **Reinforcement resets the clock; it does not restore lost detail.**
- **Protected types are permanent.** `user` identity and core-workflow `feedback` never
  decay, regardless of the (admittedly imperfect) reinforcement proxy.
- **Supersede, don't silently discard.** Outdated facts get a validity note pointing to
  what replaced them (invalidate-not-discard) before any removal is even considered.
- **Never read binaries into context.** Tag-and-queue by extension only.
- **No hardcoded paths, no repo-specific assumptions, no runtime deps** beyond bash +
  `python3` stdlib. Dates are passed in (`--now`) so a resume is deterministic.
- **First run on a store:** do a dry run (Phases 1-4 + the Phase 5 diff) and confirm with
  the user before applying anything.

---

## Verification (after a run)

1. `wc -l <memory_dir>/MEMORY.md` - index is lean; every pointer resolves to a real file.
2. No relative dates remain in any memory file ("yesterday", "last week").
3. `backlinks.py <memory_dir>` shows `broken: 0` and fewer orphans than before (every
   `[[link]]` target exists, save intentional forward-links).
4. No duplicate memories; superseded facts carry a validity note.
5. `.last-dream` updated; `/tmp` scratch and any work copy removed; `.dream-pending` gone.
6. Print the change summary (added / reinforced / superseded / linked / decayed / queued).
