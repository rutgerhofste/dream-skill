#!/usr/bin/env bash
#
# dream-hook.sh - Stop hook that checks dream conditions and triggers consolidation
#
# Add to settings.json:
#   "hooks": {
#     "Stop": [{
#       "type": "command",
#       "command": "bash ~/.claude/skills/dream/dream-hook.sh"
#     }]
#   }
#
# Fires when a Claude Code session ends. Checks if 24hrs have passed since
# the last dream. If so, spawns claude in the background to run /dream.
# Zero overhead when conditions aren't met (~10ms check).
#
# Headless safety: an unattended run has no human to approve lossy changes,
# so the skill (Phase 5) applies only non-destructive changes and queues any
# lossy proposals (deletes, merges, trims) to <memory_dir>/.dream-pending-review.md
# for the next interactive session. Nothing is hard-deleted unattended.

SKILL_DIR="$HOME/.claude/skills/dream"

# Run the condition check
if bash "$SKILL_DIR/should-dream.sh" 2>/dev/null; then
    # Conditions met - spawn dream in background
    # Use claude -p to run the dream skill non-interactively
    LOG="/tmp/dream-$(date +%Y%m%d-%H%M%S).log"
    nohup claude -p "Run the dream memory consolidation skill. Read ~/.claude/skills/dream/SKILL.md and execute all five sleep phases for each project's memory store. This is an unattended run: apply only non-destructive changes and queue any lossy proposals to .dream-pending-review.md for review. Do not hard-delete anything." \
        --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
        > "$LOG" 2>&1 &

    echo "Dream consolidation started in background (PID: $!)"
fi

# Always exit 0 so we don't block the session from closing
exit 0
