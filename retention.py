#!/usr/bin/env python3
"""
retention.py - Compute reinforcement-gated retention scores for memory decay.

This is the arithmetic core of the dream skill's CONSOLIDATE + DECAY phase. It is
deliberately a pure, deterministic stdlib script: it does NOT read or write your
memory files, it does NOT call the network, and it gets the current date passed
in (--now) so a re-run / resume produces identical output. The skill is the only
thing that ever touches your live memory; this script only ranks.

Model (see DESIGN.md for the full derivation and the research lineage):

    r(dt) = exp(-lambda * dt)                      # Ebbinghaus / ACT-R retained fraction
    importance = wn*novelty + wr*relevance + wp*repetition
    S(c)  = b1 * importance + b2 * r(dt)           # blended keep-score in [0, 1]

  - dt is days since the memory was last *reinforced* (genuinely accessed or
    mentioned), not days since it was created. Reinforcement resets dt -> 0.
  - r(dt) is the retained fraction: 1.0 at dt=0, decaying toward 0. (The brief's
    "1 - decay" is the retained fraction when `decay` denotes the forgotten
    fraction; we score on the retained fraction directly. See DESIGN.md.)
  - importance and r are each in [0, 1]; with b1 + b2 = 1, S is in [0, 1].
  - Protected memories (identity / core-workflow feedback) bypass scoring and are
    pinned at S = 1.0. Decay is gated, never applied to these.

S is a *keep* score. Low S = decay candidate. The skill never deletes on the
score alone - it proposes, and Phase 5 gates anything lossy behind approval.

Usage:
    retention.py --now 2026-06-12 < memories.json
    retention.py --now 2026-06-12 --lambda 0.0099 --b1 0.6 --b2 0.4 -i memories.json

Input JSON (stdin or -i FILE): a list of memory objects, or {"memories": [...]}.
Each object:
    {
      "name": "prefers-pnpm",            # filename stem == [[link]] target
      "type": "feedback",                # user | feedback | project | reference
      "last_access": "2026-05-20",       # last reinforcement date (YYYY-MM-DD)
      "protected": false,                # optional; auto-true for identity/core feedback
      "importance": {                    # each component in [0, 1]
        "novelty": 0.4,
        "relevance": 0.8,
        "repetition": 0.6
      }
    }
You may instead pass a precomputed "importance": 0.65 (a float) to skip the mix.

Output JSON: the same list, each item augmented with dt_days, r, importance,
score, half_life_days, and a "tier" recommendation, sorted ascending by score
(weakest memories first - the ones the skill should look at).
"""

import argparse
import json
import math
import sys
from datetime import date


# Default per-day decay rate. lambda = ln(2) / half_life.
# 0.0099/day  ->  half-life ~= 70 days for an un-reinforced, average-importance memory.
DEFAULT_LAMBDA = math.log(2) / 70.0

# Importance component weights (novelty, task-relevance, repetition). Sum to 1.
DEFAULT_W = {"novelty": 0.25, "relevance": 0.45, "repetition": 0.30}

# Blend weights for the keep-score. b1 (importance) + b2 (recency) = 1.
DEFAULT_B1 = 0.6
DEFAULT_B2 = 0.4

# Memory types that are treated as permanent unless the user explicitly retires them.
# Identity ("user") and feedback are load-bearing across every session; we do not let
# the clock erode them. Project/reference memories decay normally.
PROTECTED_TYPES = {"user"}

# Score tiers -> what the skill should propose in the Phase 5 review.
# These are *suggestions*; nothing is destructive without approval.
TIER_KEEP = 0.55        # >= keep, healthy
TIER_REVIEW = 0.35      # [review, keep) surface for a look, maybe merge
# < TIER_REVIEW          decay candidate: propose archive/trim (approval required)


def parse_date(s):
    return date.fromisoformat(s.strip())


def days_between(now, then):
    return max(0, (now - then).days)


def compute_importance(imp, weights):
    """imp is either a float in [0,1] or a dict of components."""
    if isinstance(imp, (int, float)):
        return clamp01(float(imp))
    if not isinstance(imp, dict):
        return 0.5  # no signal -> neutral
    total = 0.0
    for k, w in weights.items():
        total += w * clamp01(float(imp.get(k, 0.0)))
    return clamp01(total)


def clamp01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def tier_for(score):
    if score >= TIER_KEEP:
        return "keep"
    if score >= TIER_REVIEW:
        return "review"
    return "decay-candidate"


def is_protected(mem):
    if mem.get("protected") is True:
        return True
    return mem.get("type") in PROTECTED_TYPES


def score_memory(mem, now, lam, b1, b2, weights):
    out = dict(mem)

    if is_protected(mem):
        out["protected"] = True
        out["dt_days"] = days_between(now, parse_date(mem["last_access"])) if mem.get("last_access") else None
        out["importance"] = 1.0
        out["r"] = 1.0
        out["score"] = 1.0
        out["half_life_days"] = None
        out["tier"] = "protected"
        return out

    last = parse_date(mem["last_access"])
    dt = days_between(now, last)
    r = math.exp(-lam * dt)
    importance = compute_importance(mem.get("importance", 0.5), weights)
    score = b1 * importance + b2 * r

    out["dt_days"] = dt
    out["importance"] = round(importance, 4)
    out["r"] = round(r, 4)
    out["score"] = round(score, 4)
    out["half_life_days"] = round(math.log(2) / lam, 1) if lam > 0 else None
    out["tier"] = tier_for(score)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Compute reinforcement-gated retention scores.")
    p.add_argument("--now", required=True, help="Reference date YYYY-MM-DD (passed in for resume-safety).")
    p.add_argument("--lambda", dest="lam", type=float, default=DEFAULT_LAMBDA,
                   help="Per-day decay rate. lambda = ln(2)/half_life. Default ~70d half-life.")
    p.add_argument("--b1", type=float, default=DEFAULT_B1, help="Weight on importance (default 0.6).")
    p.add_argument("--b2", type=float, default=DEFAULT_B2, help="Weight on recency r(dt) (default 0.4).")
    p.add_argument("-i", "--input", help="Input JSON file (default: stdin).")
    args = p.parse_args(argv)

    now = parse_date(args.now)
    raw = open(args.input).read() if args.input else sys.stdin.read()
    data = json.loads(raw)
    memories = data.get("memories", data) if isinstance(data, dict) else data
    if not isinstance(memories, list):
        print("error: expected a JSON list of memories or {\"memories\": [...]}", file=sys.stderr)
        return 2

    weights = DEFAULT_W
    scored = []
    for mem in memories:
        try:
            scored.append(score_memory(mem, now, args.lam, args.b1, args.b2, weights))
        except (KeyError, ValueError) as e:
            errored = dict(mem)
            errored["error"] = f"could not score: {e}"
            scored.append(errored)

    scored.sort(key=lambda m: m.get("score", 1.0))
    json.dump(scored, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
