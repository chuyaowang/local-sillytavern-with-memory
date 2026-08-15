# Local Roleplay Agent — Project Notes

## Goal

A fully local chat/roleplay agent system: local LLM inference, a roleplay-capable
chat frontend, and persistent memory about the user across characters/sessions.
No data leaves the local network.

## Architecture (current, implemented)

Everything runs on a single GPU-equipped machine ("the host") via Docker Compose
(`docker-compose.yml` at repo root: `llama-cpp`, `qdrant`, `mem0`, `sillytavern`, all
on a shared `roleplay-net` bridge network). A second computer on the same
Tailscale network accesses SillyTavern via browser only.

`ollama` is also still defined in `docker-compose.yml` but unused — kept as a
zero-risk fallback after the switch to llama.cpp (see below), not removed.
It won't start via any `make` target; its old imported model blobs
(~13.8 GB) have been cleared from its volume to reclaim disk space, though
the volume/service definition itself is untouched.

Docker itself runs on **native Docker Engine** (`docker-ce`, installed via apt),
not Docker Desktop — Docker Desktop for Linux runs everything inside an internal
VM, which adds an indirection layer that GPU passthrough doesn't need. GPU access
uses the NVIDIA Container Toolkit (`nvidia-ctk runtime configure --runtime=docker`).
Docker Desktop is still installed and selectable via `docker context`, but native
(`default` context) is what's actually used.

### Inference (llama.cpp)

- Containerized (`ghcr.io/ggml-org/llama.cpp:server-cuda13`), GPU passthrough
  via native Docker Engine and nvidia-container-toolkit. Bound to
  `127.0.0.1:8080` — never exposed beyond localhost.
- Runs in **router mode** (`--models-preset llama-cpp/models-preset.ini
  --models-max 2`), not the vanilla single-`--model` mode — one process
  serves multiple GGUFs on demand, loading/evicting per request based on the
  `model` field, the same way Ollama could hold multiple tags. Confirmed
  each model actually runs as a *separate child process* (own PID, own CUDA
  context) under the hood, not a shared context — relevant to the VRAM notes
  below. `--models-preset` is an INI file, one `[section]` per model, keys
  matching CLI flags with the leading dashes dropped; there's also a
  `--models-dir` auto-discovery mode but the preset gives per-model control
  (sampling defaults, KV cache quant) that auto-discovery doesn't.
- Replaced Ollama for a measured ~12% generation-speed gain at comparable
  VRAM (once KV cache quantization was matched on both sides) — full
  benchmark methodology and numbers in `docs/LLAMA_CPP_BENCHMARK.md`.
- **One unified model still serves both roleplay generation and memory
  extraction**, now enforced automatically rather than by convention:
  `mem0-service` asks the router what models actually exist
  (`GET /models` — no hardcoded model names anywhere) and builds one
  `Memory` instance per discovered model at startup; the SillyTavern
  extension reads its own currently-active model
  (`context.textCompletionSettings.llamacpp_model`) and sends it with every
  extraction request, so `mem0-service` always uses whatever SillyTavern is
  actually using. This replaced an earlier, broken version of this exact
  system where `mem0`'s model was a separate hardcoded config value that
  could silently drift from SillyTavern's — caught by testing, not
  inspection (see "Lessons learned").
- Two quants configured in the preset: `gemma-q4` (safe default, ~3.3 GB) and
  `gemma-q8` (~5.3 GB, tight — see below). `ctx-size` stays at 16384 for the
  same reason as always: mem0's extraction system prompt alone is ~8,000
  tokens, and smaller context windows silently truncate it, causing schema
  failures (see "Lessons learned" below).
- Model files live in `models/` and are read directly — no equivalent of
  `ollama create`/import step. Switching to a different GGUF is just editing
  `llama-cpp/models-preset.ini` and restarting the container.
