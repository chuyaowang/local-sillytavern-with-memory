# Local-Only Setup

Nothing existing to add the memory system to, or a fully local setup with zero cloud dependency is wanted instead? This repo bundles a complete local stack — a local model through llama.cpp and SillyTavern itself, both wired up automatically — as an alternative to [Installing the Memory System](Installing-the-Memory-System.md). This assumes [Prerequisites](Prerequisites.md) are done: Docker Engine and the NVIDIA Container Toolkit both working.

llama.cpp was picked over Ollama for a measured generation-speed edge at comparable VRAM — see [Benchmarking](Benchmarking.md#backend-llamacpp-vs-ollama) for the numbers.

## 1. Clone the repo and bring your own model

```bash
git clone https://github.com/chuyaowang/local-sillytavern-with-memory.git
cd local-sillytavern-with-memory
```

The model files are not in this repo (they are multi-gigabytes). Drop one in `models/`, then point `llama-cpp/models-preset.ini`'s `model =` line at its filename.

The default model, tested to work, is [a quantized and abliterated Gemma 4 E4B model](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf), which fits in the 6GB VRAM of an NVIDIA GTX 1660Ti card. The preset's `ctx-size` needs to stay at 16384 or higher — the memory extraction prompt alone is around 8,000 tokens, and a smaller context window will silently truncate it and break extraction (see [Memory System](Memory-System.md) for why).

See [Changing the Model](Changing-the-Model.md) if you want to switch to a different one later.

## 2. Set up SillyTavern's config

Copy the template and fill in your own values:

```bash
cp sillytavern/config/config.yaml.example sillytavern/config/config.yaml
```

Edit that file and set a real `basicAuthUser.username`/`password`. Add Tailscale IPs to the `whitelist` array if reaching this from another device — see [Remote Access](Remote-Access.md).

## 3. Get the embedding model

```bash
curl -L -o models/nomic-embed-text-v1.5.f16.gguf \
  https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.f16.gguf
```

## 4. Bring up everything

```bash
docker compose up -d
```

## 5. Point SillyTavern at llama.cpp

In the SillyTavern UI: API Connections → API type **Text Completion**, source **llama.cpp**, Server URL `http://llama-cpp:8080`, then pick your model from the dropdown.

## 6. Turn on the memory extension

In SillyTavern: Manage Extensions tab on the top band → enable **Roleplay Memory**. This wires the chat up to mem0.

## Where things are once it's running

- SillyTavern: `http://localhost:8000` locally, or `http://<tailscale-ip-of-host>:8000` from a whitelisted device over Tailscale (see [Remote Access](Remote-Access.md)).
- Memory manager UI, for browsing/editing/deleting memories by hand: `http://localhost:8001/ui/`, local only. See [Installing the Memory System](Installing-the-Memory-System.md#if-port-8001-is-already-taken) if this port is already in use by something else.
- Raw mem0 API docs, for debugging: `http://localhost:8001/docs`.
- Qdrant dashboard, for debugging: `http://localhost:6333/dashboard`.

## Setting up a dev vs. prod environment

By default you have one stack: `qdrant`, `mem0`, `sillytavern`. That's fine for casual use, but testing changes against the memories and chats you actually use day to day risks polluting your real data. `docker-compose.prod.yml` adds a second, fully isolated stack (`qdrant-prod`, `mem0-prod`, `sillytavern-prod`) for developing or trying things out safely. The two stacks share llama.cpp (GPU/VRAM is the scarce resource, no reason to load the model twice) and the SillyTavern plugin/extension source code. Config, memory data, and chat history are completely separate.

### Creating the prod stack

1. Create the prod SillyTavern config:

   ```bash
   cp sillytavern-prod/config/config.yaml.example sillytavern-prod/config/config.yaml
   ```

   Edit it the same way as the dev config above: its own `basicAuthUser.username`/`password` and Tailscale whitelist.
2. Bring up the prod stack:

   ```bash
   make prod-up
   ```

   Docker creates `sillytavern-prod/data/` automatically on first run, and SillyTavern populates it with its own fresh defaults. Chats and characters from dev stay in dev.

### Switching between them

The Makefile wraps the underlying `docker compose -f docker-compose.yml -f docker-compose.prod.yml ...` commands so you don't have to remember them:

| Command | What it does |
| --- | --- |
| `make dev-up` | Start the dev stack (`qdrant`, `mem0`, `sillytavern`), plus llama.cpp if it isn't already running |
| `make dev-down` | Stop the dev stack, leave llama.cpp and prod running |
| `make prod-up` | Start the prod stack, plus llama.cpp if it isn't already running |
| `make prod-down` | Stop the prod stack, leave llama.cpp and dev running |
| `make down` | Stop everything that's running |
| `make status` | Show what's currently running |

`make dev-up` and `make prod-up` each print that stack's SillyTavern and memory-manager URLs once it's up: dev on `:8000`/`:8001`, prod on `:8010`/`:8011`. Run bare `make` to see this list at any time.
