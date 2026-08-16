# Benchmarking

Two one-off benchmarks back the backend and embedding model choices used elsewhere in this wiki. This page is a short summary with the key numbers and conclusions — the linked reports have the full methodology and raw data.

## Backend: llama.cpp vs. Ollama

The bundled local stack runs its model through llama.cpp. On the same GPU and the same model, llama.cpp generated text about 12% faster than Ollama, at a VRAM cost within 30 MiB of Ollama's — close enough to call a tie. Cold-start and time-to-first-token were roughly tied between the two as well. llama.cpp was kept for the throughput edge, plus its ability to serve more than one model from a single running process instead of needing a separate one per model.

Full numbers and methodology: [llama.cpp vs. Ollama benchmark](https://github.com/chuyaowang/local-sillytavern-with-memory/blob/main/docs/LLAMA_CPP_BENCHMARK.md).

## Embedding model: nomic-embed-text-v1.5 vs. two multilingual alternatives

The bundled embedding model, nomic-embed-text-v1.5, only supports English (see [Memory System](Memory-System.md#storage)). Two multilingual alternatives were tested against it: nomic-embed-text-v2-moe and EmbeddingGemma.

The benchmark used statistical tests to measure how well each model tells a fact apart from its own contradiction, tells an unrelated fact apart from one on the same topic, and separates a paraphrased memory from a contradicting one. None of the three models showed a statistically demonstrated advantage over the others on any of these three measures — the differences between them were consistent with random variation given the sample size. nomic-embed-text-v2-moe also accepts far less text per embedding request than the other two, a hard limit from how it was trained, and this memory system's own retrieval step already produces requests long enough to exceed that limit in ordinary use.

Since no model showed a demonstrated quality edge, nomic-embed-text-v1.5 stays the default for an English-only setup — switching would only add VRAM cost with no established benefit. For a multilingual setup, EmbeddingGemma is the recommended alternative over nomic-embed-text-v2-moe, purely on that input-length limit — a real practical constraint on top of an otherwise even result.

Full numbers, test design, and statistical methodology: [Embedding model benchmark](https://github.com/chuyaowang/local-sillytavern-with-memory/blob/main/docs/EMBEDDING_MODEL_BENCHMARK.md).