- **Embedding model**: `nomic-embed-text-v1.5.f16.gguf`, downloaded by hand
  from `nomic-ai/nomic-embed-text-v1.5-GGUF` on Hugging Face into `models/`
  (confirmed byte-for-byte equivalent to Ollama's copy by matching file
  size against Ollama's own blob) — llama.cpp has no model registry to pull
  from the way Ollama did. Served from the same router process as the chat
  models, via a `[nomic-embed-text-v1.5]` preset section with `embeddings = 1`;
  its section name must stay in sync with the hardcoded model string in
  `mem0-service/main.py`'s embedder config.
- Originally, for the Ollama-era setup: a separate, smaller model for
  extraction (to save VRAM) alongside a roleplay-tuned model was planned and
  abandoned. The roleplay-tuned "obliterated"/abliterated checkpoints tested
  were unreliable at mem0's structured JSON extraction regardless of
  quantization (Q5 and Q4 both failed), and Ollama's admission-control
  estimator made 3-model VRAM coexistence impractical on a 6GB card.
  `gemma4-e4b-hauhaucs` (now served as `gemma-q4`/`gemma-q8`) passed
  extraction-reliability testing cleanly *and* works for roleplay, so one
  model still does both jobs — this is also why only one chat model plus
  the embedder need to stay resident at once (`--models-max 2`).

### Memory (mem0 + Qdrant)

- `mem0ai` (2.0.x) as its own FastAPI service (`mem0-service/`), not embedded in
  the middleware — kept as a separate container so a future memory-editing
  tool (not yet built) can talk to the same service without duplicating the
  mem0 client setup.
- Vector store: **Qdrant** (self-hosted container), chosen over Chroma as
  mem0's best-supported/most-tested backend and better suited to a
  long-running multi-consumer service than Chroma's embedded-first design.
- `llm` and `embedder` config both point at the local llama.cpp container's
  OpenAI-compatible API (`MEM0_LLM_PROVIDER=openai`/`MEM0_EMBEDDER_PROVIDER=openai`,
  `*_BASE_URL=http://llama-cpp:8080/v1`) — mem0 defaults both to real OpenAI
  otherwise. `mem0-service/main.py` builds one `Memory` instance per model
  discovered from the router (see "Inference" above) rather than a single
  fixed instance; `MEM0_LLM_MODEL`/`MEM0_EMBEDDER_MODEL` are just the
  fallback defaults for a request that doesn't specify a model.
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
  and **moves** general facts into the shared layer via a second `mem0.add()`
  call with `infer=False` (storing the already-classified text directly,
  skipping re-extraction) followed by deleting the character-scoped
  original. A memory only ever lives in one scope at a time — an earlier
  version mirrored instead of moving (kept both copies), which meant a
  query pulling both scopes for a relevant question could inject the same
  fact twice; deleting the original after a successful move fixed this.
  Character-specific content never leaks into shared; verified by checking
  a fact's visibility from a character that never took part in the
  original conversation.

### World lore (mem0, replacing SillyTavern's native World Info)

ST's built-in World Info matches lore into the prompt by literal keyword
scan against recent messages (confirmed by reading `world-info.js`
directly) — brittle, since a paraphrase that shares no exact keyword with
an entry gets nothing injected. Lore is now a third mem0 scope with
embedding-similarity retrieval instead, on a third, orthogonal axis from
the shared/character split above:

- **`user_id="world"`** is a fixed sentinel (mirroring `agent_id="shared"`
  above, but on the *other* axis) — world lore isn't "about" any real
  user. A world is just an `agent_id` value under that sentinel
  (`"eldoria"`, `"kivotos"`, ...); no per-world schema, no new
  list/multi-world endpoints needed — `GET /scopes?user_id=world` already
  returns which worlds have lore, and the existing admin UI (`/ui`)
  already browses/edits/deletes them by picking `user_id=world` from its
  dropdown.
- **World binding: persona first, then character.** A character picks up
  a world through its own card (`character.data.extensions.world`, the
  globe-icon "Primary Lorebook" picker) — reused as-is instead of a
  separate mapping, confirmed empirically that ST never rewrites the
  bound world file on its own (wiped `sillytavern/data` entirely, a
  brand-new container with zero chat messages already had both
  `Eldoria.json` and `Seraphina` present — bundled default content baked
  into the SillyTavern image's `/home/node/app/default/content/`, seeded
  on first run, not written by ST or mem0 over time), so it's safe to
  leave the binding field in place while emptying that world file's `entries` to
  `{}` — ST's keyword scanner then has nothing to match, killing
  double-injection, while the extension keeps reading the same field as
  pure metadata. A *persona* can also bind to a world independently
  (`power_user.persona_description_lorebook`, ST's own per-persona
  lorebook picker, confirmed exposed via `getContext().powerUserSettings`
  — same live object, not a snapshot). The extension's `resolveWorld()`
  checks the persona binding first, falling back to the active
  character's — so a persona bound to a world determines it even when
  talking to a character that isn't itself bound to one.
- **Two ways lore gets written, both automatic — never manually posted
  during normal use:**
  1. **Passive**, during ordinary roleplay: the existing shared/character
     classifier (`classify_facts` in `mem0-service/main.py`) is 3-way —
     shared / character-private / world. It does **not** re-extract facts
     from the raw conversation — an earlier version did, independently
     re-deriving its own view of what counted as shared/world, and was
     unreliable about respecting its own "exclude character personality/
     traits" rule (observed leaking a character's job/habits into shared
     memory). It now operates on the memory items Pass 1 (mem0's own
     extraction, run moments earlier in the same `add_memory()` call)
     already produced: the prompt states all three scopes plainly
     (character/shared/world), and classifies each item by number rather
     than retyping its text — selected facts are looked up verbatim by
     index afterward, not trusted to be reproduced unchanged. A fact
     classified as shared or world is **moved**, not mirrored: written to
     its new scope, then deleted from character scope via `mem.delete()`
     (only after the write succeeds). A memory only ever lives in one
     scope — an earlier version kept both copies, which let a query
     pulling both scopes for a relevant question inject the same fact
     twice. Since the moving write uses `infer=False` (skips mem0's own
     entity-store linking, which only runs inside its `infer=True`
     pipeline), `_link_entities()` replicates that step by calling mem0's
     own private entity-store helpers directly — a plain spaCy NER pass
     (`extract_entities_batch`, no LLM call) followed by the same
     match-or-insert logic mem0's own pipeline uses, so a moved fact keeps
     its entity-search-boosting instead of silently losing it.
  2. **Active**, via a **World Creator** agent: a plain SillyTavern
     character (not a special API flag — ST's character panel has no UI
     for arbitrary custom `extensions` fields) that must be named exactly
     `World Weaver`, detected client-side by name. Card JSON lives at
     `sillytavern/character-cards/world-weaver.json` (import via ST's
     character panel, once per environment — not auto-installed). Its
     exchanges route to `POST /worlds/interview` instead of the normal
     `/add` path — a dedicated extraction prompt (`WORLD_INTERVIEW_PROMPT`)
     identifies both the world's name *and* its facts from the interview
     transcript, since (unlike a roleplay character) it isn't bound to one
     fixed world going in. World-scope only — deliberately does **not**
     also run shared-fact classification against the interview transcript
     (an interview isn't a conversation with a character, so there's no
     relationship dimension). Because it writes into the same store the
     passive path reads from, a lorebook built this way is available
     immediately in a fresh roleplay session bound to that world.
     - Flushes after **every** exchange rather than batching like ordinary
       roleplay chat — the push-side buffer only lives in the browser
       tab's memory, never persisted, and losing an answer to a closed tab
       before the normal token/idle threshold fired wasn't an acceptable
       tradeoff for World Weaver's low-volume, deliberate exchanges.
     - Its own pull-side memory injection is muted entirely (extension
       prompt slot explicitly cleared, not just skipped over — it isn't
       per-character, so a prior character's injected memories would
       otherwise still be sitting in it) — building a new world from a
       clean slate, and injecting memories (especially lore from an
       existing bound world, easy to trigger if the persona itself is
       bound to one) would contaminate it.
     - The card's `mes_example` originally included a concrete sample
       interview (a fictional "Kivotos" world). The model treated it as
       real prior context rather than a style example — after the user
       only stated a world's name, it immediately asserted the sample's
       specific facts as already established. Fixed by removing the
       example entirely and hardening the card's "recap what's
       established so far" instruction from a soft "when helpful" to
       unconditional ("every reply... even for a short exchange") — the
       recap also keeps the world's name present in every isolated
       single-exchange flush, since the name is usually only stated once.
