# DESIGN - The reinforcement-gated decay model

This document specifies the decay model behind the dream skill: the retention-score
formula, the definition of *importance*, the reinforcement assumptions and their
honest uncertainty, and how the design relates to the research it borrows from
(SCM, SleepGate, Zep/Graphiti, ACT-R).

The implementation of the formula lives in [`retention.py`](retention.py); the
process that uses it lives in [`SKILL.md`](SKILL.md). This file is the *why*.

---

## 1. The retention score

Every decay-eligible memory `c` carries a **keep-score**:

```
S(c) = b1 * importance(c) + b2 * r(dt)

r(dt) = exp(-lambda * dt)
```

| Symbol | Meaning | Default |
|--------|---------|---------|
| `importance(c)` | how much this memory is worth, in `[0,1]` (§2) | computed |
| `r(dt)` | retained fraction since last reinforcement, in `[0,1]` | computed |
| `dt` | days since the memory was last **reinforced** (not since created) | from Phase 2 |
| `lambda` | per-day decay rate, `lambda = ln(2) / half_life` | `ln(2)/70 ≈ 0.0099` |
| `b1` | weight on importance | `0.6` |
| `b2` | weight on recency | `0.4` |

With `b1 + b2 = 1` and both terms in `[0,1]`, `S ∈ [0,1]`. It is a **keep** score:
high = retain, low = decay candidate. Tiers (tunable in `retention.py`):

- `S ≥ 0.55` → **keep**
- `0.35 ≤ S < 0.55` → **review** (merge candidate)
- `S < 0.35` → **decay-candidate** (archive/trim, approval required)
- protected types → pinned at `S = 1.0`

### 1.1 Why `r(dt) = exp(-lambda*dt)` and a note on the brief's sign

The retained fraction follows the classic Ebbinghaus/ACT-R exponential: full strength
at the moment of access (`r(0) = 1`), decaying smoothly toward 0. This is the *retained*
fraction, not the *forgotten* fraction.

The originating brief wrote the recency term as `(1 - decay)` with
`decay = exp(-lambda*dt)`. Taken literally that inverts the sign - it would *reward* old,
un-reinforced memories and penalise fresh ones. We read the brief's `decay` as the
**forgotten** fraction (`1 - exp(-lambda*dt)`), so `1 - decay = exp(-lambda*dt)` = the
retained fraction, which is exactly `r(dt)` above. We score on the retained fraction
directly to keep the intent (recent reinforcement → higher keep-score) unambiguous.

### 1.2 Half-life, not a cliff

The previous design used hard tiers (e.g. "older than 90 days → prune"). A cliff treats
an 89-day-old and a 91-day-old memory completely differently for no real reason. The
exponential is continuous: an un-reinforced, average-importance memory loses half its
recency contribution every `~70` days, and a single genuine reinforcement resets `dt` to
0 and restores `r` to 1. Tune the aggressiveness with one knob, `lambda` (via
`--lambda`), or per-type half-lives if you extend the helper.

### 1.3 Reinforcement is lossy

Reinforcement resets the **clock** (`dt → 0`), raising `r` and therefore `S`. It does
**not** restore detail that already decayed. If deep-NREM already trimmed a memory to its
kernel, a later mention keeps that kernel alive but does not resurrect the specifics that
were dropped. This is intentional and mirrors human reconsolidation: you remember *that*
something mattered long before you remember every detail of it.

---

## 2. Importance

`importance(c)` is a weighted mix of three components, each scored in `[0,1]`:

```
importance = wn*novelty + wr*relevance + wp*repetition
           = 0.25*novelty + 0.45*relevance + 0.30*repetition     (defaults)
```

| Component | Question it answers | High when |
|-----------|--------------------|-----------|
| **novelty** | How non-obvious / unique is this? | Surprising, not derivable from the repo or common knowledge |
| **relevance** | How load-bearing for current work? | Governs an active workflow, repo, or decision |
| **repetition** | How often has the user restated it? | The user keeps repeating / re-confirming it |

Relevance is weighted highest because a memory's *current* utility is the best predictor
of future utility. Repetition is the ACT-R "frequency" term lifted to the consolidation
layer (the user bothering to say it twice is a strong durability signal). Novelty guards
against storing the obvious.

`retention.py` also accepts a precomputed scalar `importance` if the skill prefers to
judge it holistically rather than via the three-way mix.

---

## 3. Reinforcement: the assumptions and the uncertainty

The whole model rests on one shaky quantity: **when was this memory last used?** This
section is deliberately blunt about what we can and cannot know.

### 3.1 The core problem

Claude Code's recall does **not** write back to disk that a memory was retrieved. There
is no access log, no "last read" timestamp, no usage counter. So "was this memory used?"
is **not directly observable**. Anything we compute is a proxy.

### 3.2 Why the obvious proxy fails

The obvious proxy is keyword overlap: if the memory's words appear in a recent transcript,
call it used. In real testing this was useless - generic terms (a product name, "billing",
a common verb) produced ~**140/140 false hits**, because those words appear constantly in
unrelated context. Keyword overlap measures *topic presence*, not *memory use*, and the two
diverge badly for any memory whose subject is a common word.

### 3.3 The proxy we use instead

Reinforcement is graded, conservative, and explicit (see SKILL Phase 2, Pass B):

1. **Explicit distinctive mention** (strongest) - the memory's *specific, multi-token*
   identifiers (proper nouns, file paths, tool names, the precise subject) appear in a
   recent transcript. Require specificity; reject single common words.
2. **Topical activity** (partial) - the user worked in the area the memory governs without
   naming it. Counts as weak reinforcement.
