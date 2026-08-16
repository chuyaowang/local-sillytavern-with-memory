# Embedding model benchmark: same-fact vs. opposite-fact separation

A one-off comparison of the embedding model this project actually uses against two multilingual alternatives already documented as swap-in options (`docs/wiki/Memory-System.md`'s "Storage" section), run to check whether switching to either alternative would also improve retrieval quality. Not a permanent feature — see `scripts/bench-embedding-models.py`, `scripts/stat-test-embedding-gaps.py`, and `scripts/embedding-bench-cases.json` if you want to re-run or extend it.

## Background

**How mem0 extracts and deduplicates.** Every `Memory.add()` call with inference enabled runs a single LLM call (mem0's "V3 phased batch pipeline", confirmed by reading `mem0/memory/main.py`'s `_add_to_vector_store` directly), not a separate compare-against-existing decision call. Before that call, mem0 vector-searches the same `user_id`/`agent_id`/`run_id` scope for the 10 most similar existing memories and includes them in the extraction prompt as `Existing Memories`. The prompt (`ADDITIVE_EXTRACTION_PROMPT`) instructs the model to skip anything "semantically equivalent to an Existing Memory with no meaningful new context," and to record a link (`linked_memory_ids`) back to an existing memory when a new one is the same entity/topic, an updated preference, a continuation, or **a contradiction**. So a fact and its opposite are recognized as related at extraction time — but only ever linked, never merged, deleted, or marked as superseding one another. Both stay in the store as independent memories.

After the LLM call, whatever it extracted gets one more, much cruder pass: each extracted text is MD5-hashed and compared against hashes of the same 10 existing memories plus everything else already emitted in that batch. A hash match (an exact, character-for-character repeat) gets silently dropped. This catches literal re-extraction only — a paraphrase or a contradiction never matches a hash, so this layer has no bearing on either.

Net effect: mem0's dedup is LLM judgment (semantic) plus exact-text hashing (literal). Nothing in the write path makes a keep/skip decision from embedding distance directly.

**How retrieval scores results.** `Memory._search_vector_store` combines three signals, not embedding similarity alone: cosine similarity from the vector store (over-fetched to a pool of `max(limit * 4, 60)` candidates), a BM25 keyword score computed from lemmatized text, and an entity boost — named entities extracted from the query get matched against a separate entity-store collection, and any memory linked to a matched entity receives a bounded boost (`ENTITY_BOOST_WEIGHT`, capped contribution of 0.5). All three combine in `score_and_rank()` before the top-k results are returned. No reranking model runs unless one is explicitly configured (off by default).

Before those three signals combine, a gate runs first. `Memory.search()` takes a `threshold` argument (default 0.1) and passes it straight into `score_and_rank()`, which checks each candidate's raw cosine similarity score against it — `if semantic_score < threshold: continue` — and drops anything that falls short. This happens *before* BM25 or the entity boost get added in, so a candidate with a weak embedding match gets excluded outright regardless of how strong its keyword or entity match is; only candidates that clear the embedding-similarity floor go on to have those other two signals folded in and get ranked.

**How opposite facts are handled, end to end.** At extraction, a contradiction is recognized and *linked* — the `linked_memory_ids` field on the new memory records the older one's id. Nothing about that link changes what gets stored: the entity store itself doesn't encode polarity either — it indexes named entities to whichever memories mention them, so "the user likes coffee" and "the user dislikes coffee" both attach to the same "coffee" entity with no distinction between confirming and contradicting. At retrieval, none of the three scoring signals (embedding similarity, BM25, entity boost) carries a polarity term — an opposite fact is scored purely on topical/lexical/entity overlap, identical to how a confirming fact would be scored. So whether a stale, contradicted fact and its correction both surface for a given query — and how closely ranked they are — comes down entirely to how far apart the embedding model itself places "same meaning" from "opposite meaning" in vector space. Nothing downstream in mem0 corrects for it.

## Motivation

This project's `mem0-service` (see `CLAUDE.md`'s "Memory (mem0 + Qdrant)" section) reads whatever the embedding model reports as similar and injects it into the roleplay prompt as-is. Since nothing in mem0's own pipeline resolves a contradiction (see "Background" above), the embedding model is the only place a stale fact could end up ranked below its correction. That motivates three distinct, testable questions, each tied to a concrete practical concern for this project:

