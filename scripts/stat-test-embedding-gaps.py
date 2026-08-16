#!/usr/bin/env python3
"""Statistical tests for docs/EMBEDDING_MODEL_BENCHMARK.md, run against the
per-case scores scripts/bench-embedding-models.py saves to
scripts/bench-results/embedding-models.json (`per_case` for the
memory-to-memory test, `per_case_query` for the query-to-memory test).

Three named tests, each reducing to one number per case per model -- a gap
between two score columns -- so the within-model and between-model checks
use the same unit of analysis:

  1. Fact vs. opposite fact (query-driven): can retrieval tell a stale fact
     from its current replacement? gap = base - opposite, from
     `per_case_query`. Expected to show little to no separation, since both
     sides share the query's topic.
  2. On-topic vs. off-topic (query-driven): gap = same_topic_different_fact
     - unrelated, from `per_case_query`. How cleanly a model separates these
     determines how much headroom mem0's `search()` threshold (see
     docs/EMBEDDING_MODEL_BENCHMARK.md's "Background") has to filter
     unrelated content without also cutting related-but-imprecise matches.
  3. Memory-to-memory self-stratification: gap = paraphrase - opposite, from
     `per_case` (both measured against the base memory). Whether a model's
     own embeddings reliably separate a paraphrase from a contradiction is
     the feasibility check for a future admin-UI feature that proposes
     candidate duplicate/opposite memory pairs from a similarity matrix,
     unsupervised.

For each test:
  - Within-model: a one-sample t-test of that model's 9 per-case gaps
    against 0 (`scipy.stats.ttest_1samp`) -- does this model separate the
    two categories at all, on its own.
  - Between-model: a one-way ANOVA across the three models' gap arrays
    (`scipy.stats.f_oneway`) -- do the models differ from each other in how
    well they separate the two categories. A Tukey HSD post-hoc test only
    follows when that ANOVA is significant (p < 0.05); running pairwise
    comparisons after a non-significant omnibus test has nothing to follow
    up on.

Requires `scipy` (`ttest_1samp`, `f_oneway`, `tukey_hsd`, the last added in
1.8) -- not part of this project's own containers, so run it with whatever
local Python/conda environment has scipy installed, e.g.:

  /opt/miniconda3/envs/scipy_env/bin/python scripts/stat-test-embedding-gaps.py

Usage:
  python3 scripts/stat-test-embedding-gaps.py
"""
import json
from pathlib import Path

from scipy.stats import f_oneway, ttest_1samp, tukey_hsd

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_FILE = REPO_ROOT / "scripts" / "bench-results" / "embedding-models.json"

TESTS = [
    {
        "name": "Test 1: fact vs. opposite fact (query-driven)",
        "purpose": (
            "Can retrieval tell a stale fact from its current replacement? "
            "Expected to show little to no separation, since both sides "
            "share the query's topic."
        ),
        "results_key": "per_case_query",
        "minuend": "base",
        "subtrahend": "opposite",
    },
    {
        "name": "Test 2: on-topic vs. off-topic (query-driven)",
        "purpose": (
            "How much headroom does mem0's search() threshold have to "
            "filter unrelated content without also cutting "
            "related-but-imprecise matches?"
        ),
        "results_key": "per_case_query",
        "minuend": "same_topic_different_fact",
        "subtrahend": "unrelated",
    },
    {
        "name": "Test 3: memory-to-memory self-stratification",
        "purpose": (
            "Feasibility check for an unsupervised duplicate/opposite "
            "memory-pair proposal feature: does a model's own embeddings "
            "reliably separate a paraphrase from a contradiction?"
        ),
        "results_key": "per_case",
        "minuend": "paraphrase",
        "subtrahend": "opposite",
    },
]


def per_case_gaps(rows, minuend, subtrahend):
    return [row[minuend] - row[subtrahend] for row in rows]


def run_test(test, data, labels):
    print(f"\n{'=' * 70}")
    print(test["name"])
    print(test["purpose"])
    print("=" * 70)

    gap_label = f"{test['minuend']} - {test['subtrahend']}"
    samples = []

    print(f"\n--- Within-model: one-sample t-test of ({gap_label}) against 0 ---")
    for r in data:
        gaps = per_case_gaps(r[test["results_key"]], test["minuend"], test["subtrahend"])
        samples.append(gaps)
        result = ttest_1samp(gaps, 0)
        ci = result.confidence_interval(confidence_level=0.95)
        mean = sum(gaps) / len(gaps)
        sig = "significant" if result.pvalue < 0.05 else "not significant"
        print(
            f"  {r['label']:28s} n={len(gaps)} mean={mean:.4f}  "
            f"t={result.statistic:.4f} p={result.pvalue:.4f} ({sig})  "
            f"95% CI=({ci.low:.4f}, {ci.high:.4f})"
        )

    print(f"\n--- Between-model: one-way ANOVA of ({gap_label}) across the 3 models ---")
    f_stat, p_value = f_oneway(*samples)
    print(f"  F={f_stat:.4f}, p={p_value:.4f}")

    if p_value >= 0.05:
        print("  ANOVA not significant (p >= 0.05) -- skipping Tukey HSD post-hoc test.")
        return

    tukey = tukey_hsd(*samples)
    ci = tukey.confidence_interval(confidence_level=0.95)
    print("  Tukey HSD pairwise comparisons:")
    for i in range(len(labels)):
        for j in range(len(labels)):
            if i < j:
                diff = tukey.statistic[i][j]
                p = tukey.pvalue[i][j]
                lo, hi = ci.low[i][j], ci.high[i][j]
                print(f"    {labels[i]} - {labels[j]}: mean diff={diff:.4f}, p={p:.4f}, 95% CI=({lo:.4f}, {hi:.4f})")


def main():
    data = json.loads(RESULTS_FILE.read_text())
    labels = [r["label"] for r in data]

    for test in TESTS:
        run_test(test, data, labels)

    print(
        "\nNote: all three models were scored against the same 9 cases, so this "
        "is a repeated-measures design, not independent samples -- the ANOVA "
        "and Tukey HSD above treat each model's 9 gaps as an independent "
        "sample, which is the standard textbook combination but not the most "
        "powerful test available for paired data. Read the p-values as a "
        "conservative check, not a precise one."
    )


if __name__ == "__main__":
    main()
