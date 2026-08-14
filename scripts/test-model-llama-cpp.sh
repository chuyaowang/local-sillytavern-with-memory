#!/usr/bin/env bash
set -euo pipefail

# Adapted from scripts/test-model.sh -- same checks (text generation, VRAM
# footprint, mem0 memory extraction), but against the 'llama-cpp' router-mode
# service (docker-compose.yml, llama-cpp/models-preset.ini) instead of
# Ollama, for both the LLM and the embedder -- an all-llama.cpp pipeline.
# mem0-service/main.py's MEM0_LLM_PROVIDER=openai / MEM0_EMBEDDER_PROVIDER=openai
# point it at llama.cpp's OpenAI-compatible API instead of Ollama's own.
#
# The embedder isn't spun up separately here -- the real llama-cpp service
# already serves it (nomic-embed-text-v1.5) alongside whichever chat model
# is requested, via its preset. See docs/LLAMA_CPP_BENCHMARK.md.
#
# Usage: ./scripts/test-model-llama-cpp.sh [gemma-q4|gemma-q8]
# Model id must match a section in llama-cpp/models-preset.ini.

LLAMA_CPP_URL="http://localhost:8080"
MODEL="${1:-gemma-q4}"
COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

case "$MODEL" in
  gemma-q4) GGUF="${COMPOSE_DIR}/models/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf" ;;
  gemma-q8) GGUF="${COMPOSE_DIR}/models/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf" ;;
  *)
    echo "Usage: $0 [gemma-q4|gemma-q8]" >&2
    echo "Model id must match a section in llama-cpp/models-preset.ini." >&2
    exit 1
    ;;
esac

MEM0_TEST_PORT=18002
MEM0_TEST_URL="http://localhost:${MEM0_TEST_PORT}"
QDRANT_TEST_CONTAINER="qdrant-test"
MEM0_TEST_CONTAINER="mem0-test"
MEM0_TEST_IMAGE="mem0-service:test"
NETWORK=""

SAMPLE_MESSAGES='[
  {"role": "user", "content": "Hi, I'\''m Alex. My favorite color is blue and I work as a high school teacher."},
  {"role": "assistant", "content": "Nice to meet you, Alex! Teaching sounds rewarding."},
  {"role": "user", "content": "Thanks! I also have a dog named Biscuit."}
]'

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
NC=$'\033[0m'

FAILED=0

pass() { echo "${GREEN}PASS${NC} $1"; }
fail() { echo "${RED}FAIL${NC} $1"; FAILED=1; }
warn() { echo "${YELLOW}WARN${NC} $1"; }
info() { echo "$1"; }

for cmd in docker curl jq nvidia-smi; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if ! docker ps --format '{{.Names}}' | grep -qx llama-cpp; then
  echo "The 'llama-cpp' container isn't running." >&2
  echo "Start it: docker compose up -d llama-cpp" >&2
  exit 1
fi

if [[ ! -f "$GGUF" ]]; then
  echo "Missing ${GGUF}" >&2
  exit 1
fi

NETWORK=$(docker network ls --format '{{.Name}}' | grep -E '(^|_)roleplay-net$' | head -n1)
if [[ -z "$NETWORK" ]]; then
  echo "Couldn't find the roleplay-net Docker network. Is the dev stack running ('make dev-up')?" >&2
  exit 1
fi

if ! curl -sf -m 5 "${LLAMA_CPP_URL}/health" >/dev/null; then
  echo "llama-cpp isn't responding on ${LLAMA_CPP_URL}/health. Check 'docker compose logs llama-cpp'." >&2
  exit 1
fi

echo "=== Testing llama-cpp / ${MODEL} ==="

info "Warming up (first request for a not-yet-loaded model pays its load cost)..."
if ! curl -sf -m 300 "${LLAMA_CPP_URL}/v1/chat/completions" -H "Content-Type: application/json" \
  -d "$(jq -n --arg model "$MODEL" '{model: $model, messages: [{role: "user", content: "hi"}], max_tokens: 8, stream: false}')" >/dev/null; then
  echo "Couldn't get a response from llama-cpp even with a 5-minute warm-up window. Check 'docker compose logs llama-cpp' for details." >&2
  exit 1
