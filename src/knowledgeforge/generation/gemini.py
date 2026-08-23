from google import genai


class GeminiTextGenerator:
    def __init__(self, client: genai.Client, model: str) -> None:
        self.client = client
        self.model = model

    def generate(self, prompt: str) -> str:
        response = self.client.models.generate_content(model=self.model, contents=prompt)
        if not response.text:
            raise RuntimeError("Gemini returned an empty response")
        return response.text
