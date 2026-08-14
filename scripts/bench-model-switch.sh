#!/usr/bin/env bash
set -euo pipefail

# Benchmarks model-switch speed and VRAM footprint for Q4 <-> Q8, on both
# backends: Ollama (switches by requesting a different tag -- it evicts
# whatever's loaded to make room automatically, no restart needed) and
# llama.cpp (via its router mode, --models-dir -- loads/evicts models on
# demand per request, same mechanism, no container restart needed either;
# see docs/LLAMA_CPP_BENCHMARK.md for how that was confirmed to work).
# Runs Ollama first, then llama.cpp. Separate from bench-llama-cpp.sh /
# bench-ollama.sh, which benchmark steady-state generation speed instead.
#
# The 6 GB card can't hold both Q4 (5.4 GB) and Q8 (8.1 GB) at once, so
# every switch really does evict one model and load the other from disk --
# each backend is stopped/freed before the other one starts.
#
# Fresh-load timing is only as good as making sure nothing is served from
# page cache left over from an earlier read. For llama.cpp this is enforced
# directly: the GGUFs under models/ are owned by the current user, so each
# load evicts that specific file's pages first (posix_fadvise DONTNEED, no
# root needed). Ollama can't be evicted the same way -- `ollama create`
# copies the GGUF into its own blob store inside the ollama_models Docker
# volume, which is root-owned, so the numbers for Ollama may be understated
# by a warm page cache left over from an earlier read in this run or before.
# This is a known, unresolved limitation (see docs/LLAMA_CPP_BENCHMARK.md).

OLLAMA_URL="http://127.0.0.1:11434"
LLAMA_CPP_URL="http://127.0.0.1:8091"
COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MODELS_DIR="${COMPOSE_DIR}/models"

RESULTS_DIR="$(dirname "$0")/bench-results"
mkdir -p "$RESULTS_DIR"

for cmd in curl jq nvidia-smi docker; do
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
# read from disk, not a fast hit off whatever the OS cached earlier.
evict_page_cache() {
  /usr/bin/python3 -c "
import os, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
os.close(fd)
" "$1"
}

SWITCH_IDS=(Q4 Q8 Q4)
GGUF_FILES=(
  "${MODELS_DIR}/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf"
  "${MODELS_DIR}/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf"
  "${MODELS_DIR}/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf"
)

##############################################################################
# Ollama
##############################################################################

echo "=== Ollama: Q4 <-> Q8 switch ==="

( cd "$COMPOSE_DIR" && docker compose stop llama-cpp ) >/dev/null 2>&1 || true
( cd "$COMPOSE_DIR" && docker compose up -d ollama ) >/dev/null

ready=0
for _ in $(seq 1 60); do
  if curl -sf -m 3 "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "ollama didn't become ready within 60s. Check 'docker compose logs ollama'." >&2
  exit 1
fi

OLLAMA_TAGS=(gemma4-e4b-hauhaucs gemma4-e4b-hauhaucs-q8 gemma4-e4b-hauhaucs)

curl -sf "${OLLAMA_URL}/api/ps" | jq -r '.models[]?.model' | while IFS= read -r m; do
  [[ -z "$m" ]] && continue
  curl -sf "${OLLAMA_URL}/api/generate" -d "$(jq -n --arg model "$m" '{model: $model, keep_alive: 0}')" >/dev/null || true
done
sleep 2

ollama_results='[]'

for i in "${!SWITCH_IDS[@]}"; do
  id="${SWITCH_IDS[$i]}"
  tag="${OLLAMA_TAGS[$i]}"
  event="fresh load"
  [[ "$i" -gt 0 ]] && event="switch from ${SWITCH_IDS[$((i-1))]}"

  echo ""
  echo "--- ${id} (${tag}, ${event}) ---"

  evict_page_cache "${GGUF_FILES[$i]}"

  baseline_vram=$(gpu_mem_used)
  payload=$(jq -n --arg model "$tag" '{model: $model, prompt: "hi", stream: false, options: {num_predict: 8}}')

  switch_begin=$(date +%s.%N)
  response=$(curl -sf -m 300 "${OLLAMA_URL}/api/generate" -d "$payload")
  switch_end=$(date +%s.%N)

  after_request_vram=$(gpu_mem_used)
  wall_s=$(jq -n --argjson a "$switch_begin" --argjson b "$switch_end" '($b - $a) | (. * 100 | round) / 100')
  load_ms=$(( $(echo "$response" | jq -r '.load_duration // 0') / 1000000 ))

  echo "Wall time (${event}):      ${wall_s}s"
  echo "Ollama-reported load time: ${load_ms} ms"
  echo "VRAM baseline (before):    ${baseline_vram} MiB"
  echo "VRAM after request:        ${after_request_vram} MiB"

  ollama_results=$(jq -n --argjson prev "$ollama_results" --arg id "$id" --arg tag "$tag" --arg event "$event" \
    --argjson wall_s "$wall_s" --argjson load_ms "$load_ms" \
    --argjson baseline "$baseline_vram" --argjson after_request "$after_request_vram" \
    '$prev + [{model: $id, tag: $tag, event: $event, wall_time_seconds: $wall_s, ollama_load_ms: $load_ms, vram_baseline_mib: $baseline, vram_after_request_mib: $after_request}]')
