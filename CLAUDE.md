# Local Roleplay Agent — Project Notes

## Goal

A fully local chat/roleplay agent system: local LLM inference, a roleplay-capable
chat frontend, and persistent memory about the user across characters/sessions.
No data leaves the local network.

## Architecture (current, implemented)

Everything runs on a single GPU-equipped machine ("the host") via Docker Compose
(`docker-compose.yml` at repo root: `ollama`, `qdrant`, `mem0`, `sillytavern`, all
on a shared `roleplay-net` bridge network). A second computer on the same
Tailscale network accesses SillyTavern via browser only.

Docker itself runs on **native Docker Engine** (`docker-ce`, installed via apt),
not Docker Desktop — Docker Desktop for Linux runs everything inside an internal
VM, which adds an indirection layer that GPU passthrough doesn't need. GPU access
uses the NVIDIA Container Toolkit (`nvidia-ctk runtime configure --runtime=docker`).
Docker Desktop is still installed and selectable via `docker context`, but native
(`default` context) is what's actually used.

### Inference (Ollama)

- Containerized (`ollama/ollama` image), GPU passthrough via native Docker
  Engine and nvidia-container-toolkit. Bound to `127.0.0.1:11434` — never
  exposed beyond localhost.
- Model storage is a named Docker volume (`ollama_models`), not a bind mount to
  the native install — the native systemd Ollama install was fully removed once
  the container was verified working (binary, systemd unit, `/usr/share/ollama`
  data dir, and the `ollama` system user/group all deleted).
- On the host, `ollama` is aliased to `docker exec -it ollama ollama "$@"` (added
  to `~/.bashrc`) so the CLI works transparently against the container.
- **One unified model serves both roleplay generation and memory extraction**:
  `gemma4-e4b-hauhaucs`, imported from a local GGUF
  (`models/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf`) via
  `models/Modelfile`. `num_ctx` is set to 16384 — mem0's extraction system
  prompt alone is ~8,000 tokens, and smaller context windows silently truncate
  it, causing schema failures (see "Lessons learned" below).
- `nomic-embed-text` is pulled directly from Ollama's library for embeddings.
- Originally planned to use a separate, smaller model for extraction (to save
  VRAM) alongside a roleplay-tuned model. Abandoned: the roleplay-tuned
  "obliterated"/abliterated checkpoints tested were unreliable at mem0's
  structured JSON extraction regardless of quantization (Q5 and Q4 both
  failed), and Ollama's admission-control estimator made 3-model VRAM
  coexistence impractical on a 6GB card. `gemma4-e4b-hauhaucs` passed
  extraction-reliability testing cleanly *and* works for roleplay, so one
  model now does both jobs — this also sidesteps the VRAM/eviction problem
  entirely, since only one model + the embedder need to stay resident.

### Memory (mem0 + Qdrant)

- `mem0ai` (2.0.x) as its own FastAPI service (`mem0-service/`), not embedded in
  the middleware — kept as a separate container so a future memory-editing
  tool (not yet built) can talk to the same service without duplicating the
  mem0 client setup.
- Vector store: **Qdrant** (self-hosted container), chosen over Chroma as
  mem0's best-supported/most-tested backend and better suited to a
  long-running multi-consumer service than Chroma's embedded-first design.
- `llm` and `embedder` config both point at the local Ollama container
  explicitly — mem0 defaults both to OpenAI otherwise.
- `MEM0_TELEMETRY=False` set explicitly — mem0 defaults to phoning home to
  PostHog otherwise, which would violate the local-only requirement.
