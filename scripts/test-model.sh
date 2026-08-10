#!/usr/bin/env bash
set -euo pipefail

OLLAMA_URL="http://localhost:11434"
MODEL="${1:-}"

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

if ! curl -sf "${OLLAMA_URL}/api/show" -d "{\"model\": \"${MODEL}\"}" >/dev/null; then
  echo "Model '${MODEL}' isn't available in Ollama. Import it first (ollama create/pull)." >&2
  exit 1
fi

echo "=== Testing model: ${MODEL} ==="

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

check_text_generation