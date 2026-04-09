#!/usr/bin/env bash
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-8000}"
GAMES="${GAMES:-10}"
RESULTS_FILE="${RESULTS_FILE:-artifacts/reports/human_ab_results_$(date +%Y%m%d_%H%M%S).txt}"

CHECKPOINTS=(
  "gen60 artifacts/checkpoints/generation_060.pkl"
  "gen73 artifacts/checkpoints/generation_073.pkl"
)

mkdir -p "$(dirname "$RESULTS_FILE")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv/bin/python in $SCRIPT_DIR" >&2
  exit 1
fi

echo "Human-vs-Bot A/B test" | tee "$RESULTS_FILE"
echo "Games per bot: $GAMES" | tee -a "$RESULTS_FILE"
echo "Fill template after each run: Wins(__) Losses(__) Ties(__) Notes(__)" | tee -a "$RESULTS_FILE"
echo "" | tee -a "$RESULTS_FILE"

for entry in "${CHECKPOINTS[@]}"; do
  label="${entry%% *}"
  ckpt="${entry#* }"

  if [[ ! -f "$ckpt" ]]; then
    echo "Checkpoint not found for $label: $ckpt" | tee -a "$RESULTS_FILE"
    continue
  fi

  echo "========================================"
  echo "Testing $label -> $ckpt"
  echo "Open: http://localhost:$PORT"
  echo "Mode: Play Against Computer"
  echo "Play $GAMES games, then press Ctrl+C here."

  WHISK_BOT_CHECKPOINT="$ckpt" \
  WHISK_BOT_EPSILON=0 \
  WHISK_BOT_TOP_K=1 \
  WHISK_BOT_TEMPERATURE=0.01 \
  WHISK_BOT_DELAY_MIN_SEC=0.1 \
  WHISK_BOT_DELAY_MAX_SEC=0.1 \
  .venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port "$PORT" || true

  read -r -p "$label wins: " w
  read -r -p "$label losses: " l
  read -r -p "$label ties: " t
  read -r -p "$label notes: " n
  printf "%s | W:%s L:%s T:%s | %s\n" "$label" "$w" "$l" "$t" "$n" | tee -a "$RESULTS_FILE"
done

echo ""
echo "Saved: $RESULTS_FILE"
