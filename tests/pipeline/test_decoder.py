"""The child's entry point, run in-process with its limits faked; `test_sandbox` runs the real child."""

import io
import pathlib
import resource
import subprocess
import sys

import PIL.Image
import pytest

from memedexer.pipeline import decoder
from memedexer.pipeline.sandbox import read_frames
from tests import videos

MEME = pathlib.Path(__file__).parent.parent / "fixtures" / "meme.png"


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> dict[int, tuple[int, int]]:
    applied: dict[int, tuple[int, int]] = {}
    monkeypatch.setattr(resource, "setrlimit", lambda kind, value: applied.__setitem__(kind, value))
    return applied


def run(monkeypatch: pytest.MonkeyPatch, args: list[str], data: bytes) -> bytes:
    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "argv", ["decoder", *args])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout))
    decoder.main()
    sys.stdout.flush()
    return stdout.getvalue()


def test_video_mode_limits_resources_and_writes_framed_jpegs(
    monkeypatch: pytest.MonkeyPatch, limits: dict[int, tuple[int, int]]
) -> None:
    scenes = ["red", "green", "blue", "yellow", "white"] * 2

    output = run(monkeypatch, ["video", "--duration", "10"], videos.clip(scenes, container="h264"))

    assert limits == {
        resource.RLIMIT_CPU: (decoder.CPU_LIMIT_SECONDS, decoder.CPU_LIMIT_SECONDS + 1),
        resource.RLIMIT_FSIZE: (0, 0),
        resource.RLIMIT_NOFILE: (decoder.OPEN_FILES_LIMIT, decoder.OPEN_FILES_LIMIT),
        resource.RLIMIT_AS: (decoder.MEMORY_LIMIT_BYTES, decoder.MEMORY_LIMIT_BYTES),
    }
    pictures = [PIL.Image.open(io.BytesIO(frame)) for frame in read_frames(output)]
    assert [(picture.format, picture.getpixel((5, 5))[:2]) for picture in pictures] == [
        ("JPEG", (0, 128)),
        ("JPEG", (254, 0)),
        ("JPEG", (255, 255)),
    ]


def test_image_mode_writes_the_fitted_image(monkeypatch: pytest.MonkeyPatch, limits: dict) -> None:
    assert read_frames(run(monkeypatch, ["image"], MEME.read_bytes()), 1) == [MEME.read_bytes()]


@pytest.mark.parametrize(
    ("args", "data", "reason"),
    [(["image"], b"junk", "not a JPEG, PNG or WebP image"), (["video"], b"junk", "Invalid data found")],
)
def test_bad_input_exits_with_a_one_line_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, limits: dict, args: list[str], data: bytes,
    reason: str,
) -> None:
    with pytest.raises(SystemExit) as exited:
        run(monkeypatch, args, data)

    assert exited.value.code == 1
    assert capsys.readouterr().err.startswith(reason)


def test_memory_limit_is_skipped_where_the_os_refuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[int] = []

    def setrlimit(kind: int, value: tuple[int, int]) -> None:
        if kind == resource.RLIMIT_AS:
            raise ValueError("current limit exceeds maximum limit")
        applied.append(kind)

    monkeypatch.setattr(resource, "setrlimit", setrlimit)

    decoder._limit_resources()

    assert applied == [resource.RLIMIT_CPU, resource.RLIMIT_FSIZE, resource.RLIMIT_NOFILE]


def test_limited_child_cannot_write_files(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "db"
    target.write_bytes(b"")
    script = (
        "from memedexer.pipeline.decoder import _limit_resources; _limit_resources(); "
        f"open({str(target)!r}, 'ab', buffering=0).write(b'x')"
    )

    result = subprocess.run([sys.executable, "-c", script], capture_output=True)

    assert b"File too large" in result.stderr
    assert target.read_bytes() == b""


def test_image_mode_does_not_load_ffmpeg() -> None:
    script = "import sys; from memedexer.pipeline import decoder; print('av' in sys.modules)"

    assert subprocess.run([sys.executable, "-c", script], capture_output=True, text=True).stdout.strip() == "False"
