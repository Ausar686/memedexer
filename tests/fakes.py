import pathlib

from memedexer.captioning.base import CaptionResult, Image, Usage
from memedexer.captioning.catalog import ModelSpec

MEME = (pathlib.Path(__file__).parent / "fixtures" / "meme.png").read_bytes()
USAGE = Usage(1000, 100)


def caption_result() -> CaptionResult:
    return CaptionResult(
        text="WHEN ALL TESTS PASS", description="A smiley.", tags=["#tests"], languages=["en"], kind="meme", usage=USAGE
    )


class FakeProvider:
    """Returns its results in order, repeating the last one; exceptions are raised."""

    def __init__(self, *results: CaptionResult | Exception) -> None:
        self.results = list(results)
        self.calls: list[tuple[Image, ModelSpec, str | None]] = []

    async def caption(self, image: Image, *, model: ModelSpec, description_language: str | None) -> CaptionResult:
        self.calls.append((image, model, description_language))
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


class FakeDownloader:
    def __init__(self, result: bytes | Exception = MEME) -> None:
        self.result = result
        self.file_ids: list[str] = []

    async def download(self, file_id: str) -> bytes:
        self.file_ids.append(file_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
