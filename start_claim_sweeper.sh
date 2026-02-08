#!/bin/bash
#
# Start Claim Sweeper in dry-run mode (safe for testing)
#

cd /root/bookpoly || exit 1
source venv/bin/activate

# Default: DRY RUN mode (doesn't execute real claims)
export CLAIM_ENABLED=true
export CLAIM_DRY_RUN=${CLAIM_DRY_RUN:-true}

# Timing
export CLAIM_POLL_SECONDS=120
export CLAIM_JITTER_SECONDS=10
export CLAIM_MAX_PER_CYCLE=5

# Workaround price
export CLAIM_SELL_PRICE=0.99

echo "=========================================="
echo " Claim Sweeper"
echo "=========================================="
echo "  Mode: $([ \"$CLAIM_DRY_RUN\" = \"true\" ] && echo 'DRY RUN' || echo 'LIVE')"
echo "  Poll: ${CLAIM_POLL_SECONDS}s + jitter"
echo "  Max per cycle: $CLAIM_MAX_PER_CYCLE"
echo ""

if [ "$CLAIM_DRY_RUN" = "true" ]; then
    echo "  DRY RUN mode: No real claims will be executed"
    echo "  To run LIVE: CLAIM_DRY_RUN=false ./start_claim_sweeper.sh"
fi
echo ""

# Execute
exec python -m claims.loop
