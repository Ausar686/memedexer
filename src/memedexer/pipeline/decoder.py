"""Child process that decodes an untrusted image or video; run only through `sandbox`.

Reads the file from stdin and writes the pictures to stdout in `sandbox.write_frames` framing. Bad input exits with
status 1 and a one-line reason on stderr.
"""

import argparse
import resource
import sys

from memedexer.pipeline.images import encode_jpeg, fit_image
from memedexer.pipeline.sandbox import write_frames

MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024
CPU_LIMIT_SECONDS = 10
# Enough for the interpreter's own imports; the decoder itself reads only from stdin.
OPEN_FILES_LIMIT = 32


def main() -> None:
    _limit_resources()
    parser = argparse.ArgumentParser()
    kinds = parser.add_subparsers(dest="kind", required=True)
    kinds.add_parser("image")
    kinds.add_parser("video").add_argument("--duration", type=float, default=None)
    args = parser.parse_args()
    data = sys.stdin.buffer.read()
    try:
        write_frames(sys.stdout.buffer, _decode(args, data))
    except ValueError as exc:
        # Pillow's and PyAV's errors for bad input are ValueErrors (or wrapped in one); anything else is a bug and
        # keeps its traceback.
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
    return None


def _decode(args: argparse.Namespace, data: bytes) -> list[bytes]:
    if args.kind == "image":
        return [fit_image(data).data]
    # Imported only for videos: PyAV loads all of FFmpeg, which an image doesn't need.
    from memedexer.pipeline import frames

    return [encode_jpeg(picture).data for picture in frames.sample_frames(data, args.duration)]


def _limit_resources() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SECONDS, CPU_LIMIT_SECONDS + 1))
    # No file writes at all (pipes are unaffected), so a compromised decoder can't tamper with the database.
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (OPEN_FILES_LIMIT, OPEN_FILES_LIMIT))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    except ValueError:
        # macOS refuses to lower RLIMIT_AS; the limit matters on Linux, where the bot runs.
        pass
    return None


if __name__ == "__main__":
    main()