- `GET /memories/context` gained an optional `world` param — a third
  `memory.search()` alongside the existing `shared`/`character` ones,
  returned as a `world` key; the extension injects it as a distinct
  `World lore: ...` line, separate from `Relevant memories: ...`, so the
  model can tell setting lore apart from relationship history.
- **Shared memory is also world-scoped**, via mem0's metadata filtering
  (confirmed supported — `Memory.search()`'s `filters` dict accepts
  arbitrary metadata keys with real operators, not just user_id/agent_id/
  run_id, per its own docstring and `_build_filters_and_metadata()`'s
  handling of custom metadata). Every shared-fact move write is tagged
  `metadata={"world": resolved_world or NO_WORLD}` (`NO_WORLD = "none"`,
  a real sentinel rather than an unset field — same reasoning as
  `SHARED_AGENT_ID`/`WORLD_USER_ID`: mem0's filter DSL has no "field is
  unset" operator). `get_context()`'s shared query filters to
  `{"world": {"in": [world, NO_WORLD]}}` — a universal fact always shows,
  a world-tagged one only resurfaces for that same world. Motivated by an
  observed leak: "the user's mount is the Aetherian Stride" (personal,
  Eldoria-specific) was showing up for characters from unrelated
  settings before this. **Known tradeoff, left as-is on purpose**: the
  whole batch from one exchange shares one world tag, so a genuinely
  universal fact (e.g. the user's real name) learned mid-roleplay also
  stops surfacing for unrelated worlds — fixing that would need the
  classifier to judge per-fact universality, not just per-fact
  world-relevance, and wasn't judged worth the added complexity yet.
- **Character and shared memory are correctly isolated per real user**
  (verified by reading `_scope()`/`get_context()`'s filters directly) —
  both are always scoped by the actual `user_id`, so two different people
  (or personas) talking to the same character never see each other's
  facts. World lore is the one deliberate exception, scoped under the
  `user_id="world"` sentinel rather than the real user, so it's genuinely
  shared across whoever talks about that world — matching that it's meant
  to represent the setting, not any one person's data.
- `POST /worlds/{world}/memories` is a low-level write primitive (content
  in, mem0's normal extraction pipeline runs on it, `infer` left at its
  default `True`) — used only by the one-time backfill script
  (`scripts/migrate-lorebook-to-mem0.sh <world-json-file> <world-name>
  [mem0-url]`), which reads an existing ST World Info file's
  `entries[*].content` and posts each through it before that file's
  entries get emptied, so pre-existing lore isn't lost. Deliberately runs
  extraction rather than storing the raw blob verbatim — ST World Info
  entries are often whole example-dialogue blocks (`{{user}}: "..."
  {{char}}: "..."`), not atomic facts, and verbatim storage would embed a
  huge, semantically-diluted blob instead of the same atomic-fact shape
  every other write path produces.

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
- Connected to llama.cpp via **API type: Text Completion, source: llama.cpp,
  Server URL: `http://llama-cpp:8080`** (the Docker-internal hostname —
  `localhost` from inside the ST container would mean the ST container
  itself, not llama.cpp). ST's llama.cpp connector already has native model
  dropdown support (`textgen-models.js`'s `loadLlamaCppModels()`, fetches
  the router's `GET /models`) — switching quants is just picking a different
  entry from that dropdown, no plugin/extension changes needed to support
  it. Confirmed by reading ST's own source before relying on it
  (`public/scripts/textgen-models.js`, `textgen-settings.js`), not assumed.

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
   - Also sends `model`, read from
     `context.textCompletionSettings.llamacpp_model` (only when
     `mainApi === 'textgenerationwebui'` and `textCompletionSettings.type
     === 'llamacpp'`) — this is what keeps mem0's extraction model in sync
     with whatever SillyTavern is actually generating with. Both fields
     aren't part of the documented extension API; verified directly against
     `st-context.js`'s `getContext()` before relying on them.

### Dev vs. prod environments

- `docker-compose.yml` (`llama-cpp`, `qdrant`, `mem0`, `sillytavern`) is the
  development stack — where extension/plugin/mem0-service changes get tested.
  `docker-compose.prod.yml` is an overlay for the real-use instance, not a
  standalone file — it relies on `roleplay-net` and the `llama-cpp` service
  declared in the base file, so it's always run together:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
  qdrant-prod mem0-prod sillytavern-prod`. The `Makefile` wraps this (and the
  matching stop commands, careful to never stop the shared `llama-cpp` service
  when only one stack should go down) as `make dev-up`/`dev-down`/`prod-up`/
  `prod-down`/`down`/`status` — that's the version actually worth remembering.
  `dev-up`/`prod-up` list services explicitly rather than a blanket
  `docker compose up -d`, specifically so `ollama` never gets started as a
  side effect now that `llama-cpp` has no Compose profile restricting it.
- **Shared**: llama.cpp (GPU/VRAM is the scarce resource on a 6GB card — no
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
  stack without a code fork. `mem0`/`mem0-prod` are separately *tagged*
  images from the same build context, though — changing `mem0-service`
  code and only rebuilding `mem0` leaves `mem0-prod` running the old image
  until it's rebuilt too (`docker compose up -d --build mem0-prod`);
  confirmed the hard way when a fixed crash still reproduced in prod.
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
- **SillyTavern's server plugin (`sillytavern/plugins/roleplay-memory/`)
  only loads its routes once, at container startup** — unlike the client
  extension, which the browser just fetches fresh on reload. Editing the
  plugin's code and testing through the UI without restarting the
  container hits a plain 404 on any new/changed route, easy to misread as
  a client-side caching issue instead. `docker compose restart
  sillytavern` (and `sillytavern-prod`, since they share the same
  bind-mounted plugin source) after any plugin change, confirmed by
  `docker logs sillytavern | grep -i plugin` showing "Initializing plugin
  from ...".
- **llama.cpp's `--fit` (on by default, keeps a VRAM safety margin) only
  adjusts CLI arguments left unset.** Pinning `n-gpu-layers` explicitly (as
  the Q8 preset originally did, copied from the Q4 one) silently disables
  the whole safety mechanism — logged as `"failed to fit params... abort"`
  and easy to miss. Result: Q8 loaded right up to the VRAM edge and failed
  unpredictably (a `cudaMalloc` OOM one run, a `cublasCreate_v2` failure
  another, a silent 30x slowdown a third) whenever the embedder also needed
  room. Leaving `n-gpu-layers` unset for a tight-VRAM quant lets `--fit`
  offload a few layers to CPU instead — stable across repeated tests, at a
  real cost (~18.6 tok/s vs. ~50 tok/s for Q8). Not a free fix; a real
  tradeoff worth knowing about before assuming "it loaded, so it's fine."
- **mem0's `OpenAILLM.generate_response()` has no per-call model
  override** — it always uses `self.config.model`, fixed at construction,
  regardless of any `model` kwarg passed in. Point-in-time discovery, not
  assumption (checked the installed package's source directly). This is why
  per-request model routing needed one `Memory` instance per model
  (`mem0-service/main.py`'s `memories_by_model`) rather than a single
  instance with a swapped-in model name.
- **llama.cpp's router mode runs each model as a genuinely separate OS
  process**, each with its own CUDA context — confirmed via distinct PIDs
  in the container logs. This means per-process CUDA context overhead
  (tens to ~100 MiB, measured directly with `-lv 4` trace logging) is paid
  once per *loaded model*, not once per container, and "free" VRAM reported
  by `nvidia-smi` isn't necessarily usable as one contiguous allocation by
  whichever process needs it next — a likely factor in the Q8 instability
  above, beyond the raw byte-count margin.
- **This specific fine-tune (HauhauCS Gemma 4 E4B) emits a separate
  reasoning/"thinking" block** (`message.reasoning_content` via llama.cpp's
  OpenAI-compatible API) before its actual reply, and at low temperature or
  a tight token budget it can burn the *entire* budget on reasoning and
  return empty `content` — not a bug, a real model behavior. Give it
  generous `max_tokens` and don't assume `content` is non-empty just
  because the request succeeded.
- **Disk read speed can be the dominant factor in "cold start" benchmark
  numbers, easy to mistake for something else.** On this host, the drive
  holding the whole project turned out to be an external SSD plugged into
  a USB 2.0 port (capped ~35-40 MB/s regardless of the SSD's real speed) —
  found by actually measuring (`dd` timing, `lsusb -t` showing `480M` vs.
  the host's other `10000M`-capable controllers) rather than assuming slow
  model loads were inherent to the model/backend. Moving it to USB 3.x cut
  multi-minute cold loads down to single-digit seconds. If a load/switch
  benchmark looks unexpectedly slow, check the storage path before blaming
  the inference backend.

## Why mem0 over Letta

Letta's memory updates depend on the *roleplay-facing* model reliably making
tool calls mid-conversation to edit memory blocks — a real risk with a
small local model. mem0 decouples memory extraction into its own dedicated
LLM call with a purpose-built prompt, independent of the roleplay model's own
function-calling reliability. Chose mem0 for robustness with a small local
model.

## Known follow-ups (not yet built)

- [x] Memory-editing tool — a way to browse/correct/delete stored memories
      outside of steering a live conversation, motivated by `mem0-service`
      being a standalone service rather than embedded in the extension/
      plugin. Built: `mem0-service`'s `/ui` static page ("Memory Manager"),
      backed by `GET /scopes` (cross-filtered user_id/agent_id dropdowns)
      and view/edit/delete/bulk-replace endpoints. Already handles the
      world-lore scope too (pick `user_id=world`, then a world name) since
      it's generic over any user_id/agent_id pair, not roleplay-specific.
- [x] `sillytavern-prod`'s Text Completion connection now has `llamacpp_model:
      gemma-q4` set (was empty) — confirmed directly in its `settings.json`.
- [x] Ollama-era files (`models/Modelfile`, `models/Modelfile.q8`,
      `scripts/test-model.sh`) are kept intentionally, as a reference in
      case of ever falling back to Ollama — not dead weight to clean up.
- [ ] Decide `ollama`'s longer-term fate — currently kept fully defined but
      unused as a zero-risk fallback (see "Architecture" above), not a
      permanent decision.
- [x] World lore layer (mem0-based, replacing ST's native World Info) is
      decided and implemented end-to-end — see "World lore" above.
- [x] World Weaver character imported into dev SillyTavern and verified
      working (interview → `/worlds/interview` → world-scoped memory,
      confirmed via a real transcript).
- [ ] World Weaver character still needs to be imported into **prod**
      SillyTavern manually (`sillytavern/character-cards/world-weaver.json`,
      drag-and-drop into the character panel) — `mem0-prod` and
      `sillytavern-prod` themselves are already rebuilt/restarted with all
      the world-lore code, just missing this one manual step.
- [ ] Known, accepted tradeoff in the world-scoped shared-memory fix (see
      "World lore" above): a genuinely universal fact learned mid-roleplay
      inherits whatever world was bound at write time and stops surfacing
      for unrelated worlds, since tagging happens per-batch, not per-fact.
      Fixing it needs the classifier to also judge per-fact universality —
      left as a deliberate non-fix, not forgotten.
