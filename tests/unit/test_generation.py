from knowledgeforge.generation.gemini import GenerationResult
from knowledgeforge.generation.generate import Citation, generate_answer
from knowledgeforge.generation.prompt import LabeledChunk
from knowledgeforge.ingestion.chunk import TextChunk


class FakeGenerator:
    def __init__(self, response: str, input_tokens: int = 12, output_tokens: int = 34) -> None:
        self.result = GenerationResult(
            text=response, input_tokens=input_tokens, output_tokens=output_tokens
        )
        self.prompt = ""

    def generate(self, prompt: str) -> GenerationResult:
        self.prompt = prompt
        return self.result


def test_generate_answer_extracts_unique_document_and_page_citations() -> None:
    generator = FakeGenerator(
        "The answer is grounded. [doc 1, page 2] Also supported by [doc 1, page 2] "
        "and [doc 2, page 3]."
    )
    result = generate_answer(
        generator,
        "What?",
        [
            LabeledChunk("doc 1", TextChunk("Context", page=2)),
            LabeledChunk("doc 2", TextChunk("More context", page=3)),
        ],
    )

    assert result.answer.startswith("The answer is grounded")
    assert result.citations == [
        Citation(document_index=1, page=2),
        Citation(document_index=2, page=3),
    ]
    assert "What?" in generator.prompt


def test_generate_answer_ignores_page_only_markers() -> None:
    generator = FakeGenerator("Old format leaked: [page 2].")

    result = generate_answer(
        generator, "What?", [LabeledChunk("doc 1", TextChunk("c", page=2))]
    )

    assert result.citations == []


def test_generate_answer_carries_token_usage() -> None:
    generator = FakeGenerator("Answer. [doc 1, page 1]", input_tokens=99, output_tokens=7)

    result = generate_answer(
        generator, "What?", [LabeledChunk("doc 1", TextChunk("c", page=1))]
    )

    assert result.input_tokens == 99
    assert result.output_tokens == 7
