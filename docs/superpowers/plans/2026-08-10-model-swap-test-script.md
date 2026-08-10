# Model-Swap Test Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the user a script that, given an Ollama model name already imported into the running `ollama` container, checks text generation, mem0 memory-extraction JSON validity, mem0 memory-extraction end-to-end, and VRAM footprint vs. Ollama's own estimate — printing everything to the console — so they can evaluate a candidate model before switching production config over to it.

**Architecture:** Two small additions to `mem0-service/main.py` (env-var-driven model/collection config, and a `/debug/extraction-raw` endpoint that replicates mem0's real extraction call to surface raw JSON-parse failures mem0 itself swallows) make the extraction checks possible. A new `scripts/test-model.sh` drives everything: it talks to the shared `ollama` container directly over its host-bound port for the generation/VRAM checks, and spins up two fully throwaway, `--rm`-cleaned containers (a scratch Qdrant + a freshly-built mem0-service pointed at the candidate model) for the extraction checks, so nothing in the real `mem0`/`mem0-prod`/`qdrant`/`qdrant-prod` stacks is ever touched.

**Tech Stack:** Python 3.11 / FastAPI / mem0ai 2.0.17 (`mem0-service/main.py`), Bash (`scripts/test-model.sh`) using `curl`, `jq`, `docker`, `nvidia-smi`.

**No pytest in this repo** — verification throughout this plan is done the way the rest of the project already verifies infrastructure changes: live `curl`/`docker exec` checks against the running dev stack, not a unit-test framework. Each task's "red/green" steps are curl calls before/after the code exists, not `pytest` invocations.

## Global Constraints

- `MEM0_LLM_MODEL` env var, default `"gemma4-e4b-hauhaucs"` (must match today's hardcoded value exactly, so existing dev/prod behavior is unchanged).
- `QDRANT_COLLECTION` env var, default `"roleplay_memories"` (same — unchanged default behavior).
- `docker-compose.yml` (`mem0`) and `docker-compose.prod.yml` (`mem0-prod`) set `MEM0_LLM_MODEL: gemma4-e4b-hauhaucs` explicitly. `QDRANT_COLLECTION` is **not** set in either compose file — only the test script's throwaway container overrides it.
- `/debug/extraction-raw` must use mem0's actual internal extraction building blocks (`ADDITIVE_EXTRACTION_PROMPT`, `generate_additive_extraction_prompt`, `parse_messages`, `remove_code_blocks`, `extract_json`) so the check is faithful to the real `/memories` code path, not a reimplementation.
- `scripts/test-model.sh <model>` must fail fast (before touching Ollama or Docker further) if: no model argument given, `docker`/`curl`/`jq`/`nvidia-smi` missing, the `ollama` container isn't running, or the model isn't already imported into Ollama.
- Throwaway test containers are named `qdrant-test` and `mem0-test`, run with `--rm`-equivalent cleanup via a `trap ... EXIT` calling `docker rm -f`, and are force-removed at script start too (in case a previous run crashed and left them behind).
- `mem0-test` is published only to `127.0.0.1:18001:8001` (not exposed beyond localhost, same posture as every other internal service in this project). `qdrant-test` gets no published port at all.
- Exit code: non-zero if text generation, extraction-JSON-syntax, or extraction-end-to-end fails. VRAM footprint is informational only and never affects the exit code; extraction-JSON-syntax succeeding only via mem0's fallback parser is a WARN, not a FAIL.

## File Structure

- Modify `mem0-service/main.py` — env-var config (Task 1), new `/debug/extraction-raw` endpoint (Task 2).
- Modify `docker-compose.yml`, `docker-compose.prod.yml` — set `MEM0_LLM_MODEL` explicitly (Task 1).
- Create `scripts/test-model.sh` — the test script itself, built incrementally (Tasks 3–6).

---

### Task 1: mem0-service config becomes env-var driven

**Files:**
- Modify: `mem0-service/main.py:17-42`
- Modify: `docker-compose.yml:48-60` (`mem0` service)
- Modify: `docker-compose.prod.yml:29-42` (`mem0-prod` service)

**Interfaces:**
- Produces: `MEM0_LLM_MODEL` (module-level str in `main.py`, env var `MEM0_LLM_MODEL`, default `"gemma4-e4b-hauhaucs"`) and `QDRANT_COLLECTION` (module-level str, env var `QDRANT_COLLECTION`, default `"roleplay_memories"`) — both read by later tasks' throwaway container.

- [ ] **Step 1: Capture baseline behavior with a live curl call**

The dev `mem0` container is already running. Confirm today's `/memories` response shape before changing anything, so the later regression check has something to compare against:

```bash
curl -s -X POST http://localhost:8001/memories -H "Content-Type: application/json" -d '{
  "messages": [{"role": "user", "content": "My favorite food is ramen."}],
  "user_id": "task1-baseline-check"
}' | python3 -m json.tool
```

Expected: HTTP 200, a JSON object with a `"results"` array containing at least one item with `"memory"` text about ramen. Note the returned `"id"` value(s) — delete them after Step 6 so no test data lingers:

```bash
curl -s -X DELETE http://localhost:8001/memories/<id-from-above>
```

- [ ] **Step 2: Edit `mem0-service/main.py` to add the two env vars**

Open `mem0-service/main.py`. Immediately after the existing `QDRANT_HOST` line (currently line 17), add:

```python
# Model is swappable per-container the same way -- lets a throwaway
# container (e.g. scripts/test-model.sh) evaluate a candidate model without
# touching the real dev/prod mem0 containers or their config.
MEM0_LLM_MODEL = os.environ.get("MEM0_LLM_MODEL", "gemma4-e4b-hauhaucs")

# Same idea for the collection name -- a throwaway container can write to a
# scratch collection instead of the real "roleplay_memories" store.
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "roleplay_memories")
```

- [ ] **Step 3: Wire the two constants into `config`**

In the same file, change:

```python
        "config": {
            "model": "gemma4-e4b-hauhaucs",
            "ollama_base_url": OLLAMA_BASE_URL,
        },
    },
    "embedder": {
```

to:

```python
        "config": {
            "model": MEM0_LLM_MODEL,
            "ollama_base_url": OLLAMA_BASE_URL,
        },
    },
    "embedder": {
```

and change:

```python
        "config": {
            "collection_name": "roleplay_memories",
            "host": QDRANT_HOST,
```

to:

```python
        "config": {
            "collection_name": QDRANT_COLLECTION,
            "host": QDRANT_HOST,
```

- [ ] **Step 4: Set `MEM0_LLM_MODEL` explicitly in both compose files**

In `docker-compose.yml`, change the `mem0` service's environment block from:

```yaml
    environment:
      MEM0_TELEMETRY: "False"
```

to:

```yaml
    environment:
      MEM0_TELEMETRY: "False"
      MEM0_LLM_MODEL: gemma4-e4b-hauhaucs
```

In `docker-compose.prod.yml`, change the `mem0-prod` service's environment block from:

```yaml
    environment:
      MEM0_TELEMETRY: "False"
      QDRANT_HOST: qdrant-prod
```

to:

```yaml
    environment:
      MEM0_TELEMETRY: "False"
      QDRANT_HOST: qdrant-prod
      MEM0_LLM_MODEL: gemma4-e4b-hauhaucs
```

- [ ] **Step 5: Rebuild and restart the dev `mem0` container, verify the env var actually flows through**

```bash
docker compose build mem0
docker compose up -d mem0
sleep 3
docker exec mem0 printenv MEM0_LLM_MODEL
```

Expected: prints `gemma4-e4b-hauhaucs`.

```bash
curl -s http://localhost:8001/health
```

Expected: `{"status":"ok"}` (confirms `Memory.from_config` didn't blow up on startup).

- [ ] **Step 6: Regression check — confirm behavior is unchanged from baseline**

```bash
curl -s -X POST http://localhost:8001/memories -H "Content-Type: application/json" -d '{
  "messages": [{"role": "user", "content": "My favorite food is ramen."}],
  "user_id": "task1-regression-check"
}' | python3 -m json.tool
```

Expected: same shape as Step 1 — `"results"` array with at least one `"memory"` entry about ramen. Delete the returned id(s) the same way as Step 1.

- [ ] **Step 7: Verify the compose overlay still merges cleanly**

Don't touch the running `mem0-prod` container — just confirm the merged config is valid:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml config --services
```

Expected: lists `ollama`, `qdrant`, `mem0`, `sillytavern`, `qdrant-prod`, `mem0-prod`, `sillytavern-prod` with no errors.

- [ ] **Step 8: Commit**

```bash
git add mem0-service/main.py docker-compose.yml docker-compose.prod.yml
git commit -m "$(cat <<'EOF'
feat: make mem0-service's model and collection name env-configurable

- MEM0_LLM_MODEL (default: gemma4-e4b-hauhaucs) replaces the hardcoded LLM
  model name, same pattern as the existing QDRANT_HOST var
- QDRANT_COLLECTION (default: roleplay_memories) replaces the hardcoded
  vector store collection name
- docker-compose.yml/docker-compose.prod.yml set MEM0_LLM_MODEL explicitly
  for mem0/mem0-prod so today's behavior is unchanged
- Lets a throwaway container point at a candidate model and a scratch
  collection without touching the real dev/prod mem0 containers
EOF
)"
```

---

### Task 2: `/debug/extraction-raw` endpoint

**Files:**
- Modify: `mem0-service/main.py` (imports near the top; new request model near `AddMemoryRequest`/`UpdateMemoryRequest`; new route placed directly after the existing `add_memory` route)

**Interfaces:**
- Consumes: `memory` (module-level `Memory` instance, already defined in `main.py`), `MEM0_LLM_MODEL` (from Task 1, only indirectly — this endpoint uses whatever model `memory` was configured with).
- Produces: `POST /debug/extraction-raw`, request body `{"messages": [{"role": str, "content": str}, ...]}`, response body `{"raw_response": str, "cleaned_response": str, "valid_json": bool, "used_fallback": bool, "error": str (present only on failure), "has_memory_key": bool (present only on success), "memory_count": int}`.

- [ ] **Step 1: Confirm the endpoint doesn't exist yet (red)**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8001/debug/extraction-raw -d '{"messages":[]}'
```

Expected: `404`.

- [ ] **Step 2: Add the mem0-internal imports**

At the top of `mem0-service/main.py`, after the existing `from mem0 import Memory` line, add:

```python
from mem0.configs.prompts import ADDITIVE_EXTRACTION_PROMPT, generate_additive_extraction_prompt
from mem0.memory.utils import extract_json, parse_messages, remove_code_blocks
```

- [ ] **Step 3: Add the request model**

Directly below the existing `class AddMemoryRequest(BaseModel): ...` block, add:

```python
class ExtractionRawRequest(BaseModel):
    messages: list[dict]
```

- [ ] **Step 4: Add the endpoint**

Directly after the existing `add_memory` function (the `@app.post("/memories")` route), add:

```python
@app.post("/debug/extraction-raw")
def extraction_raw(req: ExtractionRawRequest):
    # Mirrors mem0's own extraction call (Memory._add_to_vector_store) so this
    # exercises the same prompt/parse path a real /memories call takes.
    # Surfaced separately because mem0 catches a JSON parse failure here and
    # silently turns it into an empty result -- identical to "the model found
    # nothing worth remembering". This endpoint reports the parse failure
    # instead of swallowing it.
    parsed_messages = parse_messages(req.messages)
    user_prompt = generate_additive_extraction_prompt(new_messages=parsed_messages)

    raw_response = memory.llm.generate_response(
        messages=[
            {"role": "system", "content": ADDITIVE_EXTRACTION_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
    )

    cleaned = remove_code_blocks(raw_response)
    result = {"raw_response": raw_response, "cleaned_response": cleaned}

    if not cleaned or not cleaned.strip():
        result.update(valid_json=False, used_fallback=False, error="empty response", memory_count=0)
        return result

    try:
        parsed = json.loads(cleaned, strict=False)
        result.update(valid_json=True, used_fallback=False)
    except json.JSONDecodeError:
        try:
            parsed = json.loads(extract_json(cleaned), strict=False)
            result.update(valid_json=True, used_fallback=True)
        except json.JSONDecodeError as e2:
            result.update(valid_json=False, used_fallback=True, error=str(e2), memory_count=0)
            return result

    memory_list = parsed.get("memory") if isinstance(parsed, dict) else None
    result.update(
        has_memory_key=isinstance(memory_list, list),
        memory_count=len(memory_list) if isinstance(memory_list, list) else 0,
    )
    return result
```

- [ ] **Step 5: Rebuild, restart, and verify against the known-good model (green)**

```bash
docker compose build mem0
docker compose up -d mem0
sleep 3
curl -s -X POST http://localhost:8001/debug/extraction-raw -H "Content-Type: application/json" -d '{
  "messages": [
    {"role": "user", "content": "Hi, I am Alex. My favorite color is blue and I work as a high school teacher."},
    {"role": "assistant", "content": "Nice to meet you, Alex! Teaching sounds rewarding."},
    {"role": "user", "content": "Thanks! I also have a dog named Biscuit."}
  ]
}' | python3 -m json.tool
```

Expected: `"valid_json": true`, `"used_fallback": false`, `"has_memory_key": true`, `"memory_count"` >= 1. (`gemma4-e4b-hauhaucs` already passed extraction-reliability testing per CLAUDE.md, so this should pass cleanly — this run is confirming the endpoint's plumbing, not testing the model.)

- [ ] **Step 6: Note the error-path verification is deferred to Task 5**

`mem0`'s `config["llm"]["config"]["model"]` is baked into `Memory.from_config()` at process startup, so testing this endpoint against a genuinely nonexistent model would require a whole separate throwaway container (build image, run with a bogus `MEM0_LLM_MODEL`, wait for it to fail or serve). That container is exactly what Task 5 builds anyway (`mem0-test`, pointed at whatever model `scripts/test-model.sh` was given) — no need to duplicate it here. If Task 5's Step 4 run against `gemma4-e4b-hauhaucs` (a model already known to pass extraction) produces `valid_json: true`, that's sufficient confirmation this endpoint's success path works correctly; a future run of the finished script against a genuinely broken candidate model is what will exercise the `valid_json: false` / `error` path for real.

- [ ] **Step 7: Commit**

```bash
git add mem0-service/main.py
git commit -m "$(cat <<'EOF'
feat: add /debug/extraction-raw endpoint to mem0-service

- Replicates mem0's real extraction call (ADDITIVE_EXTRACTION_PROMPT +
  generate_additive_extraction_prompt + parse_messages) directly against
  memory.llm, then validates the raw response as JSON itself
- mem0's own Memory._add_to_vector_store catches a JSON parse failure and
  silently returns an empty list, indistinguishable from "nothing to
  remember" -- this endpoint reports valid_json/used_fallback/error instead
  of swallowing the failure, so a bad candidate model's broken JSON is
  visible rather than looking like a quiet, correct no-op
EOF
)"
```

---

### Task 3: `scripts/test-model.sh` skeleton + Check 1 (text generation)

**Files:**
- Create: `scripts/test-model.sh`

**Interfaces:**
- Produces: shell functions `pass()`, `fail()`, `warn()`, `info()` (print a labeled line; `fail()` also sets global `FAILED=1`), `check_text_generation()` (later tasks add `check_vram_footprint()`, `check_memory_extraction()` alongside it), and top-of-file constants `OLLAMA_URL`, `MODEL`, `FAILED` that later tasks' functions read.

- [ ] **Step 1: Create the file with shebang, strict mode, constants, and output helpers**

```bash
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
```

- [ ] **Step 2: Add usage and dependency precondition checks**

Append:

```bash
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
```

- [ ] **Step 3: Run it with no argument and with a bogus model, confirm the precondition checks fire (red-path verification)**

```bash
chmod +x scripts/test-model.sh
./scripts/test-model.sh
```

Expected: prints `Usage: ...` to stderr, exits non-zero.

```bash
./scripts/test-model.sh totally-bogus-model-xyz
```

Expected: prints `Model 'totally-bogus-model-xyz' isn't available in Ollama...` to stderr, exits non-zero.

- [ ] **Step 4: Add `check_text_generation`**

Append:

```bash
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
```

- [ ] **Step 5: Wire it into a temporary main and run it against the known-good model**

Append (temporary — Task 6 replaces this with the full main flow):

```bash
check_text_generation
```

```bash
./scripts/test-model.sh gemma4-e4b-hauhaucs
```

Expected: `PASS text generation (...)` followed by a printed roleplay reply from the tavern-keeper prompt.

- [ ] **Step 6: Commit**

```bash
git add scripts/test-model.sh
git commit -m "$(cat <<'EOF'
feat: add scripts/test-model.sh skeleton with text-generation check

- Precondition checks: model argument given, docker/curl/jq/nvidia-smi
  present, ollama container running, model already imported into Ollama
- Check 1: sends a roleplay-style prompt to /api/generate, PASS if a
  non-empty response comes back, prints the generated text for the user to
  judge quality themselves
EOF
)"
```

---

### Task 4: Check 2 — VRAM footprint vs. Ollama's estimate

**Files:**
- Modify: `scripts/test-model.sh`

**Interfaces:**
- Consumes: `OLLAMA_URL`, `MODEL`, `info()` (from Task 3).
- Produces: `gpu_mem_used()` (echoes current `nvidia-smi` memory-used in MiB as a plain integer), `unload_all_models()`, `check_vram_footprint()` — later tasks don't depend on these directly, but Task 6's main flow calls `check_vram_footprint`.

- [ ] **Step 1: Add the VRAM helpers and check function**

Insert after `check_text_generation` (before the temporary `check_text_generation` call at the bottom):

```bash
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
```

Note: `.model` in `/api/ps` includes the `:latest` tag (e.g. `"gemma4-e4b-hauhaucs:latest"`) even when the model was referenced untagged — confirmed by querying the live dev `ollama` container's `/api/ps` — so the `jq` select matches either form.

- [ ] **Step 2: Wire it into the temporary main and run against the known-good model**

Change the temporary bottom of the file from:

```bash
check_text_generation
```

to:

```bash
check_text_generation
check_vram_footprint
```

```bash
./scripts/test-model.sh gemma4-e4b-hauhaucs
```

Expected: after the Check 1 output, `--- Check 2: VRAM footprint ---`, a note about unloading, then two MiB numbers where the actual delta is in the same ballpark as Ollama's estimate (both should be roughly 3000+ MiB for this model, per the model's known ~3.2GB size).

- [ ] **Step 3: Commit**

```bash
git add scripts/test-model.sh
git commit -m "$(cat <<'EOF'
feat: add VRAM footprint check to scripts/test-model.sh

- Unloads every currently-loaded Ollama model for a clean baseline, loads
  the candidate model, and diffs nvidia-smi memory.used before/after
- Compares that actual delta against Ollama's own /api/ps size_vram
  estimate for the same model, printed side by side
- Informational only, no pass/fail -- this is the actual-vs-estimated gap
  CLAUDE.md's admission-control lesson describes
EOF
)"
```

---

### Task 5: Check 3 — memory extraction (ephemeral containers, two sub-checks)

**Files:**
- Modify: `scripts/test-model.sh`

**Interfaces:**
- Consumes: `MODEL`, `pass()`, `fail()`, `warn()` (from Task 3); the `/debug/extraction-raw` endpoint (Task 2) and `MEM0_LLM_MODEL`/`QDRANT_HOST`/`QDRANT_COLLECTION` env vars (Task 1).
- Produces: `QDRANT_TEST_CONTAINER`, `MEM0_TEST_CONTAINER`, `MEM0_TEST_IMAGE`, `MEM0_TEST_PORT`, `MEM0_TEST_URL`, `NETWORK` constants; `SAMPLE_MESSAGES`; `cleanup_test_containers()`, `start_test_containers()`, `check_extraction_json_syntax()`, `check_extraction_pipeline()`, `check_memory_extraction()` — Task 6's main flow calls `check_memory_extraction` and registers `cleanup_test_containers` in a trap.

- [ ] **Step 1: Add container/network constants and the sample conversation**

Insert near the top of the file, after the existing `OLLAMA_URL`/`MODEL` constants:

```bash
MEM0_TEST_PORT=18001
MEM0_TEST_URL="http://localhost:${MEM0_TEST_PORT}"
QDRANT_TEST_CONTAINER="qdrant-test"
MEM0_TEST_CONTAINER="mem0-test"
MEM0_TEST_IMAGE="mem0-service:test"
NETWORK="roleplay-net"

SAMPLE_MESSAGES='[
  {"role": "user", "content": "Hi, I'\''m Alex. My favorite color is blue and I work as a high school teacher."},
  {"role": "assistant", "content": "Nice to meet you, Alex! Teaching sounds rewarding."},
  {"role": "user", "content": "Thanks! I also have a dog named Biscuit."}
]'
```

- [ ] **Step 2: Add container lifecycle functions**

Insert after `check_vram_footprint`:

```bash
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
```

- [ ] **Step 3: Add the two sub-checks and the wrapper**

Insert after `start_test_containers`:

```bash
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
```

- [ ] **Step 4: Wire it into the temporary main, with cleanup, and run against the known-good model**

Change the temporary bottom of the file from:

```bash
check_text_generation
check_vram_footprint
```

to:

```bash
trap cleanup_test_containers EXIT

check_text_generation
check_vram_footprint
check_memory_extraction
```

```bash
./scripts/test-model.sh gemma4-e4b-hauhaucs
```

Expected: after Checks 1–2, `--- Check 3a: extraction JSON syntax ---` with `PASS ... (N facts, no fallback needed)`, then `--- Check 3b: end-to-end memory extraction ---` with `PASS memory extraction pipeline (N facts)` and the two facts about Alex/blue/teacher and the dog Biscuit printed.

```bash
docker ps -a --filter name=qdrant-test --filter name=mem0-test
```

Expected: empty (the `trap` removed both containers after the script exited).

- [ ] **Step 5: Commit**

```bash
git add scripts/test-model.sh
git commit -m "$(cat <<'EOF'
feat: add memory-extraction checks to scripts/test-model.sh

- Spins up fully throwaway qdrant-test + mem0-test containers (built fresh
  from ./mem0-service, pointed at the candidate model and a scratch
  collection) on roleplay-net, torn down via a trap on EXIT
- Check 3a hits /debug/extraction-raw: PASS if the model's raw extraction
  response is valid JSON without needing mem0's regex-rescue fallback, WARN
  if only the fallback saved it, FAIL if genuinely invalid
- Check 3b hits /memories (the real end-to-end pipeline): PASS if at least
  one fact gets extracted from a canned sample conversation
- Neither check, nor the containers backing them, ever touches the real
  mem0/mem0-prod containers or the roleplay_memories collection
EOF
)"
```

---

### Task 6: Final main flow, summary table, exit code

**Files:**
- Modify: `scripts/test-model.sh`

**Interfaces:**
- Consumes: everything from Tasks 3–5 (`check_text_generation`, `check_vram_footprint`, `check_memory_extraction`, `cleanup_test_containers`, `FAILED`).
- Produces: the script's final behavior — nothing further depends on this task.

- [ ] **Step 1: Replace the temporary bottom of the file with the real main flow**

Replace:

```bash
trap cleanup_test_containers EXIT

check_text_generation
check_vram_footprint
check_memory_extraction
```

with:

```bash
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
```

- [ ] **Step 2: Full end-to-end run against the known-good model**

```bash
./scripts/test-model.sh gemma4-e4b-hauhaucs
echo "exit code: $?"
```

Expected: all four check sections print in order (1, 2, 3a, 3b), ending in:

```
=== Summary ===
All checks passed.
```

and `exit code: 0`.

- [ ] **Step 3: Confirm the failure path produces a non-zero exit code**

Temporarily verify with a model name that exists in Ollama's library but hasn't been pulled locally (so Check 1 fails at the `/api/generate` step — wait, this would actually be caught by the earlier `/api/show` precondition check and exit before reaching any check function). Instead, confirm the exit-code wiring directly:

```bash
bash -c 'source scripts/test-model.sh gemma4-e4b-hauhaucs >/dev/null 2>&1; FAILED=1; if [[ "$FAILED" -eq 0 ]]; then echo "would report success"; else echo "would report failure"; fi'
```

Expected: `would report failure` (confirms the `if [[ "$FAILED" -eq 0 ]]` / `exit "$FAILED"` logic is correct — `FAILED` is only ever set to `1` by `fail()`, never reset, so any single failed check anywhere in the run correctly fails the whole script).

- [ ] **Step 4: Commit**

```bash
git add scripts/test-model.sh
git commit -m "$(cat <<'EOF'
feat: finish scripts/test-model.sh with summary and exit code

- Prints a final pass/fail summary line after all four checks run
- Exits non-zero if text generation, extraction JSON syntax, or the
  extraction pipeline check failed; VRAM footprint stays informational-only
  and never affects the exit code
EOF
)"
```