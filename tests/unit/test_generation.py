from knowledgeforge.generation.generate import generate_answer
from knowledgeforge.ingestion.chunk import TextChunk


class FakeGenerator:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.response


def test_generate_answer_extracts_unique_page_citations() -> None:
    generator = FakeGenerator("The answer is grounded. [page 2] Also supported by [page 2].")
    result = generate_answer(generator, "What?", [TextChunk("Context", page=2)])

    assert result.answer.startswith("The answer is grounded")
    assert result.citations[0].page == 2
    assert "What?" in generator.prompt
