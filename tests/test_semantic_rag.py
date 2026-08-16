from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "atom-learn" / "scripts"
CLI = SCRIPTS / "atomlearn.py"
RUN_ROOT = ROOT / ".test-workspaces"
sys.path.insert(0, str(SCRIPTS))

from rag import RagEngine, RagError  # noqa: E402
from semantic import SemanticAdapterError, normalize_model_profile  # noqa: E402
from vector_index import VectorIndexError, _deps  # noqa: E402


def invoke(*args: object) -> dict:
    result = subprocess.run(
        [sys.executable, str(CLI), *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"command failed: {result.args}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout)


def workspace(label: str, *, dense_limit: int = 2000) -> tuple[Path, RagEngine]:
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    path = RUN_ROOT / f"semantic-{label}-{uuid.uuid4().hex}"
    invoke(
        "init",
        path,
        "--course-id",
        f"semantic.{label}",
        "--title",
        f"Semantic {label}",
        "--goal",
        "Verify bounded semantic retrieval",
    )
    invoke("rag", "init", path, "--dense-bruteforce-limit", dense_limit)
    return path, RagEngine.load(str(path))


def safe_model(path: Path, name: str) -> Path:
    model = path / name
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"safe-test-weights")
    return model.resolve()


class FakeEmbedding:
    @staticmethod
    def _vectors(texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0, 0.0] if "quantum" in text.lower() else [0.0, 1.0, 0.0]
            for text in texts
        ]

    def encode_document(self, texts: list[str], **_: object) -> list[list[float]]:
        return self._vectors(texts)

    def encode_query(self, texts: list[str], **_: object) -> list[list[float]]:
        return self._vectors(texts)


class StableCrossEncoder:
    def predict(self, pairs: list[tuple[str, str]], **_: object) -> list[float]:
        # Equal finite scores preserve the deterministic baseline order, which is
        # already required to pass the bundled gate.
        return [1.0 for _ in pairs]


def test_local_learned_embedding_is_explicit_hashed_and_queryable() -> None:
    path, engine = workspace("embedding")
    engine.ingest(
        {
            "sources": [
                {
                    "id": "scheduler",
                    "title": "Scheduler",
                    "authority": "textbook",
                    "text": "Round-robin scheduling assigns a fixed quantum.",
                },
                {
                    "id": "memory",
                    "title": "Memory",
                    "authority": "textbook",
                    "text": "A page table maps virtual pages to frames.",
                },
            ]
        },
        "local",
    )
    model = safe_model(path, "embedding-model")
    result = engine.embed_local(
        {
            "model": {
                "model_id": "test/embedding",
                "revision": "r1",
                "license": "test-only",
                "path": str(model),
            }
        },
        model_factory=lambda _: FakeEmbedding(),
    )
    assert result["dimension"] == 3
    assert result["model_sha256"].startswith("sha256:")
    found = engine.search(
        {"query": "quantum", "top_k": 2, "candidate_k": 5},
        record=False,
        embedding_model_factory=lambda _: FakeEmbedding(),
    )
    assert found["results"][0]["source_id"] == "scheduler"
    assert found["retrieval"]["semantic_dense_used"] is True
    assert found["retrieval"]["provider_dense_used"] is False

    unsafe = path / "unsafe-model"
    unsafe.mkdir()
    (unsafe / "pytorch_model.bin").write_bytes(b"pickle-capable")
    with pytest.raises(SemanticAdapterError, match="pickle-capable"):
        normalize_model_profile(
            {
                "model_id": "unsafe",
                "revision": "r1",
                "license": "unknown",
                "path": str(unsafe.resolve()),
            },
            "embedding",
        )
    (model / "model.safetensors").write_bytes(b"changed-after-approval")
    assert any("changed after approval" in error for error in engine.validate())


def test_named_multidomain_benchmark_is_a_nonempty_release_gate() -> None:
    path, engine = workspace("benchmark")
    report = engine.run_benchmark_profile("core-multidomain-v1")
    assert report["quality_gate"] == "pass"
    assert report["benchmark_profile"]["id"] == "core-multidomain-v1"
    assert len(report["benchmark_profile"]["dimensions"]) >= 9
    assert report["threshold_results"]
    assert report["metrics"]["source_diversity"] == 1.0
    assert report["metrics"]["freshness"] == 1.0
    assert report["metrics"]["correction_success_rate"] == 1.0
    assert report["metrics"]["residual_gap_rate"] == 0.0
    assert Path(report["report_path"]).is_file()
    with pytest.raises(RagError, match="either a named profile or explicit thresholds"):
        engine.evaluate(
            {
                **engine._benchmark_evaluation_payload(
                    engine.load_benchmark_profile("core-multidomain-v1")
                ),
                "thresholds": {
                    "recall_at_k": 0.0,
                    "mrr": 0.0,
                    "ndcg_at_k": 0.0,
                    "citation_correctness": 0.0,
                    "unsupported_claim_rate": 1.0,
                },
            }
        )


