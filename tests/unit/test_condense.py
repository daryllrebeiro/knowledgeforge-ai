"""Follow-up question rewriting (F1)."""

from knowledgeforge.generation.condense import build_rewrite_prompt, rewrite_followup_question
from knowledgeforge.generation.gemini import GenerationResult


class FakeGenerator:
    def __init__(self, text: str | None = None, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> GenerationResult:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        assert self.text is not None
        return GenerationResult(text=self.text, input_tokens=1, output_tokens=1)


def test_no_history_returns_question_without_a_provider_call() -> None:
    generator = FakeGenerator()

    assert rewrite_followup_question(generator, "What is the limit?", []) == "What is the limit?"
    assert generator.prompts == []


def test_history_is_rewritten_to_a_standalone_question() -> None:
    generator = FakeGenerator(text="What is the file download limit?")
    history = [("user", "What is the upload limit?"), ("assistant", "10 MB.")]

    result = rewrite_followup_question(generator, "and downloads?", history)

    assert result == "What is the file download limit?"
    prompt = generator.prompts[0]
    assert "and downloads?" in prompt
    assert "What is the upload limit?" in prompt
    # Conversation content is data, not instructions.
    assert "quoted data" in prompt


def test_provider_failure_falls_back_to_the_raw_question() -> None:
    generator = FakeGenerator(error=RuntimeError("provider down"))
    history = [("user", "What is the upload limit?"), ("assistant", "10 MB.")]

    result = rewrite_followup_question(generator, "and downloads?", history)

    assert result == "and downloads?"


def test_empty_rewrite_falls_back_to_the_raw_question() -> None:
    generator = FakeGenerator(text="   ")
    history = [("user", "What is the upload limit?"), ("assistant", "10 MB.")]

    result = rewrite_followup_question(generator, "and downloads?", history)

    assert result == "and downloads?"


def test_build_rewrite_prompt_includes_every_history_turn() -> None:
    prompt = build_rewrite_prompt("why?", [("user", "one"), ("assistant", "two")])

    assert "User: one" in prompt
    assert "Assistant: two" in prompt
    assert "Follow-up question: why?" in prompt
