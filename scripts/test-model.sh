#!/usr/bin/env bash
set -euo pipefail

OLLAMA_URL="http://localhost:11434"
MODEL="${1:-}"

MEM0_TEST_PORT=18001
MEM0_TEST_URL="http://localhost:${MEM0_TEST_PORT}"
QDRANT_TEST_CONTAINER="qdrant-test"
MEM0_TEST_CONTAINER="mem0-test"
MEM0_TEST_IMAGE="mem0-service:test"
# Resolved after the precondition checks below -- Compose namespaces the
# "roleplay-net" network with the project name (e.g.
# "local-roleplay-agent_roleplay-net"), so the literal name can't be
# hardcoded here.
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

if [[ -z "$MODEL" ]]; then
  echo "Usage: $0 <ollama-model-name>" >&2
  echo "The model must already be imported into the running 'ollama' container" >&2
  echo "(ollama create/pull) before running this script." >&2
  exit 1
fi

for cmd in docker curl jq nvidia-smi; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if ! docker ps --format '{{.Names}}' | grep -qx ollama; then
  echo "The 'ollama' container isn't running. Start it first (e.g. 'make dev-up')." >&2
  exit 1
fi

NETWORK=$(docker network ls --format '{{.Name}}' | grep -E '(^|_)roleplay-net$' | head -n1)
if [[ -z "$NETWORK" ]]; then
  echo "Couldn't find the roleplay-net Docker network. Is the dev stack running ('make dev-up')?" >&2
  exit 1
fi

if ! curl -sf "${OLLAMA_URL}/api/show" -d "{\"model\": \"${MODEL}\"}" >/dev/null; then
  echo "Model '${MODEL}' isn't available in Ollama. Import it first (ollama create/pull)." >&2
  exit 1
fi

echo "=== Testing model: ${MODEL} ==="

gpu_mem_used() {
  nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -n1
}

unload_all_models() {
  local loaded
  loaded=$(curl -sf "${OLLAMA_URL}/api/ps" | jq -r '.models[]?.model')
  while IFS= read -r m; do
    [[ -z "$m" ]] && continue
    curl -sf "${OLLAMA_URL}/api/generate" -d "$(jq -n --arg model "$m" '{model: $model, keep_alive: 0}')" >/dev/null || true
  done <<< "$loaded"
  sleep 2
}

check_vram_footprint() {
  echo ""
  echo "--- Check 2: VRAM footprint ---"
  info "Unloading all currently loaded models for a clean measurement (they'll reload on next use)..."
  unload_all_models

  local baseline
  baseline=$(gpu_mem_used)

  curl -sf -m 120 "${OLLAMA_URL}/api/generate" -d "$(jq -n --arg model "$MODEL" '{model: $model, prompt: "hi", stream: false}')" >/dev/null

  local after actual_delta
  after=$(gpu_mem_used)
  actual_delta=$(( after - baseline ))

  local estimate_bytes estimate_mib
  estimate_bytes=$(curl -sf "${OLLAMA_URL}/api/ps" | jq -r --arg model "$MODEL" \
    '.models[] | select(.model == $model or .model == ($model + ":latest")) | .size_vram // 0')
  estimate_mib=$(( ${estimate_bytes:-0} / 1024 / 1024 ))

  echo "Actual GPU memory delta:     ${actual_delta} MiB"
  echo "Ollama's reported size_vram: ${estimate_mib} MiB"
}

cleanup_test_containers() {
  docker rm -f "$QDRANT_TEST_CONTAINER" "$MEM0_TEST_CONTAINER" >/dev/null 2>&1 || true
}

start_test_containers() {
  cleanup_test_containers

  docker run -d --name "$QDRANT_TEST_CONTAINER" --network "$NETWORK" qdrant/qdrant:latest >/dev/null

  docker build -t "$MEM0_TEST_IMAGE" ./mem0-service >/dev/null

  docker run -d --name "$MEM0_TEST_CONTAINER" --network "$NETWORK" \
    -e "MEM0_LLM_MODEL=${MODEL}" \
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
  payload=$(jq -n --arg model "$MODEL" --arg prompt "$prompt" '{model: $model, prompt: $prompt, stream: false}')

  local response
  if ! response=$(curl -sf -m 120 "${OLLAMA_URL}/api/generate" -d "$payload"); then
    fail "text generation: request failed"
    return
  fi

  local text duration_ms
  text=$(echo "$response" | jq -r '.response // ""')
  duration_ms=$(( $(echo "$response" | jq -r '.total_duration // 0') / 1000000 ))

  if [[ -z "$text" ]]; then
    fail "text generation: empty response"
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