def test_cross_encoder_requires_a_passing_portable_benchmark_report() -> None:
    benchmark_path, benchmark_engine = workspace("reranker-benchmark")
    benchmark_engine.run_benchmark_profile("core-multidomain-v1")
    model = safe_model(benchmark_path, "cross-encoder")
    report = benchmark_engine.evaluate_reranker(
        {
            "profile": "core-multidomain-v1",
            "model": {
                "model_id": "test/cross-encoder",
                "revision": "r1",
                "license": "test-only",
                "path": str(model),
            },
        },
        model_factory=lambda _: StableCrossEncoder(),
    )
    assert report["quality_gate"] == "pass"
    report_path = benchmark_path / "reranker.report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    _, target = workspace("reranker-target")
    target.ingest(
        {
            "sources": [
                {
                    "id": "target-source",
                    "title": "Target",
                    "authority": "textbook",
                    "text": "A target corpus passage about retrieval evaluation.",
                }
            ]
        },
        "local",
    )
    tampered = json.loads(json.dumps(report))
    tampered["candidate_metrics"]["mrr"] = 0.0
    tampered_path = benchmark_path / "reranker-tampered.report.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(RagError, match="do not satisfy"):
        target.activate_reranker(
            {"report_path": str(tampered_path.resolve()), "confirmed": True}
        )
    activated = target.activate_reranker(
        {"report_path": str(report_path.resolve()), "confirmed": True}
    )
    assert activated["benchmark_profile"] == "core-multidomain-v1"
    search = target.search(
        {"query": "retrieval", "top_k": 1, "candidate_k": 5},
        record=False,
        reranker_model_factory=lambda _: StableCrossEncoder(),
    )
    assert search["retrieval"]["cross_encoder_used"] is True
    assert search["retrieval"]["reranker"] == "test/cross-encoder"


def test_large_dense_retrieval_uses_crash_safe_incremental_hnsw_generations() -> None:
    try:
        _deps()
    except VectorIndexError as exc:
        pytest.skip(str(exc))
    path, engine = workspace("hnsw", dense_limit=2)
    sources = [
        {
            "id": f"source-{index}",
            "title": f"Source {index}",
            "authority": "textbook",
            "text": f"Topic {index} contains retrieval marker {index} and supporting context.",
        }
        for index in range(6)
    ]
    engine.ingest({"sources": sources}, "local")
    before = engine.search(
        {"query": "retrieval marker 4", "top_k": 3, "candidate_k": 6}, record=False
    )
    assert before["retrieval"]["dense_execution"]["default:0"]["mode"] == "skipped_large_index"
    assert before["retrieval"]["dense_execution"]["default:0"]["scanned_chunks"] == 0

    first = engine.build_vector_index("default")
    assert first["indexes"][0]["build_mode"] == "full"
    after = engine.search(
        {"query": "retrieval marker 4", "top_k": 3, "candidate_k": 6}, record=False
    )
    assert after["retrieval"]["dense_execution"]["default:0"]["mode"] == "hnsw"
    assert after["retrieval"]["large_dense_full_scan_avoided"] is True

    changed = dict(sources[0])
    changed["text"] = "Topic zero now has revised retrieval marker evidence."
    engine.ingest({"sources": [changed]}, "local")
    assert engine.vector_index_status()["indexes"][0]["status"] == "stale"
    second = engine.build_vector_index("default")
    assert second["indexes"][0]["build_mode"] == "incremental"
    generations = sorted((path / ".atomlearn" / "rag" / "vector-index" / "default").glob("g*"))
    assert len(generations) == 2


def test_parent_context_expands_structure_but_keeps_child_citation_rule() -> None:
    _, engine = workspace("parent-context")
    engine.ingest(
        {
            "sources": [
                {
                    "id": "structured",
                    "title": "Structured source",
                    "authority": "textbook",
                    "text": "# Optimization\n\nStep size controls update magnitude.\n\nMomentum smooths updates.",
                }
            ]
        },
        "local",
    )
    result = engine.search(
        {"query": "step size magnitude", "top_k": 1, "candidate_k": 5}, record=False
    )["results"][0]
    assert result["parent_context"]["supporting_child_block_ids"]
    assert "supporting child locator" in result["parent_context"]["evidence_citation_rule"]
