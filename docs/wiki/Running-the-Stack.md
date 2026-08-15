# Running the Stack

Assumes [Prerequisites](Prerequisites.md) are done — Docker Engine, the NVIDIA
Container Toolkit, and (optionally) Tailscale all working.

## 1. Clone the repo

```bash
git clone https://github.com/chuyaowang/local-sillytavern-with-memory.git
cd local-sillytavern-with-memory
```

## 2. Bring your own model

The model files aren't in this repo (they're multi-gigabytes). Drop one in
`models/`, then point `llama-cpp/models-preset.ini`'s `model =` line at its
filename.

The default model, tested to work, is
[a quantized and abliterated Gemma 4 E4B model](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf),
which fits in the 6GB VRAM of an NVIDIA GTX 1660Ti card. The preset's `ctx-size`
needs to stay at 16384 or higher — the memory extraction prompt alone is around
8,000 tokens, and a smaller context window will silently truncate it and break
extraction (see [Memory System](Memory-System.md) for why).

See [Changing the Model](Changing-the-Model.md) if you want to switch to a
different one later.

## 3. Set up SillyTavern's config

Copy the template and fill in your own values:

```bash
cp sillytavern/config/config.yaml.example sillytavern/config/config.yaml
```

Edit that file and set a real `basicAuthUser.username`/`password`, and add the
actual Tailscale IPs of whatever devices should be able to reach it to the
`whitelist` array (`tailscale status` shows you those IPs).

## 4. Get the embedding model

mem0 uses this to turn memories into vectors for Qdrant. Download it straight into
`models/`:

```bash
curl -L -o models/nomic-embed-text-v1.5.f16.gguf \
  https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.f16.gguf
```

## 5. Bring up everything

```bash
docker compose up -d
```

## 6. Point SillyTavern at llama.cpp

In the SillyTavern UI: API Connections → API type **Text Completion**, source
**llama.cpp**, Server URL `http://llama-cpp:8080`, then pick your model from the
dropdown.

## 7. Turn on the memory extension

In SillyTavern: Manage Extensions tab on the top band → enable **Roleplay
Memory**. This is what actually wires the chat up to mem0.

## Where things are once it's running

- SillyTavern: `http://localhost:8000` locally, or `http://<tailscale-ip-of-host>:8000`
  from a whitelisted device over Tailscale.
- Memory manager UI (browse/edit/delete memories by hand): `http://localhost:8001/ui/`
  — local only.
- Raw mem0 API docs: `http://localhost:8001/docs` — debugging only.
- Qdrant dashboard: `http://localhost:6333/dashboard` — debugging only.

## Setting up a dev vs. prod environment

By default you have one stack — `qdrant`, `mem0`, `sillytavern` — which is fine if
you're not planning to test changes against the memories and chats you actually use
day to day. `docker-compose.prod.yml` adds a second, fully isolated stack
(`qdrant-prod`, `mem0-prod`, `sillytavern-prod`) so you can develop or try things out
without touching your real data. The two stacks share only llama.cpp (GPU/VRAM is
the scarce resource, no reason to load the model twice) and the SillyTavern
plugin/extension source code; everything else — config, memory data, chat history —
is completely separate.

### Creating the prod stack

1. Create the prod SillyTavern config:

   ```bash
   cp sillytavern-prod/config/config.yaml.example sillytavern-prod/config/config.yaml
   ```

   Edit it the same way as the dev config above — its own
   `basicAuthUser.username`/`password` and Tailscale whitelist.
2. Bring up the prod stack:

   ```bash
   make prod-up
   ```

   Docker creates `sillytavern-prod/data/` automatically on first run, and
   SillyTavern populates it with its own fresh defaults — nothing from dev (chats,
   characters) carries over.

### Switching between them

The Makefile wraps the underlying
`docker compose -f docker-compose.yml -f docker-compose.prod.yml ...` commands so
you don't have to remember them:

| Command | What it does |
| --- | --- |
| `make dev-up` | Start the dev stack (`qdrant`, `mem0`, `sillytavern`), plus llama.cpp if it isn't already running |
| `make dev-down` | Stop the dev stack, leave llama.cpp and prod running |
| `make prod-up` | Start the prod stack, plus llama.cpp if it isn't already running |
| `make prod-down` | Stop the prod stack, leave llama.cpp and dev running |
| `make down` | Stop everything that's running |
| `make status` | Show what's currently running |

`make dev-up` and `make prod-up` each print that stack's SillyTavern and
memory-manager URLs once it's up — dev on `:8000`/`:8001`, prod on `:8010`/`:8011`.
Run bare `make` to see this list at any time.