fi

gpu_mem_used() {
  nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -n1
}

evict_page_cache() {
  /usr/bin/python3 -c "
import os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
os.close(fd)
" "$1"
}

check_vram_footprint() {
  echo ""
  echo "--- Check 2: VRAM footprint ---"
  # Router mode loads models on demand, not eagerly at container start -- a
  # container restart plus a fresh request gives a clean before/after
  # measurement without needing an "unload" API (which router mode does
  # have, via POST /models/unload, but a restart also clears the embedder
  # and any other model that happened to be loaded, for a truly clean read).
  info "Restarting llama-cpp for a clean before/after measurement..."

  ( cd "$COMPOSE_DIR" && docker compose stop llama-cpp ) >/dev/null 2>&1 || true
  evict_page_cache "$GGUF"
  local baseline
  baseline=$(gpu_mem_used)

  ( cd "$COMPOSE_DIR" && docker compose up -d llama-cpp ) >/dev/null

  local ready=0
  for _ in $(seq 1 60); do
    if curl -sf -m 3 "${LLAMA_CPP_URL}/health" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "$ready" -ne 1 ]]; then
    fail "VRAM footprint: llama-cpp didn't come back up within 60s"
    return
  fi

  curl -sf -m 300 "${LLAMA_CPP_URL}/v1/chat/completions" -H "Content-Type: application/json" \
    -d "$(jq -n --arg model "$MODEL" '{model: $model, messages: [{role: "user", content: "hi"}], max_tokens: 8, stream: false}')" >/dev/null

  local after actual_delta
  after=$(gpu_mem_used)
  actual_delta=$(( after - baseline ))

  echo "Actual GPU memory delta: ${actual_delta} MiB"
  echo "(llama.cpp doesn't self-report an estimate the way Ollama's api/ps does, so there's nothing to compare this against.)"
}

cleanup_test_containers() {
  docker rm -f "$QDRANT_TEST_CONTAINER" "$MEM0_TEST_CONTAINER" >/dev/null 2>&1 || true
}

start_test_containers() {
  cleanup_test_containers

  docker run -d --name "$QDRANT_TEST_CONTAINER" --network "$NETWORK" qdrant/qdrant:latest >/dev/null

  docker build -t "$MEM0_TEST_IMAGE" ./mem0-service >/dev/null 2>&1

  docker run -d --name "$MEM0_TEST_CONTAINER" --network "$NETWORK" \
    -e "MEM0_LLM_PROVIDER=openai" \
    -e "MEM0_LLM_BASE_URL=http://llama-cpp:8080/v1" \
    -e "MEM0_LLM_MODEL=${MODEL}" \
    -e "MEM0_EMBEDDER_PROVIDER=openai" \
    -e "MEM0_EMBEDDER_BASE_URL=http://llama-cpp:8080/v1" \
    -e "QDRANT_HOST=${QDRANT_TEST_CONTAINER}" \
    -e "QDRANT_COLLECTION=model_eval_scratch" \
    -e "MEM0_TELEMETRY=False" \
    -p "127.0.0.1:${MEM0_TEST_PORT}:8001" \
    "$MEM0_TEST_IMAGE" >/dev/null

  local waited=0
  until curl -sf "${MEM0_TEST_URL}/health" >/dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if (( waited >= 60 )); then
      fail "memory extraction: mem0-test container didn't become healthy within 60s"
      return 1
    fi
  done
}

