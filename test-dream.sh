#!/usr/bin/env bash
#
# test-dream.sh - Test harness for the reinforcement-gated dream skill.
#
# Usage:
#   ./test-dream.sh selftest   Deterministic unit test of retention.py (no LLM needed)
#   ./test-dream.sh setup      Create a one-fact-per-file fixture store with known issues
#   ./test-dream.sh verify     Check consolidation results after running /dream
#   ./test-dream.sh teardown   Remove fixtures
#
# Workflow:
#   1. ./test-dream.sh selftest        # proves the math, always runnable
#   2. ./test-dream.sh setup           # builds the fixture memory store + transcripts
#   3. Run /dream in Claude Code, pointed at the dream-test-project store
#   4. ./test-dream.sh verify
#   5. ./test-dream.sh teardown
#
# Layout matches native Claude Code auto-memory:
#   - memory store:  ~/.claude/projects/<project>/memory/   (one fact per .md file)
#   - transcripts:   ~/.claude/projects/<project>/*.jsonl   (DIRECT, no sessions/ subdir)

# This harness builds shell assertions and runs them through `eval` in chk(); several
# locals are read only inside those eval'd strings, which static analysis cannot see.
# shellcheck disable=SC2034

set -euo pipefail

TEST_PROJECT="dream-test-project"
BASE_DIR="$HOME/.claude/projects/$TEST_PROJECT"
MEMORY_DIR="$BASE_DIR/memory"          # transcripts live directly in BASE_DIR
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

# Dates derived relative to "now" so the fixture is always inside the scan window.
d_ago() { date -v-"$1"d +%Y-%m-%d 2>/dev/null || date -d "$1 days ago" +%Y-%m-%d; }
touch_ago() { # touch_ago <file> <days> <HHMM>
    touch -t "$(date -v-"$2"d +%Y%m%d"$3" 2>/dev/null || date -d "$2 days ago" +%Y%m%d"$3")" "$1" 2>/dev/null || true
}

# ============================================================================
# SELFTEST - deterministic check of retention.py (no Claude run required)
# ============================================================================
do_selftest() {
    info "Unit-testing retention.py with a fixed fixture..."
    local out
    out=$(python3 "$SCRIPT_DIR/retention.py" --now 2026-06-12 <<'JSON'
[
  {"name":"user-identity","type":"user","last_access":"2026-01-01","importance":{"novelty":0.1,"relevance":1,"repetition":1}},
  {"name":"fresh-feedback","type":"feedback","last_access":"2026-06-05","importance":{"novelty":0.4,"relevance":0.9,"repetition":0.7}},
  {"name":"stale-reference","type":"reference","last_access":"2025-11-01","importance":0.15}
]
JSON
)
    local total=0 passed=0
    chk() { total=$((total+1)); if eval "$1"; then pass "$2"; passed=$((passed+1)); else fail "$2"; fi; }

    chk "echo '$out' | python3 -c 'import json,sys; d={m[\"name\"]:m for m in json.load(sys.stdin)}; sys.exit(0 if d[\"user-identity\"][\"tier\"]==\"protected\" and d[\"user-identity\"][\"score\"]==1.0 else 1)'" \
        "user identity is protected (S=1.0)"
    chk "echo '$out' | python3 -c 'import json,sys; d={m[\"name\"]:m for m in json.load(sys.stdin)}; sys.exit(0 if d[\"fresh-feedback\"][\"tier\"]==\"keep\" else 1)'" \
        "fresh, important feedback is kept"
    chk "echo '$out' | python3 -c 'import json,sys; d={m[\"name\"]:m for m in json.load(sys.stdin)}; sys.exit(0 if d[\"stale-reference\"][\"tier\"]==\"decay-candidate\" else 1)'" \
        "stale, low-importance reference is a decay candidate"
    chk "echo '$out' | python3 -c 'import json,sys; a=json.load(sys.stdin); sys.exit(0 if a[0][\"name\"]==\"stale-reference\" else 1)'" \
        "output sorted weakest-first"

    # backlinks.py on a tiny two-node graph: a -> b. b has a backlink; both connected.
    local g
    g=$(mktemp -d)
    printf -- '---\nname: a\n---\nlinks to [[b]].\n' > "$g/a.md"
    printf -- '---\nname: b\n---\nno outbound links.\n' > "$g/b.md"
    local bl
    bl=$(python3 "$SCRIPT_DIR/backlinks.py" "$g")
    chk "echo '$bl' | python3 -c 'import json,sys; d={n[\"name\"]:n for n in json.load(sys.stdin)[\"nodes\"]}; sys.exit(0 if d[\"b\"][\"inbound\"]==[\"a\"] else 1)'" \
        "backlinks.py records b's backlink from a"
    chk "echo '$bl' | python3 -c 'import json,sys; r=json.load(sys.stdin); sys.exit(0 if r[\"summary\"][\"orphans\"]==0 and r[\"summary\"][\"broken\"]==0 else 1)'" \
        "backlinks.py reports no orphans and no broken links"
    rm -rf "$g"

    # secret_scan.py: delegate to its own deterministic self-check.
    chk "python3 '$SCRIPT_DIR/secret_scan.py' --selftest >/dev/null 2>&1" \
        "secret_scan.py self-check passes"
    # And confirm it actually blocks a (synthetic) leaked key end-to-end.
    chk "! printf 'token ghp_%s\n' \"\$(printf 'a%.0s' {1..36})\" | python3 '$SCRIPT_DIR/secret_scan.py' --stdin >/dev/null 2>&1" \
        "secret_scan.py blocks a leaked credential (exit 1)"

    echo ""
    echo "  helper selftest: ${passed}/${total} passed"
    [[ $passed -eq $total ]] || exit 1
}

