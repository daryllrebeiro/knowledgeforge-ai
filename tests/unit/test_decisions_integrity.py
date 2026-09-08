"""Unit test for Process Fix A: Mechanical verification of docs/decisions.md."""

from scripts.verify_decisions import verify_decisions_integrity


def test_decisions_numeric_claims_are_grounded():
    violations = verify_decisions_integrity()
    assert not violations, f"docs/decisions.md has ungrounded numeric claims: {violations}"
