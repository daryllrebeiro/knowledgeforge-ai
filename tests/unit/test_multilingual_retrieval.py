"""Unit tests for Phase 8 Item 8: Multilingual Ingestion and Cross-Lingual Retrieval."""


from evaluation.run_multilingual_eval import GOLDEN_SET_PATH, run_multilingual_eval
from knowledgeforge.generation.prompt import LabeledChunk, build_prompt
from knowledgeforge.ingestion.chunk import TextChunk


def test_build_prompt_includes_cross_lingual_instruction():
    chunk = TextChunk(
        text="The cloud architecture employs multi-region replication across us-east1 and europe-west1.",
        page=1,
        section="Architecture",
    )
    labeled = LabeledChunk(label="doc 1", chunk=chunk)

    # Japanese query against English document
    japanese_question = "クラウドアーキテクチャのレプリケーションリージョンはどこですか？"
    prompt = build_prompt(japanese_question, [labeled])

    assert "reply in the language of the question" in prompt
    assert "[doc 1, page 1]" in prompt
    assert japanese_question in prompt


def test_multilingual_golden_set_file_exists_and_valid():
    assert GOLDEN_SET_PATH.exists()
    eval_results = run_multilingual_eval(GOLDEN_SET_PATH)

    assert "total_queries" in eval_results
    assert "language_pairs_tested" in eval_results
    assert eval_results["total_queries"] >= 3
    assert eval_results["hit_at_1"] >= 0.80
    assert eval_results["citation_accuracy"] >= 0.80

    # Ensure all three target language pairs are represented
    pairs = eval_results["language_pairs_tested"]
    assert "de->en" in pairs
    assert "es->en" in pairs
    assert "ja->en" in pairs
