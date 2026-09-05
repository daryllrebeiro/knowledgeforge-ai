from collections.abc import Iterator
from dataclasses import dataclass

from google import genai

from knowledgeforge.reliability import with_retry


@dataclass(frozen=True)
class GenerationResult:
    """Generated text plus the token usage it consumed."""

    text: str
    input_tokens: int
    output_tokens: int


class GeminiTextStream:
    """Streaming generation deltas with accumulated token usage (F1).

    Exposes ``input_tokens``/``output_tokens`` after iteration completes;
    usage counts only grow across stream chunks, so the maximum seen is the
    final total. Iteration is not retried — a mid-stream failure cannot be
    resumed without resending partial output — but callers still route the
    stream through the circuit breaker.
    """

    def __init__(self, client: genai.Client, model: str, prompt: str) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt
        self.input_tokens = 0
        self.output_tokens = 0

    def __iter__(self) -> Iterator[str]:
        for response in self._client.models.generate_content_stream(
            model=self._model, contents=self._prompt
        ):
            usage = response.usage_metadata
            if usage is not None:
                self.input_tokens = max(self.input_tokens, usage.prompt_token_count or 0)
                self.output_tokens = max(self.output_tokens, usage.candidates_token_count or 0)
            if response.text:
                yield response.text


class GeminiTextGenerator:
    def __init__(self, client: genai.Client, model: str) -> None:
        self.client = client
        self.model = model

    @with_retry
    def generate(self, prompt: str) -> GenerationResult:
        response = self.client.models.generate_content(model=self.model, contents=prompt)
        if not response.text:
            raise RuntimeError("Gemini returned an empty response")
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage is not None else None
        output_tokens = usage.candidates_token_count if usage is not None else None
        return GenerationResult(
            text=response.text,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
        )
