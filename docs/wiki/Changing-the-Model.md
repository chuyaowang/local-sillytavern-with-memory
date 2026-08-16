# Changing the Model

One model serves both roleplay generation and memory extraction automatically for whichever LLM SillyTavern is connected to (see [Memory System Design](Memory-System.md)). This page is specifically about swapping the bundled local llama.cpp setup's model. If SillyTavern is pointed at a cloud API, changing the model is just picking a different one in SillyTavern's own connection settings; none of the steps below apply. The embedding model is always separate regardless of backend — see [Memory System's Storage section](Memory-System.md#storage) if you want to change it, for example to a multilingual one.

## Steps

1. **Download a GGUF.** Grab one from Hugging Face or other sources. Place it in `models/`. The actual VRAM used is usually smaller than the GGUF file's size as the token embedding table typically stays on CPU.
2. **Add it to `llama-cpp/models-preset.ini`.** Copy an existing `[section]`, give it a new name, and point `model =` at your filename. Keep `ctx-size` at 16384 or higher since mem0's extraction prompt alone is around 8,000 tokens, and a smaller context window silently truncates it and breaks extraction. If VRAM is tight for this quant, reduce `-ngl` to a smaller number (see the `gemma-q8` section's comments for the `--fit`/`n-gpu-layers` tradeoff).
3. **Restart llama.cpp** so it picks up the new preset:

   ```bash
   docker compose restart llama-cpp
   ```

4. **Test it before committing to it:**

   ```bash
   ./scripts/test-model-llama-cpp.sh <new-model-name>
   ```

   Runs three checks against a throwaway setup, leaving your real containers and data untouched:
   - **Text generation:** a real completion request against the router, confirming the model loads and produces output at all.
   - **VRAM footprint:** measured via a container restart, so it reflects actual resident usage.
   - **mem0 extraction:** tests real memory extraction and if the extracted json format is correct.

   Pick a different model if anything fails.
5. **Point SillyTavern at it.** Go to API Connections, re-pick the new model from the dropdown (same place as [Local-Only Setup](Local-Only-Setup.md)'s "Point SillyTavern at llama.cpp" step). mem0 follows automatically. It reads whichever model SillyTavern is actually using on every request, so there's no separate mem0 config to update.

## Models checked so far

Results from running `scripts/test-model-llama-cpp.sh` against each model, for reference:

| Model | Text generation | VRAM footprint (actual) | Extraction JSON syntax | Extraction pipeline |
| --- | --- | --- | --- | --- |
| [Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf) | Pass | ~3.3 GB | Pass | Pass |
| [Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P](https://huggingface.co/HauhauCS/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive/blob/main/Gemma-4-E4B-Uncensored-HauhauCS-Aggressive-Q8_K_P.gguf) | Pass | ~4.7-5.7 GB | Pass | Pass |

Q8 fits but needs to off-load some computation to the CPU when the embedder also needs VRAM, which reduces text generation speed. See [docs/LLAMA_CPP_BENCHMARK.md](https://github.com/chuyaowang/local-sillytavern-with-memory/blob/main/docs/LLAMA_CPP_BENCHMARK.md) for the full story. Q4 is the safer everyday default.