- **No graph database (Neo4j, FalkorDB, or otherwise) is used.** `graph_store`
  as a config concept was **removed entirely in mem0 2.0** — `MemoryConfig`
  has no such field, and passing one is silently ignored (confirmed by
  inspecting the installed package directly). Neo4j was originally stood up
  per the plan below, found to be receiving zero data, and fully removed
  (container, volumes, config, all references). The `mem0-falkordb` bridge
  package was also evaluated and found incompatible with mem0 2.0.17 (its
  runtime-patching approach doesn't restore the removed Pydantic field).
- What replaced graph_store: mem0's built-in **`entity_store`** — a *second*
  Qdrant collection linking named entities (people, places, quoted terms) to
  memories, for search-relevance boosting. Not a relationship graph. Requires
  the `mem0ai[nlp]` extra (spaCy + `en_core_web_sm`, pre-baked into
  `mem0-service`'s Dockerfile) — without it, entity linking silently no-ops.

### Memory scoping — hybrid (implemented)

The original open question ("shared vs. per-character memory") is resolved:

- Every memory gets a real `agent_id`: either the actual character name, or a
  fixed sentinel `"shared"` when none is given. (mem0's filter DSL has no
  "field is unset" operator — only equality/comparison on values that exist —
  so leaving `agent_id` unset would make a true shared-only query impossible.)
- **Shared layer** (`agent_id="shared"`): general facts about the user,
  visible to every character.
- **Character layer** (`agent_id=<character>`): relationship/history specific
  to one character, private to it.
- `GET /memories/context` runs both queries and returns them separately;
  callers (the SillyTavern extension) merge them for prompt injection.
- **LLM-based classification**: every character-scoped `POST /memories` also
  runs a second, lightweight classification pass (same model, a separate
  prompt asking "which of these facts are general vs. relationship-specific")
  and mirrors general facts into the shared layer via a second `mem0.add()`
  call with `infer=False` (storing the already-classified text directly,
  skipping re-extraction). Character-specific content never leaks into
  shared; verified by checking a fact's visibility from a character that
  never took part in the original conversation.

### Frontend (SillyTavern)

- Containerized (`ghcr.io/sillytavern/sillytavern`), config/data/plugins/
  extensions persisted under `sillytavern/` in the repo.
- The **only** service bound beyond `127.0.0.1` — `0.0.0.0:8000`, since it's
  the one component meant to be reachable from the Tailscale network.
- Access control is **two layers together**, not either/or:
  `basicAuthMode: true` (credentials in `sillytavern/config/config.yaml`,
  gitignored) *and* `whitelistMode: true` with the specific Tailscale IPs of
  the approved devices added to `whitelist` (default `whitelistDockerHosts`
  only covers the Docker gateway, not real remote clients, so it doesn't help
  here). Note: testing from the host itself hits Docker's NAT quirks — a
  request to `127.0.0.1` appears internally as the bridge gateway IP, while a
  request to the host's own Tailscale IP appears as that real IP — the
  gateway IP (`172.18.0.1` for this network) is whitelisted too, purely for
  host-side testing convenience.
- Connected to Ollama via **API type: Text Completion, source: Ollama, Server
  URL: `http://ollama:11434`** (the Docker-internal hostname — `localhost`
  from inside the ST container would mean the ST container itself, not
  Ollama).

### ST ↔ mem0 integration — two pieces, not one

The original open question ("extension vs. middleware proxy") is resolved as
**both**, because a pure client-side (browser JS) extension can't reach
`mem0` at all — `mem0` is bound to `127.0.0.1:8001` on the host, unreachable
from a remote device's browser even over Tailscale, and opening it up would
break the "internal services stay local" posture.

1. **Server plugin** (`sillytavern/plugins/roleplay-memory/`, Node.js,
   requires `enableServerPlugins: true` in `config.yaml`). Runs *inside* the
   ST container, so it can reach `http://mem0:8001` over the internal Docker
   network like any other service. Exposes `/api/plugins/roleplay-memory/context`
   and `/add`, proxying to `mem0-service`. This is the piece that actually
   solves the reachability problem.
   - Needs its own `package.json` with `"type": "commonjs"` — the ST app's own
     `package.json` declares `"type": "module"`, so a bare `.js` file in a
     subdirectory without one gets treated as ESM by default and
     `module.exports` fails.
2. **Client extension** (`sillytavern/extensions/roleplay-memory/`, browser
   JS). Two jobs:
   - **Pull**: `generate_interceptor` (registered via `manifest.json`'s
     `generate_interceptor` field) calls the plugin's `/context` route and
     injects results via SillyTavern's own `setExtensionPrompt(key, value,
     type, depth, scan, role)` — the same mechanism ST's built-in
     summarize/memory extension uses. **Use `extension_prompt_types.IN_PROMPT`
     (0), not `IN_CHAT` (1)** — `IN_CHAT` only merges into a structured Chat
     Completion messages array; `IN_PROMPT` is what actually merges into a
     flat Text Completion prompt string (confirmed empirically by inspecting
     the real outgoing prompt in ST's logs — these enum values aren't exposed
     via `SillyTavern.getContext()`, so they're hardcoded in the extension
     with a comment). Getting this wrong doesn't error — it just silently
     doesn't appear in the prompt.
   - **Push**: listens for `MESSAGE_RECEIVED`, batches exchanges into a
     buffer rather than sending after every single message, and flushes
     (POSTs to the plugin's `/add` route) on whichever comes first: buffer
     crosses ~800 estimated tokens (character count / 4, not exact
     tokenization), 2 minutes of idle conversation, the user says something
     like "remember this"/"memorize that", or the chat/character changes
     (flushes immediately so buffered messages don't get misattributed to
     whatever character comes next).
   - POST requests to ST's own API need an `X-CSRF-Token` header (fetched
     from `/csrf-token` first) or they get rejected with 403, even with valid
     basic-auth/session.
   - `user_id`/`agent_id` are derived client-side from ST's own state:
     `user_id` from the persona name (`context.name1`), `agent_id` from the
     active character's name (`context.characters[context.characterId].name`)
     — both slugified.

### Dev vs. prod environments

- `docker-compose.yml` (`ollama`, `qdrant`, `mem0`, `sillytavern`) is the
  development stack — where extension/plugin/mem0-service changes get tested.
  `docker-compose.prod.yml` is an overlay for the real-use instance, not a
  standalone file — it relies on `roleplay-net` and the `ollama` service
  declared in the base file, so it's always run together:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
  qdrant-prod mem0-prod sillytavern-prod`.
- **Shared**: Ollama (GPU/VRAM is the scarce resource on a 6GB card — no
  reason to load the model twice) and the SillyTavern plugin/extension code
  (`sillytavern/plugins`, `sillytavern/extensions`, bind-mounted into both ST
  containers from the same host path). It's git-tracked source, not runtime
  state, so there's nothing to contaminate — and it means a plugin fix
  doesn't need a separate promotion step to reach prod.
- **Duplicated**: Qdrant (`qdrant-prod` container, separate named volume
  `qdrant_storage_prod`), mem0-service (`mem0-prod`, same image/build as
  `mem0`, pointed at `qdrant-prod` instead of `qdrant`), and SillyTavern's
  config/data (`sillytavern-prod/config/`, `sillytavern-prod/data/`,
  gitignored the same way as the dev copies, own basic-auth credentials and
  whitelist). `mem0-service/main.py` reads `QDRANT_HOST` from the environment
  (default `qdrant`) specifically so the same built image serves either
  stack without a code fork.
- **Rejected**: a single shared mem0-service instance manually repointed at
  whichever Qdrant is "active" via env var + restart. mem0-service is
  stateless per request and has no way to know which Qdrant is prod vs. dev
  — a manual toggle is exactly the kind of step that gets forgotten,
  silently writing test data into the real store. Two always-running,
  differently-named services fix the target by which container you're
  talking to, not a mutable setting someone has to remember to flip.
- Both SillyTavern instances are reachable over Tailscale, on different host
  ports (`sillytavern` on `:8000`, `sillytavern-prod` on `:8010`) — a device
  needs a whitelist entry in both `config.yaml` files to reach both.
  `mem0-prod`/`qdrant-prod` stay on `127.0.0.1` only, same posture as the dev
  copies, just on different ports (`8011`, `6343`/`6344`).

## Lessons learned (things that failed silently or non-obviously)

- **Never mutate the `chat` array directly to inject "hidden" context.** An
  earlier version of the interceptor spliced a hand-rolled message object
  (`is_system: true` — a field that isn't actually respected) into the `chat`
  array. Two bugs resulted: the note leaked into the visible, persisted
  reply, and the model treated it as the start of its own turn and echoed it
  back verbatim — compounding into a snowballing hallucination loop once
  mem0 extracted the model's own confabulated "memory bank" explanations as
  new facts. Always use `setExtensionPrompt` instead.
- **mem0's system prompt is large (~8,000 tokens) and non-configurable**
  through the public API (`custom_instructions` is additive, not a
  replacement). Any model doing extraction needs `num_ctx` comfortably above
  that or facts silently get truncated away.
- **Ollama's VRAM admission control uses a pre-load *estimate*, not real
  available memory.** A multimodal-capable model (vision+audio bundled, even
  if only used for text) gets sized for worst-case mmproj memory and will
  evict everything else regardless of actual headroom. Custom GGUF imports
  without a declared multimodal capability avoid this entirely.
- **Ollama disables `mmap` under host memory pressure**, which increases RAM
  usage further and can spiral. Restarting the `ollama` container clears
  accumulated host-side memory from cycling through many large models during
  testing.
- **A model's own claims about "remembering" or "logging to a profile" are
  not evidence anything actually happened** — RLHF-tuned models readily
  hallucinate this kind of flourish. Verify via server-side logs/data
  directly (mem0's own request log, direct Qdrant queries) before trusting a
  model's self-report.

## Why mem0 over Letta

Letta's memory updates depend on the *roleplay-facing* model reliably making
tool calls mid-conversation to edit memory blocks — a real risk with a
small local model. mem0 decouples memory extraction into its own dedicated
LLM call with a purpose-built prompt, independent of the roleplay model's own
function-calling reliability. Chose mem0 for robustness with a small local
model.

## Known follow-ups (not yet built)

- [ ] Memory-editing tool — a way to browse/correct/delete stored memories
      outside of steering a live conversation. Motivated `mem0-service` being
      a standalone service rather than embedded in the extension/plugin.
- [ ] Nothing else from the original plan is currently open — vector store,
      memory scoping, and the ST↔mem0 integration are all decided and
      implemented (see above).