done

ollama_output_file="${RESULTS_DIR}/ollama-switch.json"
echo "$ollama_results" | jq '.' > "$ollama_output_file"
echo ""
echo "Ollama switch results saved to: ${ollama_output_file}"

##############################################################################
# llama.cpp (router mode)
##############################################################################

echo ""
echo "=== llama.cpp: Q4 <-> Q8 switch (router mode) ==="

( cd "$COMPOSE_DIR" && docker compose stop ollama llama-cpp ) >/dev/null 2>&1 || true

ROUTER_CONTAINER="llama-cpp-router-bench"
cleanup_router() {
  docker rm -f "$ROUTER_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup_router EXIT
cleanup_router

docker run --rm -d --name "$ROUTER_CONTAINER" --gpus all \
  -v "${MODELS_DIR}:/models:ro" \
  -p "127.0.0.1:8091:8080" \
  ghcr.io/ggml-org/llama.cpp:server-cuda13 \
  --models-dir /models --models-max 1 \
  --jinja -c 16384 -ngl 99 \
  --host 0.0.0.0 --port 8080 \
  --temp 1.0 --top-p 0.95 --top-k 64 \
  --cache-type-k q8_0 --cache-type-v q8_0 >/dev/null

ready=0
for _ in $(seq 1 60); do
  if curl -sf -m 3 "${LLAMA_CPP_URL}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "Router server didn't become healthy within 60s. Check 'docker logs ${ROUTER_CONTAINER}'." >&2
  exit 1
fi

LLAMA_CPP_MODEL_NAMES=(
  "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P"
  "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P"
  "Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P"
)

llama_cpp_results='[]'

for i in "${!SWITCH_IDS[@]}"; do
  id="${SWITCH_IDS[$i]}"
  event="fresh load"
  [[ "$i" -gt 0 ]] && event="switch from ${SWITCH_IDS[$((i-1))]}"

  echo ""
  echo "--- ${id} (${event}) ---"

  evict_page_cache "${GGUF_FILES[$i]}"

  baseline_vram=$(gpu_mem_used)
  payload=$(jq -n --arg model "${LLAMA_CPP_MODEL_NAMES[$i]}" \
    '{model: $model, messages: [{role: "user", content: "hi"}], max_tokens: 8, stream: false}')

  switch_begin=$(date +%s.%N)
  response=$(curl -sf -m 300 "${LLAMA_CPP_URL}/v1/chat/completions" -H "Content-Type: application/json" -d "$payload")
  switch_end=$(date +%s.%N)

  after_request_vram=$(gpu_mem_used)
  switch_s=$(jq -n --argjson a "$switch_begin" --argjson b "$switch_end" '($b - $a) | (. * 100 | round) / 100')

  echo "Time (${event}):          ${switch_s}s"
  echo "VRAM baseline (before):    ${baseline_vram} MiB"
  echo "VRAM after request:        ${after_request_vram} MiB"

  llama_cpp_results=$(jq -n --argjson prev "$llama_cpp_results" --arg id "$id" --arg event "$event" \
    --argjson time_s "$switch_s" --argjson baseline "$baseline_vram" --argjson after_request "$after_request_vram" \
    '$prev + [{model: $id, event: $event, time_seconds: $time_s, vram_baseline_mib: $baseline, vram_after_request_mib: $after_request}]')
done

cleanup_router
trap - EXIT

llama_cpp_output_file="${RESULTS_DIR}/llama-cpp-switch.json"
echo "$llama_cpp_results" | jq '.' > "$llama_cpp_output_file"
echo ""
echo "llama.cpp switch results saved to: ${llama_cpp_output_file}"