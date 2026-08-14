#!/usr/bin/env bash
set -euo pipefail

# Benchmarks the 'llama-cpp' container (see docker-compose.yml, profile
# "llama-cpp") via its own HTTP API: cold-start (container restart -> server
# ready) time, time-to-first-token (timings.prompt_ms), generation
# throughput (timings.predicted_per_second), and VRAM usage. After the cold
# start, sends a 3-turn conversation (growing message history, each turn
# built on the last) rather than repeating one prompt -- that's what a real
# chat session looks like, prompt-cache reuse on later turns included. See
# scripts/bench-ollama.sh for the Ollama-side equivalent -- same
# conversation, same methodology, for a like-for-like comparison.

LLAMA_CPP_URL="http://127.0.0.1:8080"
MAX_TOKENS=256
COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="${COMPOSE_DIR}/models"
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

echo "=== Cold start: restarting llama-cpp and timing container-start -> server-ready ==="
( cd "$COMPOSE_DIR" && docker compose stop llama-cpp ) >/dev/null 2>&1 || true
evict_page_cache "$Q4_GGUF"
cold_start_begin=$(date +%s.%N)
( cd "$COMPOSE_DIR" && docker compose --profile llama-cpp up -d llama-cpp ) >/dev/null

ready=0
for _ in $(seq 1 300); do
  if curl -sf -m 3 "${LLAMA_CPP_URL}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
cold_start_end=$(date +%s.%N)

if [[ "$ready" -ne 1 ]]; then
  echo "llama-cpp didn't become healthy within 300s. Check 'docker compose logs llama-cpp'." >&2
  exit 1
fi

cold_start_s=$(jq -n --argjson a "$cold_start_begin" --argjson b "$cold_start_end" '($b - $a) | (. * 100 | round) / 100')
echo "Cold start (container restart -> server ready): ${cold_start_s}s"

echo ""
echo "=== Benchmarking llama-cpp (${#TURNS[@]}-turn conversation, ${MAX_TOKENS} tokens/turn) ==="

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

  payload=$(jq -n --argjson messages "$messages" --argjson max_tokens "$MAX_TOKENS" \
    '{messages: $messages, temperature: 1.0, top_p: 0.95, top_k: 64, max_tokens: $max_tokens, stream: false}')

  response_file="${RESULTS_DIR}/llama-cpp-turn-${turn_num}.json"
  if ! response=$(curl -sf -m 300 "${LLAMA_CPP_URL}/v1/chat/completions" \
    -H "Content-Type: application/json" -d "$payload"); then
    kill "$poller_pid" 2>/dev/null || true
    echo "Request failed on turn ${turn_num}." >&2
    exit 1
  fi

  kill "$poller_pid" 2>/dev/null || true
  trap - EXIT
  echo "$response" | jq '.' > "$response_file"

  peak_vram=$(sort -n "$samples_file" | tail -n1)
  rm -f "$samples_file"

  reply=$(echo "$response" | jq -r '.choices[0].message.content')
  messages=$(echo "$messages" | jq --arg content "$reply" '. + [{role: "assistant", content: $content}]')

  cache_n=$(echo "$response" | jq -r '.timings.cache_n')
  prompt_ms=$(echo "$response" | jq -r '.timings.prompt_ms')
  prompt_n=$(echo "$response" | jq -r '.timings.prompt_n')
  predicted_ms=$(echo "$response" | jq -r '.timings.predicted_ms')
  predicted_n=$(echo "$response" | jq -r '.timings.predicted_n')
  predicted_per_second=$(echo "$response" | jq -r '.timings.predicted_per_second')

  echo "Time to first token (prompt eval): ${prompt_ms} ms (${prompt_n} new prompt tokens, ${cache_n} reused from cache)"
  echo "Generation:                        ${predicted_ms} ms for ${predicted_n} tokens (${predicted_per_second} tok/s)"
  echo "VRAM idle-loaded / peak-during-turn: ${baseline_vram} MiB / ${peak_vram} MiB"
  echo "Full response saved to: ${response_file}"
done