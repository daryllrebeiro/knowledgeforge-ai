"""Unit tests for resumable, cost-aware Phase 12 evaluation runner."""

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from evaluation.run_phase12_eval import (
    CheckpointTracker,
    SpendLimitReached,
    is_quota_exhausted_error,
    main,
)


def test_checkpoint_tracker_spend_and_save(tmp_path: Path):
    ckpt_file = tmp_path / "checkpoint.json"
    tracker = CheckpointTracker(
        ckpt_file,
        max_spend_usd=0.01,
        input_token_cost=0.0001,
        output_token_cost=0.0002,
    )

    # Normal spend below cap (10*0.0001 + 10*0.0002 = 0.003 < 0.01)
    tracker.record_spend(input_chars=40, output_chars=40)
    assert tracker.data["total_spend_usd"] == 0.003
    assert ckpt_file.exists()

    # Spend exceeding cap triggers SpendLimitReached
    with pytest.raises(SpendLimitReached):
        tracker.record_spend(input_chars=400, output_chars=400)

    # Checkpoint contains updated spend
    data = json.loads(ckpt_file.read_text(encoding="utf-8"))
    assert data["total_spend_usd"] >= 0.01


def test_checkpoint_tracker_load_and_profiles(tmp_path: Path):
    ckpt_file = tmp_path / "checkpoint.json"
    initial_data = {
        "completed_profiles": {
            "baseline-500-100-vector": {"hit_at_5": 0.85, "hits": 17, "retrieval_total": 20}
        },
        "total_spend_usd": 0.05,
    }
    ckpt_file.write_text(json.dumps(initial_data), encoding="utf-8")

    tracker = CheckpointTracker(ckpt_file)
    assert tracker.has_profile("baseline-500-100-vector")
    assert not tracker.has_profile("large-800-150-vector")
    assert tracker.get_profile("baseline-500-100-vector")["hit_at_5"] == 0.85


def test_quota_exhausted_detection():
    exc1 = Exception("429 RESOURCE_EXHAUSTED: quota exceeded")
    assert is_quota_exhausted_error(exc1)

    exc2 = Exception("GoogleGenAIError: Your prepayment credits are depleted.")
    assert is_quota_exhausted_error(exc2)

    exc3 = ValueError("Invalid input syntax")
    assert not is_quota_exhausted_error(exc3)


def test_eval_local_with_checkpoint_and_resume(tmp_path: Path, monkeypatch):
    ckpt_file = tmp_path / "test_eval_ckpt.json"
    output_file = tmp_path / "test_eval_out.json"

    # Pre-populate checkpoint with one completed profile
    pre_data = {
        "completed_profiles": {
            "baseline-500-100-vector": {
                "hit_at_5": 0.9,
                "hits": 18,
                "retrieval_total": 20,
                "answer_correctness": None,
                "correct_answers": 0,
                "graded_answers": 0,
                "refusal_accuracy": None,
                "correct_refusals": 0,
                "refusal_graded": 0,
                "refusal_total": 0,
                "errors": [],
            }
        }
    }
    ckpt_file.write_text(json.dumps(pre_data), encoding="utf-8")

    captured = StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    # Run eval with --resume for only baseline-500-100
    code = main(
        [
            "--local",
            "--profiles",
            "baseline-500-100",
            "--checkpoint-file",
            str(ckpt_file),
            "--output",
            str(output_file),
            "--resume",
        ]
    )
    assert code == 0
    stdout = captured.getvalue()
    assert "Resuming: reusing completed profile 'baseline-500-100-vector'" in stdout
    assert output_file.exists()
