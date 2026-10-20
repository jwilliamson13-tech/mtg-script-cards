"""Transcribe a video or audio file to an SRT with faster-whisper.

This is the optional ``--audio`` path. faster-whisper is a separate install
(``pip install -r requirements-whisper.txt``) and is imported only when a file
is actually transcribed, so the rest of the tool works without it.

Device: ``"auto"`` tries the GPU (CUDA) first and falls back to the CPU if the
GPU cannot load the model or fails while transcribing. ``int8`` is the default
compute type on both because it works everywhere: older GPUs (Pascal, e.g. the
GTX 10 series) have no float16, and int8 is the fast choice on a CPU.

Decoding: ``beam_size=5`` (Whisper's own default; greedy decoding mishears more
names) and ``vad_filter=True`` (skips silence and music, where Whisper is prone
to inventing text and looping).

The decklist prompt is Whisper's ``initial_prompt``. Each 30 s window is
conditioned on the 223 tokens before it, so the prompt steers only about the
first minute; on the fixture episode that is where the correct spellings
(Bitterblossom, Faeries) appear. faster-whisper's ``hotwords`` would repeat it
in every window, but on a 10-minute fixture clip it dropped 5-11% of the words
(one variant lost every "Master of Waves") for no gain in confident cards,
because the decklist matcher already recovers misheard names.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Protocol, TextIO

INSTALL = "pip install -r requirements-whisper.txt"
PROMPT_TOKENS = 223
"""Whisper's prompt budget: half its 448-token context, less one. faster-whisper
keeps only the last 223 tokens of a longer prompt, which would drop the first
(main deck) names, so the prompt is cut to fit before it gets there."""
DEFAULT_COMPUTE_TYPE = "int8"
PROGRESS_EVERY = 30.0  # seconds of wall time between progress lines


class TranscriberMissing(Exception):
    """faster-whisper is not installed (or cannot be imported). The message is
    one line saying what to install."""


class _Timed(Protocol):
    start: float
    end: float
    text: str


# --- SRT ---------------------------------------------------------------------


def srt_time(seconds: float) -> str:
    """``3725.5`` -> ``"01:02:05,500"``."""
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def srt_cue(number: int, segment: _Timed) -> str:
    """One SRT cue: number, timing line, text, blank line."""
    return (
        f"{number}\n{srt_time(segment.start)} --> {srt_time(segment.end)}\n"
        f"{segment.text.strip()}\n\n"
    )


def format_srt(segments: Iterable[_Timed]) -> str:
    """A standard SRT with one cue per segment. Segments with no text are skipped."""
    cues = [s for s in segments if s.text.strip()]
    return "".join(srt_cue(i, s) for i, s in enumerate(cues, start=1))


# --- the decklist prompt -------------------------------------------------------


def prompt_names(decklists: Iterable[Iterable[str]]) -> list[str]:
    """Unique card names across decklists, in order, with the faces of a
    double-faced card ("Delver of Secrets // Insectile Aberration") listed
    separately: that is how they are said."""
    names: dict[str, None] = {}
    for deck in decklists:
        for name in deck:
            for face in name.split("//"):
                face = face.strip()
                if face:
                    names.setdefault(face, None)
    return list(names)


def build_prompt(names: Sequence[str], fits: Callable[[str], bool]) -> str | None:
    """``names`` joined with ", ", keeping as many whole names from the start
    as ``fits`` allows. None when there are no names, or not even one fits."""
    prompt = None
    for i in range(1, len(names) + 1):
        candidate = ", ".join(names[:i])
        if not fits(candidate):
            break
        prompt = candidate
    return prompt


def _fit_prompt(
    prompt: str | Sequence[str], count_tokens: Callable[[str], int]
) -> tuple[str | None, str]:
    """The prompt cut to Whisper's budget, counted with the model's tokenizer,
    and a note saying how much of it was kept. A list of names keeps whole
    names; a string is cut after the last word that fits."""
    fits = lambda text: count_tokens(text) <= PROMPT_TOKENS  # noqa: E731
    if not isinstance(prompt, str):
        names = list(prompt)
        fitted = build_prompt(names, fits)
        kept = next((n for n in range(len(names), 0, -1) if ", ".join(names[:n]) == fitted), 0)
        return fitted, f"{kept} of {len(names)} decklist card names"
    words = prompt.split()
    lo, hi = 0, len(words)  # largest n with words[:n] fitting
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fits(" ".join(words[:mid])):
            lo = mid
        else:
            hi = mid - 1
    return " ".join(words[:lo]) or None, f"{lo} of {len(words)} words"


# --- faster-whisper -------------------------------------------------------------


def _add_nvidia_dll_dirs() -> list[str]:
    """Windows only: make the CUDA libraries from the pip ``nvidia-*`` packages
    (``nvidia-cublas-cu12``, ``nvidia-cudnn-cu12``) findable.

    Those packages put their DLLs in ``site-packages/nvidia/<lib>/bin``, where
    Windows does not look, and the GPU then fails with "Library cublas64_12.dll
    is not found". Returns the directories added.
    """
    if sys.platform != "win32":
        return []
    try:
        spec = importlib.util.find_spec("nvidia")
    except (ImportError, ValueError):
        return []
    if spec is None or not spec.submodule_search_locations:
        return []
    added = []
    path = os.environ.get("PATH", "").split(os.pathsep)
    for base in spec.submodule_search_locations:
        for bin_dir in sorted(Path(base).glob("*/bin")):
            if not any(bin_dir.glob("*.dll")):
                continue
            directory = str(bin_dir)
            if directory not in path:
                os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(directory)
            except OSError:
                pass
            added.append(directory)
    return added


def _import_whisper():
    """``faster_whisper.WhisperModel``, or TranscriberMissing with a one-line message."""
    _add_nvidia_dll_dirs()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        if exc.name in (None, "faster_whisper"):
            raise TranscriberMissing(
                f"--audio needs the transcription extra, which is not installed: {INSTALL}"
            ) from None
        raise TranscriberMissing(
            f"--audio needs the transcription extra, which did not load ({exc}): {INSTALL}"
        ) from None
    return WhisperModel


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - any failure means: no usable GPU
        return False


def _clock(seconds: float) -> str:
    """``4886.6`` -> ``"1:21:26"``; ``75`` -> ``"1:15"``."""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _log(log: TextIO, message: str) -> None:
    print(message, file=log, flush=True)


def _run(
    WhisperModel,
    audio_path: Path,
    part_path: Path,
    *,
    model: str,
    device: str,
    compute_type: str,
    initial_prompt: str | Sequence[str] | None,
    log: TextIO,
) -> int:
    """Transcribe on one device into ``part_path``. Returns the number of cues."""
    started = time.monotonic()
    _log(log, f"Loading the Whisper model '{model}' on {device} ({compute_type})...")
    whisper = WhisperModel(model, device=device, compute_type=compute_type)
    _log(log, f"Model loaded in {time.monotonic() - started:.0f}s.")

    prompt = None
    if initial_prompt:
        tokenizer = whisper.hf_tokenizer
        # faster-whisper encodes the prompt with a leading space; count it the same way.
        count = lambda text: len(  # noqa: E731
            tokenizer.encode(" " + text, add_special_tokens=False).ids
        )
        prompt, kept = _fit_prompt(initial_prompt, count)
        if prompt:
            _log(log, f"Prompt: {kept}, {count(prompt)} of {PROMPT_TOKENS} tokens.")

    segments, info = whisper.transcribe(
        str(audio_path),
        beam_size=5,
        vad_filter=True,
        initial_prompt=prompt,
    )
    _log(
        log,
        f"Transcribing {_clock(info.duration)} of audio "
        f"(language: {info.language}); progress every {PROGRESS_EVERY:.0f}s...",
    )
    transcribing = time.monotonic()
    last = transcribing
    count_cues = 0
    with open(part_path, "w", encoding="utf-8", newline="\n") as fh:
        for segment in segments:
            if not segment.text.strip():
                continue
            count_cues += 1
            fh.write(srt_cue(count_cues, segment))
            now = time.monotonic()
            if now - last >= PROGRESS_EVERY:
                last = now
                _log(
                    log,
                    f"  {_clock(segment.end)} of {_clock(info.duration)} "
                    f"({_clock(now - transcribing)} elapsed)",
                )
    _log(
        log,
        f"Transcribed {_clock(info.duration)} in {_clock(time.monotonic() - transcribing)} "
        f"on {device} ({compute_type}), {count_cues} cues.",
    )
    return count_cues


def transcribe(
    audio_path: str | Path,
    srt_path: str | Path,
    *,
    model: str = "small",
    initial_prompt: str | Sequence[str] | None = None,
    device: str = "auto",
    compute_type: str | None = None,
    log: TextIO | None = None,
) -> Path:
    """Transcribe ``audio_path`` (any audio or video file ffmpeg can read) to an
    SRT at ``srt_path``, one cue per Whisper segment. Progress goes to ``log``
    (stderr by default).

    ``initial_prompt`` is a string, or a list of card names joined with ", ";
    either is cut to Whisper's 223-token prompt budget, counted with the
    model's own tokenizer (a list keeps whole names, the first ones first).

    ``device`` is ``"auto"`` (GPU if one works, else CPU), ``"cuda"`` or
    ``"cpu"``. ``compute_type`` defaults to ``int8``.

    The SRT is written to a ``.part`` file and renamed when complete, so an
    interrupted run never leaves a partial transcript that a rerun would reuse.

    Raises TranscriberMissing when faster-whisper is not installed.
    """
    log = log or sys.stderr
    audio_path, srt_path = Path(audio_path), Path(srt_path)
    WhisperModel = _import_whisper()
    compute = compute_type or DEFAULT_COMPUTE_TYPE

    if device == "auto":
        devices = ["cuda", "cpu"] if _cuda_available() else ["cpu"]
    else:
        devices = [device]

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = srt_path.with_name(srt_path.name + ".part")
    try:
        for i, dev in enumerate(devices):
            try:
                _run(
                    WhisperModel,
                    audio_path,
                    part_path,
                    model=model,
                    device=dev,
                    compute_type=compute,
                    initial_prompt=initial_prompt,
                    log=log,
                )
                break
            except (RuntimeError, ValueError) as exc:
                if i + 1 == len(devices):
                    raise
                reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                _log(log, f"The GPU did not work ({reason}); using the CPU instead.")
                if compute_type is not None:
                    compute = DEFAULT_COMPUTE_TYPE  # a GPU compute type may not exist on CPU
        os.replace(part_path, srt_path)
    finally:
        if part_path.exists():
            part_path.unlink()
    return srt_path
