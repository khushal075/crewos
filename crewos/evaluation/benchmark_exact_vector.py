"""Measure warm exact cosine scoring at the configured candidate cap."""

import argparse
import json
from random import Random
from statistics import median
from time import perf_counter

from crewos.retrieval.strategies.cosine_similarity import CosineSimilarity


def benchmark(*, candidates: int, dimensions: int, samples: int) -> dict:
    if not 1 <= candidates <= 4096 or not 1 <= dimensions <= 4096 or not 3 <= samples <= 100:
        raise ValueError("Benchmark dimensions, candidates or sample count exceed bounds")
    rng = Random(17)
    vectors = [tuple(rng.uniform(-1, 1) for _ in range(dimensions)) for _ in range(candidates)]
    query = tuple(rng.uniform(-1, 1) for _ in range(dimensions))
    similarity = CosineSimilarity()

    def baseline():
        return sorted(
            (similarity.score(left=query, right=vector) for vector in vectors), reverse=True
        )[:10]

    def prepared():
        compare = similarity.prepare(query)
        return sorted((compare(vector) for vector in vectors), reverse=True)[:10]

    if baseline() != prepared():
        raise AssertionError("Prepared exact scoring changed the top scores")

    def measure(score):
        elapsed = []
        for _ in range(samples):
            started = perf_counter()
            score()
            elapsed.append((perf_counter() - started) * 1000)
        elapsed.sort()
        return {
            "p50_ms": round(median(elapsed), 2),
            "p95_ms": round(elapsed[(95 * samples + 99) // 100 - 1], 2),
        }

    return {
        "candidates": candidates,
        "dimensions": dimensions,
        "samples": samples,
        "baseline": measure(baseline),
        "prepared_exact": measure(prepared),
        "scope": "In-memory exact scoring and ranking only; no embedding, SQLite or network.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=int, default=512)
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument("--samples", type=int, default=20)
    args = parser.parse_args()
    print(
        json.dumps(
            benchmark(candidates=args.candidates, dimensions=args.dimensions, samples=args.samples),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
