# Dream - Reinforcement-Gated Memory Consolidation for Claude Code

> Your agent dreams like you do. Not just tidying notes - *consolidating*: replaying
> what got used, downscaling what didn't, weaving new associations, and letting the rest
> fade. Forgetting is a feature, and it is never silent.

Dream runs a five-stage **sleep cycle** over Claude Code's persistent memory. Instead of
deleting on a fixed schedule, it gives every memory a **retention score** and lets weak,
un-reinforced memories fade while pinning the ones that define who you are and how you
work. It is **format-neutral** - it works on any Claude Code auto-memory layout, not a
specific repo's conventions.

This project is a derivative of
[**grandamenium/dream-skill**](https://github.com/grandamenium/dream-skill) (MIT). The
original ran a four-phase dedup/prune pass. This fork replaces the prune step with a
reinforcement-gated **decay** model grounded in recent sleep-consolidation research.
The lineage is preserved in the commit history and here.

---

## The sleep model

Human memory does not consolidate on a timer; it consolidates by **replay and
reinforcement** during sleep, downscaling the unused and strengthening the used. Dream
mirrors that across five phases, modelled on sleep stages:

| Phase | Sleep stage | What it does |
|-------|-------------|--------------|
| **1. Orient** | light NREM | Survey the store; tag strays/junk (never reading binaries); measure the always-loaded `MEMORY.md` cost |
| **2. Gather + Reinforce** | NREM | Mine recent transcripts for new facts **and** compute a reinforcement signal for every existing memory |
| **3. Consolidate + Decay** | deep NREM | Fold in new facts; score retention; clear out junk and dead links |
| **4. Associate** | REM | Weave missing `[[links]]`; surface genuine cross-memory insight (never invent) |
| **5. Review + Apply** | waking | Non-destructive: work on a dated copy, show the diff, apply lossy changes only after approval, clean up every temp file |

Nothing is written to live memory until Phase 5.

### Retention, not a calendar

Each memory carries a keep-score:

```
S = b1 * importance + b2 * r(dt)        r(dt) = exp(-lambda * dt)
```

`dt` is days since the memory was last **reinforced** (genuinely used or mentioned), not
since it was created. A single reinforcement resets the clock; importance (a mix of
novelty, task-relevance, and repetition) floors the score so that valuable-but-quiet
memories are not condemned by silence. Identity and core-workflow memories are **pinned**
and never decay. Full derivation in [DESIGN.md](DESIGN.md); the scorer is
[retention.py](retention.py).

**Decay is lossy but never silent**, and reinforcement resets the clock without restoring
detail that already faded - exactly like real memory.

---

## <a name="research"></a>Research

The model borrows specific, named mechanisms from four lines of work. (How each maps onto
the design is in [DESIGN.md §4](DESIGN.md#4-relationship-to-the-research).)

- **SCM - Sleep-Consolidated Memory with Algorithmic Forgetting for LLMs** (Shinde),
  [arXiv:2604.20943](https://arxiv.org/abs/2604.20943) - the NREM/REM stage decomposition,
  multi-dimensional importance tagging, proportional downscaling, and REM-phase
  association. This is where the retention-score-instead-of-day-tiers idea comes from.
- **Learning to Forget (SleepGate)** - sleep-inspired consolidation for proactive
  interference, [arXiv:2603.14517](https://arxiv.org/abs/2603.14517) - conflict-aware
  tagging and merging related entries into compact summaries.
- **Zep: A Temporal Knowledge Graph Architecture for Agent Memory** (Rasmussen et al.),
  [arXiv:2501.13956](https://arxiv.org/abs/2501.13956) - **invalidate-not-discard**: mark
  an outdated memory with a validity note instead of hard-deleting it.
- **ACT-R base-level activation + the Ebbinghaus forgetting curve** - the `exp(-lambda*dt)`
  recency term and the recency+frequency basis of reinforcement. See Honda et al.,
  *Human-Like Remembering and Forgetting in LLM Agents: An ACT-R-Inspired Memory
  Architecture* (HAI 2025), and Anderson & Schooler (1991) for the base-level equation.
  *(The originating brief cited [arXiv:2512.20651](https://arxiv.org/abs/2512.20651) here;
  that ID is actually "Memory Bear AI", which grounds itself in ACT-R/Ebbinghaus but isn't
  the ACT-R paper - we cite the directly relevant work instead. See
  [DESIGN.md §4.4](DESIGN.md#44-act-r-base-level-activation--ebbinghaus).)*

---

## Limitations (read this)

This skill is a heuristic, and its central quantity is not actually observable. Being
honest about that:

- **There is no reliable "this memory was used" signal.** Claude Code's recall does not
  write back to disk that a memory was retrieved - no access log, no last-read timestamp.
  Everything Dream computes about "use" is a **proxy**.
- **Keyword overlap does not work.** The obvious proxy - "the memory's words appear in a
  recent transcript" - is far too coarse. In real testing, generic terms produced
  ~**140/140 false hits**, because common subject words appear constantly in unrelated
  context. Dream instead requires *specific, multi-token, distinctive* mentions and treats
  topical activity as only partial reinforcement (see
  [DESIGN.md §3](DESIGN.md#3-reinforcement-the-assumptions-and-the-uncertainty)).
- **Absence of a signal is not evidence of uselessness.** A memory can be silently
  load-bearing. Dream mitigates this with the importance floor (`b1*importance`), so a
  quiet-but-valuable memory drops only to the *review* tier, never straight to deletion.
- **Identity and core workflow are treated as permanent.** `user` memories and
  core-workflow `feedback` are pinned and exempt from the proxy entirely. We accept
  keeping some stale-ish identity over ever silently forgetting who you are.
- **Decay is lossy and irreversible at the detail level.** Reinforcement keeps a memory's
  kernel alive but does not resurrect specifics that were already trimmed.
- **The model proposes; you dispose.** Every lossy change (delete, merge, trim, archive)
  surfaces in the Phase 5 review and requires explicit approval. In headless/auto runs,
  only non-destructive changes are applied; lossy proposals are queued to
  `.dream-pending-review.md` for the next interactive session.

---

## Install

### Option 1 - clone into your skills directory

```bash
git clone https://github.com/rutgerhofste/dream-skill.git ~/.claude/skills/dream
```

### Option 2 - installer (sets up the auto-trigger Stop hook)

```bash
git clone https://github.com/rutgerhofste/dream-skill.git /tmp/dream-skill
bash /tmp/dream-skill/install.sh --auto
```

### Option 3 - manual

1. Copy `SKILL.md`, `retention.py`, `should-dream.sh`, and `dream-hook.sh` to
   `~/.claude/skills/dream/`.
2. `chmod +x ~/.claude/skills/dream/*.sh ~/.claude/skills/dream/retention.py`
3. Start a session and run `/dream`.

---

## Auto-trigger

A native Claude Code **Stop hook** fires when a session ends and runs a ~10 ms check
(`should-dream.sh`): has it been 24+ hours since the last dream? If yes, the next
consolidation is queued. Zero overhead when the condition isn't met. Headless auto-runs
apply only non-destructive changes and queue anything lossy for your review.

---

## What's included

| File | Purpose |
|------|---------|
| `SKILL.md` | The five-phase sleep cycle - the skill prompt |
| `retention.py` | Pure stdlib scorer for the retention score `S(c)` (resume-safe, `--now` passed in) |
| `DESIGN.md` | The retention formula, importance definition, reinforcement assumptions, and research mapping |
| `should-dream.sh` | 24-hour condition checker for the Stop hook |
| `dream-hook.sh` | Stop hook that triggers a background consolidation when due |
| `install.sh` | Installer, with `--auto` for the hook and `--uninstall` |
| `test-dream.sh` | Builds a one-fact-per-file fixture store with known issues and verifies a run |

---

## Usage

```
/dream
```

Or just use Claude Code normally after `install.sh --auto` - it consolidates in the
background roughly every 24 hours.

## Requirements

- Claude Code with auto-memory support
- `bash` + `python3` (standard library only) - no additional dependencies

## License

MIT. Derivative of [grandamenium/dream-skill](https://github.com/grandamenium/dream-skill);
original copyright retained.
