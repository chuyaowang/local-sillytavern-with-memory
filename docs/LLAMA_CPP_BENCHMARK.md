# llama.cpp vs. Ollama benchmark

A one-off comparison of raw inference performance between the two backends
serving the same GGUF, run to decide which one to keep using. Not a
permanent feature — see `docker-compose.yml`'s `llama-cpp` service (Compose
profile `llama-cpp`, off by default) and `scripts/bench-llama-cpp.sh` /
`scripts/bench-ollama.sh` (steady-state generation speed) /
`scripts/bench-model-switch.sh` (model-switch speed) if you want to re-run
any of it.

## Setup

- **Model**: `Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf`
  (~5.3 GB), the same file both backends load — `models/Modelfile` for
  Ollama, mounted read-only for llama.cpp.
- **GPU**: NVIDIA GeForce GTX 1660 Ti, 6 GB VRAM, driver 580.173.02, CUDA
  13.0.
- **llama.cpp**: `ghcr.io/ggml-org/llama.cpp:server-cuda13`, `--jinja -c
  16384 -ngl 99 --cache-type-k q8_0 --cache-type-v q8_0`.
- **Ollama**: existing `gemma4-e4b-hauhaucs` model, `num_ctx 16384`,
  `OLLAMA_KV_CACHE_TYPE=q8_0` — same KV cache quantization as llama.cpp
  above, so the two are on equal footing there.
- **Sampling** (both): temperature 1.0, top_p 0.95, top_k 64 — matches
  `models/Modelfile`.
- **Test**: a fixed 3-turn roleplay conversation (growing message history
  each turn, 256 tokens generated per turn), not a repeated single prompt —
  meant to reflect real chat-session behavior including whatever prefix/KV
  caching each backend does on its own.
- Only one backend held the model in VRAM at a time (6 GB card can't fit
  both).
- The drive holding this whole project turned out to be an external SSD
  that had been plugged into a USB 2.0 port, capping every disk read well
  below what the SSD itself can do — see "Model switching" below for how
  that was found and fixed. All numbers here are from after that fix.

## Results

### Cold start (model load / container ready)

| | llama.cpp | Ollama |
|---|---|---|
| Time to ready | 14.0s (container restart) | 10.7s (model load, Ollama's own `load_duration`) |

Close enough to call it a tie once the disk itself isn't the bottleneck.
llama-server holds the model resident in one long-running process, so a
restart mostly just means reading the file again; Ollama spawns a fresh
runner subprocess per load and pays a similar cost each time. The two
architectures still behave differently under repeated switching, though —
see "Model switching" below, where llama.cpp's very first request after
starting up pays a one-time cost Ollama doesn't.

### Time to first token (turns 1-3)

| Turn | llama.cpp | Ollama |
|---|---|---|
| 1 | 501ms (64 prompt tokens, 0 cached) | 496ms (80 prompt tokens) |
| 2 | 365ms (83 prompt tokens, 59 cached) | 211ms (117 prompt tokens) |
| 3 | 517ms (113 prompt tokens, 137 cached) | 659ms (251 prompt tokens) |

Both land in the same few-hundred-millisecond range turn to turn; neither
grows smoothly with conversation length in this run. llama.cpp's response
reports explicit prefix-cache hits (`timings.cache_n`), so its "new prompt
tokens" count stays much smaller than the conversation's real length even
as turns pile up (137 tokens reused by turn 3, out of a much longer total
conversation) — Ollama doesn't expose an equivalent number, so it's not
possible to tell from these numbers alone how much reuse it's doing
internally.

### Generation throughput

| Turn | llama.cpp | Ollama |
|---|---|---|
| 1 | 53.0 tok/s | 47 tok/s |
| 2 | 52.8 tok/s | 47 tok/s |
| 3 | 52.4 tok/s | 47 tok/s |

llama.cpp generated tokens about 12% faster than Ollama on this model/GPU,
consistently across all three turns.

### VRAM

| Turn | llama.cpp | Ollama |
|---|---|---|
| 1 | 3347-3359 MiB | 3325 MiB |
| 2 | 3359 MiB | 3325 MiB |
| 3 | 3359 MiB | 3325-3327 MiB |

With the KV cache quantization matched on both sides, the two are within
about 30 MiB of each other — a difference small enough to not be worth
explaining further.

### Model switching (Q4 <-> Q8)

Measured separately, in `scripts/bench-model-switch.sh`. Both backends can
switch models without restarting their container: llama.cpp via its router
mode (`--models-dir`, loads/evicts models on demand), Ollama by just
requesting a different model tag. The 6 GB card can't hold both Q4 (5.4 GB)
and Q8 (8.1 GB) at once, so every switch really does evict one model and
load the other from disk.

An early run of this test turned up an unrelated but important fact about
this machine: the drive holding this whole project (and the OS itself) is
an external SSD connected over USB, and it had been plugged into a USB 2.0
port — capping every read at around 35-40 MB/s no matter how fast the SSD
itself is. Moving it to a USB 3.x port fixed that. The numbers below are
from after that fix:

| Switch | llama.cpp | Ollama |
|---|---|---|
| Fresh load Q4 | 80.6s | 11.6s |
| Q4 → Q8 | 19.0s | 21.5s |
| Q8 → Q4 | 10.4s | 12.9s |

llama.cpp's first number stands out: 80.6 seconds to load Q4, but only 19.0
and 10.4 seconds for the two switches after it — even though one of those
switches is loading the *larger* Q8 file. If the 80.6s were mostly about
reading the file off disk, Q8 should have taken longer than Q4, not
shorter. So most of that first number isn't disk time at all — it's a
one-time cost (CUDA context setup, GPU kernel selection) that only happens
once per container, the first time it actually runs an inference, not once
per model switch. Ollama doesn't show this pattern because it spawns a
brand new subprocess for every single model load, switch or not, so it pays
a similar setup cost every time rather than just once — which is also why
its three numbers scale roughly with file size (Q8 taking longer than Q4)
instead of having one outlier.

Ollama's numbers here come with one caveat: `ollama create` copies the GGUF
into its own blob store inside a Docker-managed volume, and that volume's
files are owned by root, so the benchmark script can't evict them from the
OS page cache the way it does for llama.cpp's files (which it reads
directly and does own). If any of the three reads happened to hit a cache
left over from an earlier read, that specific number would look faster than
a genuinely fresh load — the scaling with file size and load_duration
numbers here are consistent enough with real disk reads to trust, but this
is an acknowledged gap in how cleanly the two backends were measured.