# ============================================================================
# SETUP - build a one-fact-per-file fixture store
# ============================================================================
do_setup() {
    info "Creating test environment at $BASE_DIR"
    if [[ -d "$BASE_DIR" ]]; then
        warn "Test directory already exists. Run '$0 teardown' first."
        exit 1
    fi
    mkdir -p "$MEMORY_DIR"

    # --- MEMORY.md index (with a deliberately dead pointer) ---
    cat > "$MEMORY_DIR/MEMORY.md" << 'IDXEOF'
# Memory Index

- [User identity](user-identity.md) — who the user is
- [Error handling](prefers-result-types.md) — core workflow: Result types, not try/catch
- [Default branch](default-branch.md) — git default branch
- [API base URL](api-base-url.md) — where the API lives
- [Package manager](package-manager.md) — which package manager to use
- [Editor habit](stale-sublime.md) — quick-edit editor preference
- [Old deploy notes](gone.md) — DEAD POINTER: this file does not exist
IDXEOF

    # --- Protected: user identity ---
    cat > "$MEMORY_DIR/user-identity.md" << 'EOF'
---
name: user-identity
description: The user's name and basic identity
metadata:
  type: user
---

The user's name is Jordan.
EOF

    # --- Protected: core-workflow feedback ---
    cat > "$MEMORY_DIR/prefers-result-types.md" << 'EOF'
---
name: prefers-result-types
description: Error-handling convention - Result types via neverthrow
metadata:
  type: feedback
---

Use Result types via neverthrow for error handling, not try/catch. Related: [[package-manager]].
EOF

    # --- Decay-eligible project fact, will be SUPERSEDED to main ---
    cat > "$MEMORY_DIR/default-branch.md" << 'EOF'
---
name: default-branch
description: The git default branch for this project
metadata:
  type: project
---

The default branch is develop.
EOF

    # --- Decay-eligible project fact, will be SUPERSEDED to new URL ---
    cat > "$MEMORY_DIR/api-base-url.md" << 'EOF'
---
name: api-base-url
description: Base URL of the API
metadata:
  type: project
---

The API base URL is https://api.oldservice.com/v1.
EOF

    # --- Feedback fact, will be SUPERSEDED yarn -> pnpm ---
    cat > "$MEMORY_DIR/package-manager.md" << 'EOF'
---
name: package-manager
description: Which package manager to use
metadata:
  type: feedback
---

Use yarn for package management.
EOF

    # --- Stale reference, never reinforced -> should decay ---
    cat > "$MEMORY_DIR/stale-sublime.md" << 'EOF'
---
name: stale-sublime
description: Quick-edit editor preference
metadata:
  type: reference
---

Likes Sublime Text for quick file edits.
EOF
    touch_ago "$MEMORY_DIR/stale-sublime.md" 220 1200

    # --- Stray binary junk that drifted into the store (must NEVER be read) ---
    printf '\x89PNG\r\n\x1a\n\x00\x00\x00stray-binary-do-not-read' > "$MEMORY_DIR/screenshot.png"

    # --- Transcript: 2 days ago - supersedes + new fact + reinforces neverthrow ---
    local s1; s1="$BASE_DIR/$(d_ago 2)_work.jsonl"
    cat > "$s1" << 'EOF'
{"type":"human","content":"Let's go with main as the default branch instead of develop. Simplifying git flow."}
{"type":"assistant","content":"Switching the default branch from develop to main."}
{"type":"human","content":"The API moved. Base URL is now https://api.newplatform.io/v2."}
{"type":"assistant","content":"Updating references to https://api.newplatform.io/v2."}
{"type":"human","content":"We're on pnpm now, not yarn. Switched last month."}
{"type":"assistant","content":"Got it - pnpm instead of yarn."}
{"type":"human","content":"Make sure error handling still uses neverthrow Result types in the new module."}
{"type":"assistant","content":"Yes - neverthrow Result types throughout the new module, no try/catch."}
{"type":"human","content":"From now on, always include a test plan in PR descriptions. Every PR."}
{"type":"assistant","content":"Understood - every PR description gets a test plan section."}
EOF
    touch_ago "$s1" 2 1400

    # --- Transcript: 1 day ago - new fact + identity reinforcement ---
    local s2; s2="$BASE_DIR/$(d_ago 1)_review.jsonl"
    cat > "$s2" << 'EOF'
{"type":"human","content":"I review code on my phone during commute, so keep PR descriptions as scannable bullet points, not paragraphs."}
{"type":"assistant","content":"Will do, Jordan - bullet points and scannable PR descriptions since you review on mobile."}
EOF
    touch_ago "$s2" 1 1600

    echo ""
    info "Fixture created. Structure:"
    find "$BASE_DIR" -type f | sort | sed 's/^/  /'
    cat << EOF

=== WHAT A CORRECT DREAM RUN SHOULD DO ===
  Non-destructive (safe even headless):
   - Supersede default-branch (develop -> main) with a validity note, keep history
   - Supersede api-base-url (oldservice -> newplatform.io/v2) with a validity note
   - Supersede package-manager (yarn -> pnpm) with a validity note
   - Reinforce prefers-result-types (neverthrow mentioned) - reset its clock, keep it
   - Add new memory: test plan required in every PR description
   - Add new memory: PR descriptions as scannable bullet points (mobile review)
   - Remove the dead 'gone.md' pointer from MEMORY.md (target does not exist)
   - Weave [[links]] where related
   - Keep user-identity (Jordan) and prefers-result-types untouched (protected)
  Lossy (proposed only; queued to .dream-pending-review.md if headless):
   - stale-sublime is a decay candidate (never reinforced, ~220 days old)
   - screenshot.png is stray junk - QUEUE for removal, NEVER read it into context

=== NEXT STEPS ===
  1. Run /dream in Claude Code against project '$TEST_PROJECT'
  2. ./test-dream.sh verify
EOF
}

