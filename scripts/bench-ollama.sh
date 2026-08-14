#!/usr/bin/env bash
set -euo pipefail

# Benchmarks the running 'ollama' container via its own HTTP API, using the
# same 3-turn conversation and sampling settings as scripts/bench-llama-cpp.sh
# for a like-for-like comparison: cold-start (model load) time,
# time-to-first-token (prompt_eval_duration), generation throughput
# (eval_count / eval_duration), and VRAM usage.
#
# The model is explicitly unloaded before Turn 1 so it's forced to cold-load
# from disk (unlike llama-cpp, which loads once at container start) --
# Ollama reports that load cost directly as `load_duration` in the Turn 1
# response, no separate timed step needed.

OLLAMA_URL="http://127.0.0.1:11434"
MODEL="gemma4-e4b-hauhaucs"
MAX_TOKENS=256
MODELS_DIR="$(cd "$(dirname "$0")/.." && pwd)/models"
Q4_GGUF="${MODELS_DIR}/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf"

TURNS=(
  'You are a mysterious tavern keeper in a fantasy roleplay. A traveler just walked in, soaked from the rain, asking for a room and a hot meal. Describe the tavern, greet them in character, and offer them a choice of rooms.'
  "The traveler thanks you and picks the room by the fireplace. They ask if there's any news or rumors going around town lately."
  'The traveler seems uneasy after hearing that and asks if you know a safe route to the old forest road, since they need to head that way at first light.'
)

RESULTS_DIR="$(dirname "$0")/bench-results"
mkdir -p "$RESULTS_DIR"

for cmd in curl jq nvidia-smi; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if ! docker ps --format '{{.Names}}' | grep -qx ollama; then
  echo "The 'ollama' container isn't running. Start it first (e.g. 'make dev-up')." >&2
  exit 1
fi

if ! curl -sf "${OLLAMA_URL}/api/show" -d "{\"model\": \"${MODEL}\"}" >/dev/null; then
  echo "Model '${MODEL}' isn't available in Ollama. Import it first (ollama create/pull)." >&2
  exit 1
fi

gpu_mem_used() {
  nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -n1
}

# Drops this file's pages from the host OS page cache (posix_fadvise
# DONTNEED, no root needed) so a "fresh load" right after is a genuine cold
# read from disk, not a fast hit off whatever the OS cached from an earlier
# load earlier in this script or a previous run -- otherwise "switch" and
# "fresh load" timings quietly measure page-cache speed instead of the real
# first-ever-load cost.
evict_page_cache() {
  /usr/bin/python3 -c "
import os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
os.close(fd)
" "$1"
}

echo "=== Cold start: unloading ${MODEL} so Turn 1 loads it fresh from disk ==="
curl -sf "${OLLAMA_URL}/api/ps" | jq -r '.models[]?.model' | while IFS= read -r m; do
  [[ -z "$m" ]] && continue
  curl -sf "${OLLAMA_URL}/api/generate" -d "$(jq -n --arg model "$m" '{model: $model, keep_alive: 0}')" >/dev/null || true
done
sleep 2
evict_page_cache "$Q4_GGUF"

echo ""
echo "=== Benchmarking Ollama / ${MODEL} (${#TURNS[@]}-turn conversation, ${MAX_TOKENS} tokens/turn) ==="

messages='[]'

for i in "${!TURNS[@]}"; do
  turn_num=$((i + 1))
  echo ""
  echo "--- Turn ${turn_num}/${#TURNS[@]} ---"

  messages=$(echo "$messages" | jq --arg content "${TURNS[$i]}" '. + [{role: "user", content: $content}]')

  baseline_vram=$(gpu_mem_used)

  samples_file=$(mktemp)
  (
    while true; do
      gpu_mem_used >> "$samples_file"
      sleep 0.5
    done
  ) &
  poller_pid=$!
  # shellcheck disable=SC2064
  trap "kill ${poller_pid} 2>/dev/null || true" EXIT

  payload=$(jq -n --arg model "$MODEL" --argjson messages "$messages" --argjson num_predict "$MAX_TOKENS" \
    '{model: $model, messages: $messages, stream: false, options: {temperature: 1.0, top_p: 0.95, top_k: 64, num_predict: $num_predict}}')

  response_file="${RESULTS_DIR}/ollama-turn-${turn_num}.json"
  if ! response=$(curl -sf -m 300 "${OLLAMA_URL}/api/chat" -d "$payload"); then
    kill "$poller_pid" 2>/dev/null || true
    echo "Request failed on turn ${turn_num}." >&2
    exit 1
  fi

  kill "$poller_pid" 2>/dev/null || true
  trap - EXIT
  echo "$response" | jq '.' > "$response_file"

  peak_vram=$(sort -n "$samples_file" | tail -n1)
  rm -f "$samples_file"

  reply=$(echo "$response" | jq -r '.message.content')
  messages=$(echo "$messages" | jq --arg content "$reply" '. + [{role: "assistant", content: $content}]')

  load_ms=$(( $(echo "$response" | jq -r '.load_duration // 0') / 1000000 ))
  prompt_eval_count=$(echo "$response" | jq -r '.prompt_eval_count // 0')
  prompt_eval_ms=$(( $(echo "$response" | jq -r '.prompt_eval_duration // 0') / 1000000 ))
  eval_count=$(echo "$response" | jq -r '.eval_count // 0')
  eval_ms=$(( $(echo "$response" | jq -r '.eval_duration // 0') / 1000000 ))
  tok_per_sec=$(jq -n --argjson count "$eval_count" --argjson ms "$eval_ms" \
    '(if $ms > 0 then ($count / ($ms / 1000)) else 0 end) | round')

  if [[ "$turn_num" -eq 1 && "$load_ms" -gt 0 ]]; then
    echo "Cold start (model load, from Ollama's own load_duration): ${load_ms} ms"
  fi
  echo "Time to first token (prompt eval): ${prompt_eval_ms} ms (${prompt_eval_count} prompt tokens)"
  echo "Generation:                        ${eval_ms} ms for ${eval_count} tokens (${tok_per_sec} tok/s)"
  echo "VRAM idle-loaded / peak-during-turn: ${baseline_vram} MiB / ${peak_vram} MiB"
  echo "Full response saved to: ${response_file}"
done