## mem0 integration

`scripts/test-model-llama-cpp.sh` (adapted from `scripts/test-model.sh`)
checks whether llama.cpp can serve as mem0's backend for both the LLM and
the embedder — an all-llama.cpp pipeline, no Ollama in the loop at all.
mem0-service now supports this via `MEM0_LLM_PROVIDER=openai` /
`MEM0_EMBEDDER_PROVIDER=openai`, pointing at llama.cpp's OpenAI-compatible
API instead of Ollama's own (`mem0-service/main.py`). The embedder needs
its own llama-server process — a chat model and an embedding model can't
share one — running `models/nomic-embed-text-v1.5.f16.gguf`, downloaded
from Hugging Face (`nomic-ai/nomic-embed-text-v1.5-GGUF`) and confirmed to
be the exact same model Ollama uses (matching file size against Ollama's
own blob for it).

Result: it works. Both the extraction JSON-syntax check and the end-to-end
extraction pipeline check passed, producing the same kind of sensible
extracted facts mem0 produces with Ollama.

One real finding along the way: this model emits a separate "thinking"
block (`reasoning_content`) before its actual reply, and at llama-server's
bare default sampling settings, it sometimes spent the *entire* token
budget on that reasoning and never produced a reply at all — even at 512
tokens. Using the project's actual sampling settings (`models/Modelfile`:
temperature 1.0, top_p 0.95, top_k 64) fixed it reliably. Worth knowing if
this model is ever pointed at through a client that doesn't set those —
llama-server's own defaults aren't safe for it.

## Production shape: one router service, not two

Confirmed by starting a real router-mode container with a `--models-preset`
INI file listing both the chat model and the embedding model, then loading
both at once and querying each — they coexisted fine, `GET /models` showed
both `"loaded"`, combined VRAM was 3677 MiB (well under the 6 GB card).
Preset format (one `[section]` per model, keys match CLI flag names without
the leading dashes):

```ini
[gemma-q4]
model = /models/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf
jinja = 1
ctx-size = 16384
n-gpu-layers = 99
temp = 1.0
top-p = 0.95
top-k = 64
cache-type-k = q8_0
cache-type-v = q8_0

[gemma-q8]
model = /models/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf
jinja = 1
ctx-size = 16384
n-gpu-layers = 99
temp = 1.0
top-p = 0.95
top-k = 64
cache-type-k = q8_0
cache-type-v = q8_0

[nomic-embed]
model = /models/nomic-embed-text-v1.5.f16.gguf
embeddings = 1
ctx-size = 2048
n-gpu-layers = 99
```

Run with `--models-preset preset.ini --models-max 2` — not 3: Q4 (~3.3 GB)
and Q8 (~5+ GB) can't both fit alongside the embedder on a 6 GB card, so
`--models-max 2` keeps the embedder resident (touched on every mem0 call)
plus whichever chat model was used most recently, and LRU-evicts the other
chat model automatically when you switch. That's the intended behavior,
not a limitation — switching models *is* supposed to evict the old one.

This replaces the earlier "separate `llama-cpp` + `llama-cpp-embed`
services" idea in the integration punch list — one router-mode service with
this preset covers the LLM (both quants) and the embedder together.

### Switching Q4 <-> Q8 from SillyTavern

Confirmed directly in SillyTavern's own source
(`public/scripts/textgen-models.js`, `public/scripts/textgen-settings.js`
inside the `sillytavern` container): the Text Completion "llama.cpp" source
already has full native model-switching support, unrelated to anything
built for this project. `loadLlamaCppModels()` populates a `#llamacpp_model`
dropdown from the connected server's model list (`GET /models` — the exact
endpoint router mode exposes), and the selected value is sent as `model` in
every generation request (`textgen-settings.js`'s `getModel()`, `LLAMACPP`
case). With the preset above running, that dropdown would show `gemma-q4`,
`gemma-q8`, and `nomic-embed`, and picking `gemma-q4` or `gemma-q8` there
switches the roleplay model live — the router server loads/evicts on that
request the same way it did in testing (a request each way after the very
first taking roughly 10-20s once the container's already up, per "Model
switching" above).

## Raw data

Full JSON responses (every `timings`/duration field, per turn, per backend)
are saved by the benchmark scripts to `scripts/bench-results/` — gitignored,
regenerated each run.