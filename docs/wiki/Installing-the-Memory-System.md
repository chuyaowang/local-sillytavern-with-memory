# Installing the Memory System

This assumes [Prerequisites](Prerequisites.md) are done: Docker Engine and the NVIDIA Container Toolkit both working.

The steps below add this memory system to an existing SillyTavern installation and a chat connection already configured — whatever backend it already talks to keeps working exactly the same way, with nothing to change there.

No existing SillyTavern, or a fully local setup with zero cloud dependency is wanted instead? See [Local-Only Setup](Local-Only-Setup.md) for a further, optional alternative to the steps below.

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

### If port 8001 is already taken

mem0 needs to be reachable at `localhost:8001` on the host for the memory admin UI and a separately-installed SillyTavern to reach it. If something else on the machine is already using port 8001, bringing mem0 up fails with a clear error (`port is already allocated`).

If the port has been taken, open `docker-compose.yml`, find the `mem0` service's `ports:` line (`"127.0.0.1:8001:8001"`), and change the first `8001` to a free port, for example `"127.0.0.1:18001:8001"`. The second `8001` is the container's own internal port and should stay as is — it is what everything inside Docker keeps using regardless. After changing it, use that new port everywhere `localhost:8001` shows up elsewhere in these docs (the admin UI, and step 4 below if bringing a separate SillyTavern).

After changing it, run `docker compose up -d --force-recreate mem0` for a full restart of the mem0 service.

## 4. Add the plugin and extension to SillyTavern

Copy or symlink two folders from this cloned repo into the existing SillyTavern installation:

- `sillytavern/plugins/roleplay-memory/` → `<SillyTavern install>/plugins/roleplay-memory/`
- `sillytavern/extensions/roleplay-memory/` → `<SillyTavern install>/public/scripts/extensions/third-party/roleplay-memory/`

In that SillyTavern's own `config.yaml`, set `enableServerPlugins: true` and restart it — server plugins only load at startup, so a running instance needs a restart to pick this up.

Then open the copied `plugins/roleplay-memory/index.js` file and change `mem0:8001` near the top to `localhost:8001`, then restart SillyTavern. This setup assumes SillyTavern and mem0 are on the same machine — reaching mem0 from a SillyTavern on a different machine entirely is outside what this guide covers.

## 5. Turn on the extension

In SillyTavern: Manage Extensions tab on the top band → enable **Roleplay Memory**.

That covers it — the existing chat connection is what memory extraction automatically follows (see [Memory System's Automatic model sync section](Memory-System.md#automatic-model-sync)).

## Keeping a separate memory store for testing

Testing changes against the memories used day to day risks polluting real data. Bring up a second, fully isolated memory store instead of the default `qdrant`/`mem0` pair:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d qdrant-prod mem0-prod
```

This shares llama.cpp with the default `qdrant`/`mem0` pair from step 3 (GPU/VRAM is the scarce resource, no reason to load the embedding model twice) but keeps memory data completely separate, reachable at `http://localhost:8011` instead of `:8001`. A single SillyTavern installation can switch between the two stores the same way as above — edit the address in its copied `plugins/roleplay-memory/index.js` and restart it, whenever switching is needed. To avoid editing and restarting every time, set up two separate SillyTavern installations instead, one permanently pointed at `:8001` and the other at `:8011`, and just use whichever one is needed.

The [Local-Only Setup](Local-Only-Setup.md#setting-up-a-dev-vs-prod-environment) bundles its own SillyTavarn instead of relying on an external installation. This makes setting up the plug-in significantly easier and features a shortcut to switch between the development and production environment.