3. **None** (silence) - no evidence in the window. The clock keeps running, but **absence
   of a signal is not evidence of uselessness.** A memory can be silently load-bearing.

This is combined with importance: a high-importance memory with no recent signal still
scores reasonably (the `b1*importance` term floors it), so silence alone never condemns a
valuable memory - it only lowers it toward the review tier where a human looks.

### 3.4 The fallback: treat-as-permanent

For the memories where a wrong forget is most costly, we **opt out of the proxy entirely**:

- `user` (identity) memories - who the user is.
- core-workflow `feedback` memories - how they fundamentally want to work.

These are pinned at `S = 1.0` (`PROTECTED_TYPES` / `protected: true` in `retention.py`).
The reinforcement proxy is too noisy to be trusted with load-bearing identity, so we
don't let it touch them. This is a deliberate precision/recall trade: we accept keeping
some stale-ish identity memory rather than ever silently forgetting who the user is.

### 3.5 What this means in practice

The model is honest about being a heuristic. It is good at: surfacing obviously-dead
junk, dead links, and superseded facts; keeping recently-used and important memories.
It is weak at: distinguishing a genuinely-dead memory from a silently-useful one whose
subject simply did not come up. That weakness is mitigated by (a) the importance floor,
(b) protected types, and (c) the hard rule that every lossy action requires human
approval. The model proposes; the human disposes.

---

## 4. Relationship to the research

The design borrows specific mechanisms from four lines of work. Citations and the honest
caveat about one of them are in [README.md](README.md#research).

### 4.1 SCM - Sleep-Consolidated Memory (arXiv 2604.20943)

The backbone. SCM frames offline consolidation as distinct **NREM** and **REM** stages
with **intentional, value-based forgetting**. We adopt:

- **The stage decomposition** - our five phases map onto light NREM (orient), NREM
  (gather), deep NREM (consolidate + decay), REM (associate), waking (review).
- **Multi-dimensional importance** - SCM's importance tagging becomes our
  `novelty/relevance/repetition` mix (§2).
- **Proportional downscaling** - SCM downscales memory strength during NREM
  (`s ← 0.8·s`-style). We express this continuously as `r(dt) = exp(-lambda·dt)`: a
  decay applied by elapsed un-reinforced time rather than a fixed multiplier per pass.
  This is the single biggest change from the old skill - **a retention score, not the raw
  60/120/180-day tiers**.
- **REM random-walk dreaming** - SCM's REM phase forms new associations. Our Phase 4
  weaves `[[links]]` and surfaces genuine cross-memory insight (and, crucially, adds
  nothing when nothing genuine emerges - no confabulation).

### 4.2 SleepGate / "Learning to Forget" (arXiv 2603.14517)

Addresses **proactive interference** - stale entries disrupting retrieval of current
ones. We adopt two mechanisms:

- **Conflict-aware tagging** - when a new fact conflicts with an old one, tag the
  conflict (our supersede note) rather than letting both coexist silently.
- **Consolidation into compact summaries** - our Phase 3d merge of related `review`-tier
  memories into a single compact memory that preserves each distinct fact.

### 4.3 Zep / Graphiti (arXiv 2501.13956)

A temporal knowledge graph for agent memory whose key idea we lift wholesale:
**invalidate-not-discard**. When a fact is superseded, Zep marks the old edge with a
validity interval rather than deleting it. We apply this to supersede (SKILL Phase 3a): an
outdated memory gets a `> Superseded YYYY-MM-DD by [[new-fact]] (previously: ...)` note
and is kept, preserving history and the audit trail. Hard removal is a separate,
approval-gated step - never the default response to "this is outdated".

### 4.4 ACT-R base-level activation + Ebbinghaus

The `r(dt)` term and the role of **frequency** (our `repetition`) and **recency** (our
`dt`) come from ACT-R's base-level activation, which itself formalises the Ebbinghaus
forgetting curve: activation rises with frequency of access and decays with time since
access. Reinforcement = recency + frequency of *genuine* access (§3) is exactly this,
adapted to the fact that we can only proxy "access".

**Citation caveat (honest):** the originating brief cited arXiv **2512.20651** for this.
That ID actually resolves to *"Memory Bear AI"*, which *grounds itself* in ACT-R and the
Ebbinghaus curve but is not primarily an ACT-R paper. The directly relevant work is
Honda et al., *"Human-Like Remembering and Forgetting in LLM Agents: An ACT-R-Inspired
Memory Architecture"* (HAI 2025), plus the foundational ACT-R base-level learning
equation (Anderson & Schooler, 1991). We cite those rather than repeat a mislabelled ID.

---

## 5. Parameter reference

| Parameter | Where | Default | Effect of increasing |
|-----------|-------|---------|----------------------|
| `lambda` | `--lambda` / `DEFAULT_LAMBDA` | `ln(2)/70` | Faster decay (shorter half-life) |
| `b1` | `--b1` / `DEFAULT_B1` | `0.6` | Importance dominates recency |
| `b2` | `--b2` / `DEFAULT_B2` | `0.4` | Recency dominates importance |
| `wn,wr,wp` | `DEFAULT_W` | `0.25/0.45/0.30` | Re-weight importance components |
| keep / review tiers | `TIER_KEEP`, `TIER_REVIEW` | `0.55`, `0.35` | Stricter/looser retention |
| protected types | `PROTECTED_TYPES` | `{user}` | What never decays |

All parameters are passed in or live in `retention.py` as named constants; none depend on
wall-clock state inside the script (`--now` is supplied by the caller), so re-runs and
resumes are deterministic.
