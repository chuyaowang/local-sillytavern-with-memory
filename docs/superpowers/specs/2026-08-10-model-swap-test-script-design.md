# Model-swap test script — design

Date: 2026-08-10

## Purpose

`gemma4-e4b-hauhaucs` is currently the one model serving both roleplay
generation and mem0 extraction (see CLAUDE.md). If the user wants to try a
different model, there's currently no repeatable way to check, before
switching production config over to it, whether it: generates usable
roleplay text, survives mem0's ~8,000-token extraction prompt without
producing malformed JSON, and how much VRAM it actually costs versus what
Ollama estimates. This design covers a script that runs all four checks
against a candidate model already imported into Ollama, printing results to
the console.

## Part 1 — config fix (prerequisite)

`mem0-service/main.py` currently hardcodes the extraction model
(`"gemma4-e4b-hauhaucs"`, used in `config["llm"]["config"]["model"]`) and the
Qdrant collection name (`"roleplay_memories"`). Both become env-var driven,
matching the existing `QDRANT_HOST` pattern:

```python
MEM0_LLM_MODEL = os.environ.get("MEM0_LLM_MODEL", "gemma4-e4b-hauhaucs")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "roleplay_memories")
```

used in `config["llm"]["config"]["model"]` and
`config["vector_store"]["config"]["collection_name"]` respectively.

`docker-compose.yml` and `docker-compose.prod.yml` set `MEM0_LLM_MODEL`
explicitly for the `mem0` and `mem0-prod` services (no behavior change today
— same value as the old hardcoded default). `QDRANT_COLLECTION` is *not* set
in either compose file (default covers current behavior); only the test
script's ephemeral container overrides it.

This also means swapping the production model going forward is a one-line
compose-file edit instead of a source change.

## Part 2 — `/debug/extraction-raw` endpoint

Added to `mem0-service/main.py`. Exists to answer a question the real
`/memories` endpoint cannot: whether the model's raw extraction response was
syntactically valid JSON. Confirmed by reading the installed mem0 2.0.17
source (`Memory._add_to_vector_store`) that a JSON parse failure is caught
and silently turned into `extracted_memories = []` — identical to "the model
found nothing worth remembering." A candidate model that produces broken
JSON (e.g. from truncation under a too-small `num_ctx`, mem0's own
known failure mode per CLAUDE.md) is otherwise indistinguishable from a
model that's just working correctly on unremarkable input.

The endpoint replicates mem0's actual internal extraction call using its own
(non-public) building blocks, so the test is faithful to what really happens
during a live conversation:

- `mem0.configs.prompts.ADDITIVE_EXTRACTION_PROMPT` (system prompt)
- `mem0.configs.prompts.generate_additive_extraction_prompt` (user prompt
  builder)
- `mem0.memory.utils.parse_messages` (message-list → transcript string)
- `mem0.memory.utils.remove_code_blocks` / `extract_json` (mem0's own
  response cleanup/rescue steps)

```python
class ExtractionRawRequest(BaseModel):
    messages: list[dict]


@app.post("/debug/extraction-raw")
def extraction_raw(req: ExtractionRawRequest):
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

**Known fragility, accepted deliberately:** `mem0.configs.prompts` and
`mem0.memory.utils` are internal modules, not mem0's public API, and
`requirements.txt` pins no `mem0ai` version — a future upgrade could rename
or remove these and break only this endpoint (test-only code, not the
production extraction path). Consistent with `main.py`'s existing
`classify_shared_facts`, which already calls `memory.llm.generate_response`
directly outside mem0's public `add()`/`search()` surface.

## Part 3 — `scripts/test-model.sh <ollama-model-name>`

Bash script. Precondition: `<ollama-model-name>` is already imported into
the running `ollama` container (`ollama create`/`ollama pull`). Fails fast
with a clear message if `ollama show <model>` can't find it, or if
`docker`/`curl`/`jq`/`nvidia-smi` aren't available, or if the `ollama`
container isn't running.

### Check 1 — text generation

`POST http://localhost:11434/api/generate` with a fixed roleplay-style
prompt, `stream:false`. PASS if HTTP 200 and non-empty `response` field.
Prints the generated text (for the user to judge quality themselves) and
`total_duration`.

### Check 2 — VRAM footprint vs. Ollama's estimate

1. `GET /api/ps`, and for every currently-loaded model call
   `/api/generate` with `keep_alive: 0` to unload it.
2. `nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits` →
   baseline.
3. Trigger the candidate model to load (small `/api/generate` call).
4. `nvidia-smi` again → actual delta.
5. `GET /api/ps` → the candidate model's `size_vram` field → Ollama's own
   estimate.

Both numbers printed side by side. Informational only, no pass/fail — this
is the actual-vs-estimated gap CLAUDE.md's admission-control lesson
describes. Script prints a note that other resident models were evicted as a
side effect of this step.

### Check 3 — memory extraction (two sub-checks)

Spin up two fully ephemeral containers on the existing `roleplay-net`
network, both `--rm`, both force-removed first in case a prior run left
them stuck (`docker rm -f qdrant-test mem0-test 2>/dev/null || true`):

- `qdrant-test`: plain `qdrant/qdrant:latest`, no published port, no
  persistent volume — internal-network-only, discarded on exit.
- `mem0-test`: built fresh at script start from `./mem0-service`
  (`docker build -t mem0-service:test ./mem0-service`, so it reflects the
  current source including the Part 2 endpoint even if the real `mem0`
  container hasn't been rebuilt), env `MEM0_LLM_MODEL=<candidate>`,
  `QDRANT_HOST=qdrant-test`, `QDRANT_COLLECTION=model_eval_scratch`,
  `MEM0_TELEMETRY=False`, published to `127.0.0.1:18001:8001`.

Script polls `http://localhost:18001/health` until ready (60s timeout) before
running checks. A canned sample conversation with three unambiguous facts
(name+color preference, job, pet) is used for both sub-checks:

- **3a — JSON syntax**: `POST /debug/extraction-raw` with the sample
  conversation. PASS if `valid_json: true` and `used_fallback: false`. WARN
  (not fail) if `valid_json: true` but `used_fallback: true` — mem0's rescue
  path saved it, but the model is skirting correctness. FAIL if
  `valid_json: false`. Prints `memory_count` and the raw/cleaned response
  (truncated to a readable length).
- **3b — end-to-end pipeline**: `POST /memories` with the same sample
  conversation. PASS if the response contains at least one extracted fact.
  Prints the extracted facts.

`trap 'docker rm -f qdrant-test mem0-test >/dev/null 2>&1' EXIT` ensures both
containers are removed on any exit path (success, failure, or interrupt),
so a crash never leaves stray containers or touches the real `roleplay_memories`
collection, `qdrant`, or `qdrant-prod`.

### Summary

Final table: PASS/FAIL for checks 1, 3a, 3b (3a's WARN state shown
distinctly); VRAM numbers from check 2 (informational). Exit code is
non-zero if any of checks 1/3a/3b failed, zero otherwise (3a's WARN state
does not affect exit code).

## Out of scope

- Automated judgment of roleplay text *quality* (tone, character
  consistency) — left to the human reading the printed output.
- Testing the embedder (`nomic-embed-text`) — it isn't the thing being
  swapped.
- Persisting test results anywhere — console output only, per the request.