1. **Fact vs. opposite fact, in a real retrieval query.** If a query about a topic pulls a fact and its direct contradiction in almost equally close, retrieval can't tell a corrected fact from the stale one it replaced — for example, an earlier preference change ("used to like coffee, now doesn't") risking both scores surfacing with little to distinguish them by relevance.
2. **On-topic vs. off-topic, in a real retrieval query.** How cleanly a model separates a same-topic-but-different fact from something with no topical overlap at all determines how much headroom mem0's `search()` threshold (0.1 by default, see "Background") actually has — a model that pushes unrelated content much lower than related content gives that gate real work to do; one that doesn't makes the threshold nearly useless for filtering.
3. **Paraphrase vs. opposite, memory text against memory text (no query involved).** This project's admin UI (`mem0-service/static/index.html`) already supports manually moving a memory between scopes; a natural next feature would propose likely duplicate or contradicting memory pairs for the user to review, unsupervised, from a similarity matrix. That only works if a model's own embeddings reliably place a paraphrase closer to its source fact than a contradiction does — this checks whether that holds for each candidate model.

Each question reduces to one number per case per model: a gap between two similarity scores. That gap is tested at two levels — whether it's real within a given model (a one-sample t-test against zero), and whether the size of that gap actually differs between models (a one-way ANOVA). See "Methods" for why both levels matter separately.

## Methods

**Models tested:**

| Model | File | Parameters | Architecture |
|---|---|---|---|
| nomic-embed-text-v1.5 (current) | `nomic-embed-text-v1.5.f16.gguf` | 137M | nomic-bert |
| nomic-embed-text-v2-moe | `nomic-embed-text-v2-moe.Q8_0.gguf` | 475M | nomic-bert-moe |
| EmbeddingGemma-300M | `embeddinggemma-300M-Q8_0.gguf` | 303M | gemma-embedding |

All three run from `models/`, loaded one at a time via a throwaway `llama-cpp:server-cuda13` container (`--embeddings`, GPU, `-ngl 99`) — the 6 GB card can't hold more than one alongside the roleplay model, so each run stops and fully removes the previous container before starting the next. VRAM footprint is the `nvidia-smi` memory delta between just before the container starts and right after `/health` reports ready (idle-loaded, no request sent yet), so it includes each model's own per-process CUDA context overhead, not just its weights. Max tokens accepted is each model's *trained* context length, read directly from the GGUF file's own metadata (`<architecture>.context_length`, parsed straight from the binary header, not the `--ctx-size` flag the server happened to be started with). Embedding dimensions come from the length of a real returned embedding vector.

**Synthetic test data** (`scripts/embedding-bench-cases.json`): 9 base memories spanning all three of this project's actual memory scopes — 3 character-scoped (relationship/history between two named characters), 3 shared-scoped (general facts about the user), 3 world-scoped (setting lore) — written in the same style mem0's extraction prompt actually produces, using this project's own example cast (Nova, Seraphina, Amber, Arthuria, Cynthia; worlds Eldoria, Camelot) for realism rather than generic placeholder text. Each base memory is paired with four hand-written variants (**paraphrase** — same fact, reworded; **opposite** — direct contradiction; **same_topic_different_fact** — related subject, a distinct fact; **unrelated** — no topical overlap) and a **query** — a short, in-conversation roleplay line that raises the base memory's topic without restating the fact itself, written in the same dialogue-driven style a real SillyTavern conversation turn takes. For example, the `user-coffee` case's base fact ("The user likes to drink coffee every morning before work") pairs with the query "Seraphina offers to make the user a hot drink before they head out for the day."

`scripts/bench-embedding-models.py` runs two comparisons per case per model: **memory-to-memory** (the base fact embedded against each of its four variants — feeds Test 3) and **query-to-memory** (the query embedded against the base fact and all four variants — feeds Tests 1 and 2, and matches how `Memory.search()` is actually invoked in production, embedding a conversation turn rather than one memory text). Both use the model's own `/v1/embeddings` endpoint and cosine similarity. Per-category mean and standard deviation are taken across the 9 cases; full per-case scores are saved to `scripts/bench-results/embedding-models.json`.

