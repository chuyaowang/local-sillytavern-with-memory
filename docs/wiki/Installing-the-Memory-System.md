# Installing the Memory System

This assumes [Prerequisites](Prerequisites.md) are done: Docker Engine and the NVIDIA Container Toolkit both working.

The steps below add this memory system to a SillyTavern that is already running, with a chat connection already configured — whatever backend it already talks to keeps working exactly the same way, with nothing to change there. No existing SillyTavern, or a fully local setup with zero cloud dependency is wanted instead? See [Local-Only Setup](Local-Only-Setup.md) for that, as a further, optional alternative to the steps below.

## 1. Clone the repo

```bash
git clone https://github.com/chuyaowang/local-sillytavern-with-memory.git
cd local-sillytavern-with-memory
```

Needed regardless of setup — this is where the plugin, extension, and memory service source live.

## 2. Get the embedding model

mem0 uses this to turn memories into vectors for Qdrant, and needs it regardless of which backend handles chat:

```bash
curl -L -o models/nomic-embed-text-v1.5.f16.gguf \
  https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.f16.gguf
```

## 3. Bring up the memory services

```bash
docker compose up -d qdrant mem0 llama-cpp
```

llama.cpp here only serves the embedding model from the previous step — it is not doing any chat generation in this setup. A different embedding endpoint can be used instead if a local one is not wanted either — see [Memory System's Storage section](Memory-System.md#storage).

## 4. Add the plugin and extension to SillyTavern

Copy or symlink two folders from this cloned repo into the existing SillyTavern installation:

- `sillytavern/plugins/roleplay-memory/` → `<SillyTavern install>/plugins/roleplay-memory/`
- `sillytavern/extensions/roleplay-memory/` → `<SillyTavern install>/public/scripts/extensions/third-party/roleplay-memory/`

In that SillyTavern's own `config.yaml`, set `enableServerPlugins: true` and restart it — server plugins only load at startup, so a running instance needs a restart to pick this up.

The plugin reaches mem0 over the `MEM0_URL` environment variable, which defaults to `http://mem0:8001` (the address it resolves to inside this repo's own Docker network). If SillyTavern is a separate installation running on the same machine, set `MEM0_URL=http://localhost:8001` in the environment it runs in. mem0 is bound to localhost only, by design, so this setup assumes SillyTavern and mem0 are on the same machine — reaching mem0 from a SillyTavern on a different machine entirely is outside what this guide covers.

## 5. Turn on the extension

In SillyTavern: Manage Extensions tab on the top band → enable **Roleplay Memory**.

That covers it — the existing chat connection is what memory extraction automatically follows (see [Memory System's Automatic model sync section](Memory-System.md#automatic-model-sync)).

## Keeping a separate memory store for testing

Testing changes against the memories used day to day risks polluting real data. Bring up a second, fully isolated memory store instead of the default `qdrant`/`mem0` pair:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d qdrant-prod mem0-prod
```

This shares llama.cpp with the primary store (GPU/VRAM is the scarce resource, no reason to load the embedding model twice) but keeps memory data completely separate, reachable at `http://localhost:8011` instead of `:8001`. Point the plugin's `MEM0_URL` at whichever one is wanted for a given SillyTavern instance, or run two SillyTavern installations, each pointed at its own.

This project also bundles its own second SillyTavern container (`sillytavern-prod`) alongside `qdrant-prod`/`mem0-prod`, and a `make prod-up`/`make dev-up` shortcut that brings up all three together — see [Local-Only Setup](Local-Only-Setup.md#setting-up-a-dev-vs-prod-environment) if using the bundled SillyTavern instead of a separate installation, since that is where a second bundled SillyTavern container is actually useful.