check_extraction_json_syntax() {
  echo ""
  echo "--- Check 3a: extraction JSON syntax ---"
  local payload
  payload=$(jq -n --argjson messages "$SAMPLE_MESSAGES" '{messages: $messages}')

  local response
  if ! response=$(curl -sf -m 120 -X POST "${MEM0_TEST_URL}/debug/extraction-raw" -H "Content-Type: application/json" -d "$payload"); then
    fail "extraction JSON syntax: request failed"
    return
  fi

  local valid_json used_fallback memory_count
  valid_json=$(echo "$response" | jq -r '.valid_json')
  used_fallback=$(echo "$response" | jq -r '.used_fallback')
  memory_count=$(echo "$response" | jq -r '.memory_count // 0')

  if [[ "$valid_json" != "true" ]]; then
    fail "extraction JSON syntax: model produced invalid JSON"
    echo "Raw response (truncated):"
    echo "$response" | jq -r '.raw_response' | head -c 1000
    echo ""
    return
  fi

  if [[ "$used_fallback" == "true" ]]; then
    warn "extraction JSON syntax: valid only via mem0's regex-rescue fallback (${memory_count} facts)"
  else
    pass "extraction JSON syntax (${memory_count} facts, no fallback needed)"
  fi
  echo "Extraction JSON:"
  echo "$response" | jq '.cleaned_response | fromjson'
}

check_extraction_pipeline() {
  echo ""
  echo "--- Check 3b: end-to-end memory extraction ---"
  local payload
  payload=$(jq -n --argjson messages "$SAMPLE_MESSAGES" '{messages: $messages, user_id: "model-eval"}')

  local response
  if ! response=$(curl -sf -m 120 -X POST "${MEM0_TEST_URL}/memories" -H "Content-Type: application/json" -d "$payload"); then
    fail "memory extraction pipeline: request failed"
    return
  fi

  local count
  count=$(echo "$response" | jq -r '.results | length')

  if [[ "$count" -lt 1 ]]; then
    fail "memory extraction pipeline: no facts extracted"
    return
  fi

  pass "memory extraction pipeline (${count} facts)"
  echo "Extracted facts:"
  echo "$response" | jq -r '.results[].memory'
}

check_memory_extraction() {
  if ! start_test_containers; then
    return
  fi
  check_extraction_json_syntax
  check_extraction_pipeline
}

check_text_generation() {
  echo ""
  echo "--- Check 1: text generation ---"
  local prompt='You are a mysterious tavern keeper in a fantasy roleplay. A traveler just walked in asking for a room. Respond in character, 2-3 sentences.'
  local payload
  # This model emits a separate reasoning_content ("thinking") block before
  # its actual reply. Its length has real run-to-run variance even at the
  # project's real sampling settings (temp 1.0, top_p 0.95, top_k 64, baked
  # into llama-cpp/models-preset.ini, confirmed reaching the server via
  # GET /models) -- it burned the whole budget on reasoning and left
  # `content` empty at 512 tokens on one run of this exact check. 1536
  # gives enough headroom to make that rare rather than eliminating it
  # outright; worth knowing for real use, not just a test-script quirk.
  payload=$(jq -n --arg model "$MODEL" --arg prompt "$prompt" \
    '{model: $model, messages: [{role: "user", content: $prompt}], max_tokens: 1536, stream: false}')

  local response
  if ! response=$(curl -sf -m 120 "${LLAMA_CPP_URL}/v1/chat/completions" -H "Content-Type: application/json" -d "$payload"); then
    fail "text generation: request failed"
    return
  fi

  local text duration_ms
  text=$(echo "$response" | jq -r '.choices[0].message.content // ""')
  duration_ms=$(jq -n --argjson p "$(echo "$response" | jq -r '.timings.prompt_ms // 0')" \
    --argjson g "$(echo "$response" | jq -r '.timings.predicted_ms // 0')" '($p + $g) | round')

  if [[ -z "$text" ]]; then
    fail "text generation: empty response"
    local reasoning
    reasoning=$(echo "$response" | jq -r '.choices[0].message.reasoning_content // ""')
    if [[ -n "$reasoning" ]]; then
      warn "text generation: content was empty but reasoning_content wasn't -- the whole token budget went to reasoning, none left for the reply"
    fi
    return
  fi

  pass "text generation (${duration_ms}ms)"
  echo "Generated text:"
  echo "  $text"
}

trap cleanup_test_containers EXIT

check_text_generation
check_vram_footprint
check_memory_extraction

echo ""
echo "=== Summary ==="
if [[ "$FAILED" -eq 0 ]]; then
  echo "${GREEN}All checks passed.${NC}"
else
  echo "${RED}One or more checks failed.${NC}"
fi

exit "$FAILED"