**Statistical testing** (`scripts/stat-test-embedding-gaps.py`): each of the three tests in "Motivation" reduces to a per-case gap between two score columns (e.g., Test 1's gap is `base − opposite` from the query-to-memory scores). Two checks run on that gap, per test:

- **Within-model**: a one-sample t-test (`scipy.stats.ttest_1samp`) of a given model's 9 per-case gaps against 0 — does this model separate the two categories at all, on its own, regardless of the other two models.
- **Between-model**: a one-way ANOVA (`scipy.stats.f_oneway`) across the three models' gap arrays — do the models differ *from each other* in how well they achieve that separation. A Tukey HSD post-hoc test only follows when that ANOVA is significant (p < 0.05); running pairwise comparisons after a non-significant omnibus test has nothing to follow up on.

These answer different questions and can disagree — a gap can be real and consistent within every model individually while the models are still statistically indistinguishable from each other in how large that gap is. Both are reported for each test below. With only 9 cases per model, the within-model tests are the more informative signal.

## Results

### Model specs

| Model | VRAM footprint | Max tokens | Dimensions |
|---|---|---|---|
| nomic-embed-text-v1.5 | 320 MiB | 2048 | 768 |
| nomic-embed-text-v2-moe | 390 MiB | 512 | 768 |
| EmbeddingGemma-300M | 408 MiB | 2048 | 768 |

All three return 768-dimensional vectors. v2-moe's 512-token trained context is a hard ceiling from how the model was trained, not a config choice. A single extracted memory (mem0's own length guideline caps one at 15-80 words, up to 100 for detail-rich content — roughly 20-130 tokens) fits comfortably within it. The tighter case is mem0's existing-memory retrieval step, which embeds the raw new-message batch being processed for extraction rather than a short stored fact — this project has already measured a real flush reaching 1315 tokens (`llama-cpp/models-preset.ini`'s `batch-size`/`ubatch-size` fix), well past v2-moe's ceiling.

### Descriptive scores

Memory-to-memory (mean ± stdev, n=9):

| Model | Paraphrase | Opposite | Same-topic-different-fact | Unrelated |
|---|---|---|---|---|
| nomic-embed-text-v1.5 | 0.9314 ± 0.0306 | 0.8612 ± 0.0399 | 0.7818 ± 0.0577 | 0.4246 ± 0.0554 |
| nomic-embed-text-v2-moe | 0.8687 ± 0.0420 | 0.7586 ± 0.0780 | 0.6367 ± 0.0887 | 0.1831 ± 0.0560 |
| EmbeddingGemma-300M | 0.8798 ± 0.0321 | 0.7449 ± 0.0707 | 0.6523 ± 0.0798 | 0.2539 ± 0.0502 |

Query-to-memory (mean ± stdev, n=9):

| Model | Base | Paraphrase | Opposite | Same-topic-different-fact | Unrelated |
|---|---|---|---|---|---|
| nomic-embed-text-v1.5 | 0.7265 ± 0.0975 | 0.7310 ± 0.0916 | 0.6820 ± 0.1154 | 0.6513 ± 0.0747 | 0.4473 ± 0.0736 |
| nomic-embed-text-v2-moe | 0.5779 ± 0.1250 | 0.5854 ± 0.1287 | 0.5497 ± 0.1355 | 0.4688 ± 0.1194 | 0.2387 ± 0.0822 |
| EmbeddingGemma-300M | 0.5727 ± 0.1106 | 0.5537 ± 0.1056 | 0.5288 ± 0.1448 | 0.4936 ± 0.1107 | 0.2696 ± 0.0834 |

Every query-to-memory score sits lower than its memory-to-memory counterpart, and the standard deviations grow (roughly 0.07-0.15 vs. 0.03-0.09) — a dialogue line and a declarative fact statement share less surface form even on the same topic, and a hand-written query only ever approximates its case's topic rather than restating it. Both are expected and are exactly why the tests below distinguish the two data sources.

### Test 1: fact vs. opposite fact (query-driven)

Gap = `base − opposite`, from the query-to-memory scores. Tests whether a real retrieval query can tell a current fact from its direct contradiction.

**Within-model** (one-sample t-test against 0):

| Model | Mean gap | t | p | 95% CI | Significant? |
|---|---|---|---|---|---|
| nomic-embed-text-v1.5 | 0.0445 | 2.316 | 0.049 | (0.000, 0.089) | Barely (p just under 0.05) |
| nomic-embed-text-v2-moe | 0.0281 | 1.224 | 0.256 | (-0.025, 0.081) | No |
| EmbeddingGemma-300M | 0.0439 | 1.835 | 0.104 | (-0.011, 0.099) | No |

**Between-model** (one-way ANOVA on the gap): F=0.176, p=0.840 — not significant, no post-hoc test warranted.

v1.5's within-model result clears the conventional 0.05 threshold, but only just — its 95% CI's lower bound sits at 0.0002, a hair above zero — and three within-model tests were run here (one per model). Uncorrected for that, a p=0.049 result isn't strong evidence on its own; corrected for testing three models (Bonferroni: α=0.0167), it wouldn't clear the bar either. v2-moe and EmbeddingGemma-300M don't separate the two categories at all. Read together, this test does not show retrieval reliably telling a stale fact from its correction, for any of the three models — the outcome the test's purpose predicted.

### Test 2: on-topic vs. off-topic (query-driven)