# ============================================================================
# VERIFY - check results (tolerant of the non-destructive / queued model)
# ============================================================================
do_verify() {
    [[ -d "$BASE_DIR" ]] || { fail "Test dir missing. Run '$0 setup' first."; exit 1; }

    echo ""; info "Verifying dream consolidation results..."; echo ""
    local total=0 passed=0
    chk() { total=$((total+1)); if eval "$1"; then pass "$2"; passed=$((passed+1)); else fail "$2"; fi; }

    local idx="$MEMORY_DIR/MEMORY.md"
    local all; all=$(cat "$MEMORY_DIR"/*.md 2>/dev/null || echo "")
    local pending="$MEMORY_DIR/.dream-pending-review.md"
    local pending_txt; pending_txt=$(cat "$pending" 2>/dev/null || echo "")

    chk "[[ -f '$idx' ]]" "MEMORY.md index exists"
    chk "! grep -q 'gone.md' '$idx' 2>/dev/null" "Dead pointer 'gone.md' removed from index"

    # Supersede via invalidate-not-discard: new value present AND history kept.
    chk "grep -qi 'main' '$MEMORY_DIR/default-branch.md' 2>/dev/null" "default-branch updated to main"
    chk "grep -qiE 'supersed|previously|develop' '$MEMORY_DIR/default-branch.md' 2>/dev/null" \
        "default-branch keeps a validity note (history not hard-discarded)"
    chk "echo \"\$all\" | grep -qi 'newplatform.io'" "API URL updated to newplatform.io"
    chk "echo \"\$all\" | grep -qi 'pnpm'" "package manager updated to pnpm"

    # Reinforcement / protection
    chk "[[ -f '$MEMORY_DIR/prefers-result-types.md' ]] && grep -qi 'neverthrow' '$MEMORY_DIR/prefers-result-types.md'" \
        "core-workflow feedback (neverthrow) retained"
    chk "echo \"\$all\" | grep -qi 'jordan'" "user identity (Jordan) retained"

    # New facts captured
    chk "echo \"\$all\" | grep -qiE 'test plan'" "new fact captured: test plan in PR descriptions"
    chk "echo \"\$all\" | grep -qiE 'bullet|scannable|mobile|phone'" "new fact captured: bullet-point PR descriptions"

    # Decay is lossy but never silent: the candidate must be queued for review OR removed.
    chk "echo \"\$pending_txt\" | grep -qi 'sublime' || [[ ! -f '$MEMORY_DIR/stale-sublime.md' ]]" \
        "stale-sublime decay surfaced (queued for review or removed - not silently)"

    # Stray binary: never hard-deleted unattended; either still present or explicitly queued.
    chk "[[ -f '$MEMORY_DIR/screenshot.png' ]] || echo \"\$pending_txt\" | grep -qi 'screenshot.png'" \
        "stray binary not silently deleted (present or queued for review)"

    # Hygiene
    chk "! grep -riE '(yesterday|last week|last month|tomorrow|today)' '$MEMORY_DIR'/*.md 2>/dev/null | grep -viE 'supersed|previously|was changed' >/dev/null" \
        "no unresolved relative dates in memory files"
    chk "! ls /tmp/dream-work-* >/dev/null 2>&1 && [[ ! -f /tmp/dream-memories.json ]]" \
        "no temp work artifacts left in /tmp"

    # Link graph: REM phase should leave no broken links and fewer than the 4 seeded orphans.
    chk "python3 '$SCRIPT_DIR/backlinks.py' '$MEMORY_DIR' | python3 -c 'import json,sys; s=json.load(sys.stdin)[\"summary\"]; sys.exit(0 if s[\"broken\"]==0 and s[\"orphans\"]<4 else 1)'" \
        "link graph healthy after dream (broken=0, orphans reduced)"

    # Every index pointer resolves to a real file.
    chk "python3 - '$idx' '$MEMORY_DIR' <<'PY'
import re,sys,os
idx,mem=sys.argv[1],sys.argv[2]
links=re.findall(r'\]\(([^)]+\.md)\)', open(idx).read())
missing=[l for l in links if not os.path.exists(os.path.join(mem, os.path.basename(l)))]
sys.exit(1 if missing else 0)
PY" "every MEMORY.md pointer resolves to a real file"

    echo ""
    echo "=============================="
    echo -e "  Results: ${passed}/${total} checks passed"
    echo "=============================="
    if [[ $passed -eq $total ]]; then
        echo -e "${GREEN}All checks passed.${NC}"
    elif [[ $passed -ge $((total * 3 / 4)) ]]; then
        echo -e "${YELLOW}Most checks passed. Review failures above.${NC}"
    else
        echo -e "${RED}Several checks failed.${NC}"
    fi
    echo ""
}

do_teardown() {
    if [[ ! -d "$BASE_DIR" ]]; then warn "Nothing to clean up."; exit 0; fi
    info "Removing $BASE_DIR"
    rm -rf "$BASE_DIR"
    pass "Test environment removed."
}

case "${1:-}" in
    selftest) do_selftest ;;
    setup)    do_setup ;;
    verify)   do_verify ;;
    teardown) do_teardown ;;
    *)
        echo "Usage: $0 {selftest|setup|verify|teardown}"
        echo "  selftest  - deterministic retention.py unit test (no Claude run needed)"
        echo "  setup     - create one-fact-per-file fixtures with known issues"
        echo "  verify    - check whether a /dream run fixed them"
        echo "  teardown  - remove fixtures"
        exit 1
        ;;
esac