Gap = `same_topic_different_fact − unrelated`, from the query-to-memory scores. Tests how much room mem0's `search()` threshold has to filter genuinely unrelated content without also cutting a related-but-different fact.

**Within-model** (one-sample t-test against 0):

| Model | Mean gap | t | p | 95% CI | Significant? |
|---|---|---|---|---|---|
| nomic-embed-text-v1.5 | 0.2040 | 6.117 | 0.0003 | (0.127, 0.281) | Yes |
| nomic-embed-text-v2-moe | 0.2300 | 4.824 | 0.0013 | (0.120, 0.340) | Yes |
| EmbeddingGemma-300M | 0.2240 | 5.508 | 0.0006 | (0.130, 0.318) | Yes |

**Between-model** (one-way ANOVA on the gap): F=0.111, p=0.896 — not significant, no post-hoc test warranted.

All three models separate these two categories clearly and reliably — this is the strongest result in the whole benchmark. None of the three does it detectably better than another. That has a concrete, practical consequence: real headroom exists to raise `search()`'s threshold above its current default of 0.1 to filter unrelated content, for whichever of these three models is deployed — but where to set it isn't shared across models, since the absolute score levels differ even though the separation doesn't. v1.5's unrelated-fact mean (0.4473) already sits well above 0.1, so the default threshold does essentially no filtering for it today; a threshold around 0.5-0.55 would sit between its same-topic (0.6513) and unrelated (0.4473) means. v2-moe and EmbeddingGemma-300M's unrelated means (0.2387, 0.2696) are closer to the current default, so a smaller increase — roughly 0.35-0.40 — would do the equivalent job for either.

### Test 3: memory-to-memory self-stratification

Gap = `paraphrase − opposite`, from the memory-to-memory scores (both measured against the base fact, no query involved). Tests whether a model's own embeddings reliably place a paraphrase closer to its source fact than a contradiction does — the feasibility check for a future admin-UI feature that proposes candidate duplicate/opposite memory pairs from a similarity matrix, unsupervised.

**Within-model** (one-sample t-test against 0):

| Model | Mean gap | t | p | 95% CI | Significant? |
|---|---|---|---|---|---|
| nomic-embed-text-v1.5 | 0.0702 | 3.556 | 0.0074 | (0.025, 0.116) | Yes |
| nomic-embed-text-v2-moe | 0.1100 | 3.249 | 0.0117 | (0.032, 0.188) | Yes |
| EmbeddingGemma-300M | 0.1350 | 4.331 | 0.0025 | (0.063, 0.207) | Yes |

**Between-model** (one-way ANOVA on the gap): F=1.277, p=0.297 — not significant, no post-hoc test warranted.

All three models separate paraphrase from opposite reliably within themselves — the feasibility case for an unsupervised duplicate/opposite proposal feature holds regardless of which of these three models backs it. The raw means order EmbeddingGemma-300M above v2-moe above v1.5, but the ANOVA doesn't support treating that ordering as a real difference between models; it's consistent with what 9 cases of one model's own noise could produce relative to another's.

### Reading the results together

Two different questions got two different answers. Test 1 asks whether the gap between a fact and its opposite exists at all in a real query — mostly no, across all three models, which is the expected outcome given both sides of that comparison share the query's topic. Tests 2 and 3 ask about different gaps (on-topic vs. off-topic; paraphrase vs. opposite in memory-to-memory), and those gaps are real and strong within every model tested. What no test found, on any of the three questions, is one model separating its categories more than another by a statistically established margin — every between-model ANOVA came back non-significant.

That leaves the VRAM and context-length numbers from "Model specs" as the only established difference between the three: both alternatives cost more VRAM than v1.5 (390 and 408 MiB vs. 320 MiB), and v2-moe's 512-token ceiling is short enough to be hit by mem0's own existing-memory retrieval step (see "Model specs"). With no model showing a demonstrated retrieval advantage over another on any of the three questions this benchmark set out to answer, those costs are the whole picture — this benchmark gives no reason to switch away from v1.5. A larger, more diverse test set would be needed before a real between-model quality difference could be established one way or the other; what this sample size and design can establish is that Test 2 and Test 3's separations are real properties of embedding space, shared across all three models tested.

## Raw data

Full per-case scores — both the memory-to-memory comparisons (9 cases × 4 categories × 3 models) and the query-to-memory comparisons (9 cases × 5 categories × 3 models) — are saved by `scripts/bench-embedding-models.py` to `scripts/bench-results/embedding-models.json` — gitignored, regenerated each run. `scripts/stat-test-embedding-gaps.py` reads that same file and reruns every within-model t-test and between-model ANOVA (and Tukey HSD, when warranted) behind the three tests above.
