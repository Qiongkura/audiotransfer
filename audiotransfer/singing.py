"""GAME note boundaries, bounded CPU inference, and exact vocal overlays.

The client is original code. GAME's pretrained models are CC BY-NC-SA 4.0;
see docs/GAME-NOTICE.md. Weights are downloaded and verified locally.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import time
import urllib.request
import zipfile

import numpy as np

from .events import NoteEvent
from .notes import midi_to_frequency, midi_to_note_name
from .vocal_pitch import file_digest, MODELS

ARCHIVE_SHA256 = "00ba0c64115b6b874d9ea4afd3e6cf822abda2a04e52569233b0a044fd40e4e8"
ARCHIVE_SIZE = 45676364
MODEL_URL = "https://github.com/openvpi/GAME/releases/download/v1.0.3/GAME-1.0.3-small-onnx.zip"
FILES = {
    "config.json": "60dfe7e7b57db29475604eeea9b2abf5f8cd2a49326f1f4bc0e1c14ed151a55e",
    "encoder.onnx": "a6868a702d9c88ccc06b19e54f49d88b7f8cba4a68517f20806bd81ef6f94e06",
    "segmenter.onnx": "7724db4ba59a388fff5aba2f08f0ac025d4ca59caef80e7f474053a1455c88e5",
    "estimator.onnx": "a84738b2504472a1f22773cc6b0eccad1353a8f36229ad490b8fea85388bb5e1",
    "dur2bd.onnx": "49d10d1ea186e43c51fd2c51cedb01a3852d363f1c7056ae77df50f7e8dd0996",
    "bd2dur.onnx": "6672d56b00f27a20e9bfaa92801c15e394bb520e49031d3ed2515b0a6cf27402",
}


@dataclass(frozen=True)
class SungNote:
    start_ms: int
    end_ms: int
    midi: float | None


def _download(archive: Path, progress) -> None:
    """Resume validated HTTP ranges; never accept a truncated release response."""
    parts = archive.parent / "download"
    parts.mkdir(exist_ok=True)
    with urllib.request.urlopen(
        urllib.request.Request(MODEL_URL + f"?head={time.time_ns()}", method="HEAD"),
        timeout=45,
    ) as response:
        endpoint = response.geturl()
    chunk_size = 2 * 1024 * 1024
    count = math.ceil(ARCHIVE_SIZE / chunk_size)

    def fetch(index):
        first, end = index * chunk_size, min(ARCHIVE_SIZE, (index + 1) * chunk_size)
        part = parts / f"{index:03d}.part"
        for attempt in range(16):
            have = part.stat().st_size if part.exists() else 0
            if have == end - first:
                return part
            if have > end - first:
                part.unlink()
                have = 0
            start = first + have
            request = urllib.request.Request(
                endpoint + f"&part_={index}-{attempt}-{time.time_ns()}",
                headers={"Range": f"bytes={start}-{end - 1}", "Accept-Encoding": "identity"},
            )
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    expected = f"bytes {start}-{end - 1}/{ARCHIVE_SIZE}"
                    if response.status != 206 or response.headers.get("Content-Range") != expected:
                        raise ValueError("模型服务器未返回完整分片")
                    with part.open("ab") as stream:
                        remaining = end - start
                        while remaining:
                            block = response.read(min(65536, remaining))
                            if not block:
                                break
                            stream.write(block)
                            remaining -= len(block)
            except (OSError, ValueError):
                continue
        raise ValueError("唱音模型下载未完成，请重新转换以继续下载。")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for done, future in enumerate(as_completed([pool.submit(fetch, i) for i in range(count)]), 1):
            future.result()
            progress(f"下载唱音模型进度：{done * 100 // count}%")
    temporary = archive.with_suffix(".partial")
    with temporary.open("wb") as output:
        for index in range(count):
            with (parts / f"{index:03d}.part").open("rb") as source:
                shutil.copyfileobj(source, output)
    if file_digest(temporary) != ARCHIVE_SHA256:
        for index in range(count):
            (parts / f"{index:03d}.part").unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)
        raise ValueError("唱音模型下载校验失败，请重新转换。")
    temporary.replace(archive)


def ensure_model(progress=None) -> Path:
    report = progress or (lambda _: None)
    directory = MODELS / "game"
    directory.mkdir(parents=True, exist_ok=True)
    if all((directory / name).is_file() and file_digest(directory / name) == digest for name, digest in FILES.items()):
        return directory
    archive = directory / "GAME-1.0.3-small-onnx.zip"
    # Reuse the verified research download when upgrading an existing checkout.
    research = MODELS / "game-research" / archive.name
    if not archive.is_file() or file_digest(archive) != ARCHIVE_SHA256:
        if research.is_file() and file_digest(research) == ARCHIVE_SHA256:
            archive = research
        else:
            report("首次下载唱音识别模型（约 44 MiB）；模型限非商业用途，后续可离线使用。")
            _download(archive, report)
    with zipfile.ZipFile(archive) as bundle:
        for name, digest in FILES.items():
            temporary = directory / (name + ".partial")
            with bundle.open("GAME-1.0.3-small-onnx/" + name) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            if file_digest(temporary) != digest:
                raise ValueError("唱音模型文件校验失败，请重新转换。")
            temporary.replace(directory / name)
    return directory


class GameModel:
    def __init__(self, directory: Path):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("缺少唱音识别依赖，请运行 setup.bat 后重试。") from exc
        ort.set_seed(42)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        self.sessions = {
            name: ort.InferenceSession(str(directory / f"{name}.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
            for name in ("encoder", "segmenter", "estimator", "dur2bd", "bd2dur")
        }

    def predict(self, samples: np.ndarray, sample_rate: int) -> list[SungNote]:
        import librosa
        duration = len(samples) / sample_rate
        if not duration:
            return []
        waveform = librosa.resample(samples, orig_sr=sample_rate, target_sr=self.config["samplerate"]).astype(np.float32)[None]
        x_seg, x_est, mask = self.sessions["encoder"].run(None, {
            "waveform": waveform, "duration": np.array([duration], np.float32),
        })
        known = self.sessions["dur2bd"].run(None, {"durations": np.array([[duration]], np.float32), "maskT": mask})[0]
        boundaries = known.copy()
        for step in range(8):
            boundaries = self.sessions["segmenter"].run(None, {
                "x_seg": x_seg, "language": np.array([self.config["languages"]["zh"]], np.int64),
                "known_boundaries": known, "prev_boundaries": boundaries,
                "t": np.array([step / 8], np.float32), "maskT": mask,
                "threshold": np.array(.3, np.float32), "radius": np.array(2, np.int64),
            })[0]
        durations, mask_n = self.sessions["bd2dur"].run(None, {"boundaries": boundaries, "maskT": mask})
        presence, pitches = self.sessions["estimator"].run(None, {
            "x_est": x_est, "boundaries": boundaries, "maskT": mask, "maskN": mask_n,
            "threshold": np.array(.2, np.float32),
        })
        result, elapsed = [], 0.0
        for length, voiced, pitch, valid in zip(durations[0], presence[0], pitches[0], mask_n[0]):
            if not valid:
                continue
            if not np.isfinite(length) or length < 0 or (voiced and not np.isfinite(pitch)):
                raise ValueError("唱音识别返回了无效音符")
            first = round(elapsed * 1000)
            elapsed += float(length)
            last = min(round(elapsed * 1000), round(duration * 1000))
            if last > first:
                midi = float(pitch) if voiced and 0 <= pitch <= 127 else None
                result.append(SungNote(first, last, midi))
        return result


def stitch_chunks(chunks, duration_ms: int) -> list[SungNote]:
    """Join only a held note demonstrably crossing an artificial chunk cut."""
    result: list[SungNote] = []
    previous_crosses = False
    for first, last, offset, notes in chunks:
        for note in notes:
            a, b = max(first, note.start_ms + offset), min(last, note.end_ms + offset, duration_ms)
            if b <= a:
                continue
            crosses_left = note.start_ms + offset <= first - 40
            crosses_right = note.end_ms + offset >= last + 40
            if (result and a == first == result[-1].end_ms and previous_crosses and crosses_left
                    and note.midi is not None and result[-1].midi is not None
                    and round(note.midi) == round(result[-1].midi)):
                result[-1] = SungNote(result[-1].start_ms, b, result[-1].midi)
            else:
                result.append(SungNote(a, b, note.midi))
            previous_crosses = crosses_right and b == last
    return result


def transcribe(samples: np.ndarray, sample_rate: int, progress=None) -> list[SungNote]:
    report = progress or (lambda _: None)
    samples = np.asarray(samples, np.float32)
    if samples.ndim != 1 or not np.all(np.isfinite(samples)) or sample_rate <= 0:
        raise ValueError("唱音识别需要有效的单声道音频")
    if not len(samples):
        return []
    total_ms = round(len(samples) * 1000 / sample_rate)
    if np.max(np.abs(samples), initial=0) <= 1e-5:
        return [SungNote(0, total_ms, None)] if total_ms else []
    directory = ensure_model(report)
    signature = hashlib.sha256(samples.tobytes() + f":{sample_rate}:game103-small:zh4:8:.3:.2:chunks12-context600:v1".encode()).hexdigest()
    cache_dir = directory / "notes-cache"
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / (signature + ".json")
    if cache.is_file():
        try:
            cached = [SungNote(**item) for item in json.loads(cache.read_text(encoding="utf-8"))]
            previous = 0
            for note in cached:
                if not 0 <= previous <= note.start_ms < note.end_ms <= total_ms:
                    raise ValueError("invalid cached note timing")
                if note.midi is not None and not (np.isfinite(note.midi) and 0 <= note.midi <= 127):
                    raise ValueError("invalid cached note pitch")
                previous = note.end_ms
            if not cached:
                raise ValueError("empty cached transcription")
            report("复用唱音边界识别结果。")
            return cached
        except (ValueError, TypeError, KeyError):
            report("唱音缓存不完整，重新识别。")
    model = GameModel(directory)
    chunks = []
    for first in range(0, total_ms, 12000):
        last = min(total_ms, first + 12000)
        a, b = max(0, first - 600), min(total_ms, last + 600)
        snippet = samples[round(a * sample_rate / 1000):round(b * sample_rate / 1000)]
        # Avoid promoting separated-stem digital silence into a note.
        notes = model.predict(snippet, sample_rate) if np.max(np.abs(snippet), initial=0) > 1e-5 else [SungNote(0, b - a, None)]
        chunks.append((first, last, a, notes))
        report(f"识别唱音与节奏进度：{last * 100 // total_ms}%")
    result = stitch_chunks(chunks, total_ms)
    temporary = cache.with_suffix(".partial")
    temporary.write_text(json.dumps([vars(item) for item in result]), encoding="utf-8")
    temporary.replace(cache)
    return result


def overlay_events(baseline: list[NoteEvent], sung_notes: list[SungNote], regions: list[tuple[int, int]], total_ms: int) -> list[NoteEvent]:
    """Replace complete singing regions, preserving breaths and real repeats."""
    base = []
    cursor = 0
    for event in baseline:
        base.append((cursor, cursor + event.duration_ms, event))
        cursor += event.duration_ms
    points = {0, total_ms}
    for first, last, _ in base:
        points.update((max(0, first), min(total_ms, last)))
    for note in sung_notes:
        points.update((max(0, note.start_ms), min(total_ms, note.end_ms)))
    for first, last in regions:
        points.update((max(0, first), min(total_ms, last)))
    points = sorted(p for p in points if 0 <= p <= total_ms)
    result, previous = [], None
    bi = ni = ri = 0
    for first, last in zip(points, points[1:]):
        while bi < len(base) and base[bi][1] <= first: bi += 1
        while ni < len(sung_notes) and sung_notes[ni].end_ms <= first: ni += 1
        while ri < len(regions) and regions[ri][1] <= first: ri += 1
        singing = ri < len(regions) and regions[ri][0] <= first < regions[ri][1]
        token = ("sung", ni) if singing else ("base", bi)
        if singing:
            note = sung_notes[ni] if ni < len(sung_notes) and sung_notes[ni].start_ms <= first else None
            midi = round(note.midi) if note and note.midi is not None else None
            event = NoteEvent(midi_to_frequency(midi), last - first, midi_to_note_name(midi)) if midi is not None else NoteEvent(0, last - first, "rest")
        elif bi < len(base) and base[bi][0] <= first:
            original = base[bi][2]
            event = NoteEvent(original.frequency_hz, last - first, original.note, original.legato)
        else:
            event = NoteEvent(0, last - first, "rest")
        if result and ((token == previous and result[-1].note == event.note) or (result[-1].frequency_hz == event.frequency_hz == 0)):
            old = result[-1]
            result[-1] = NoteEvent(old.frequency_hz, old.duration_ms + event.duration_ms, old.note, old.legato)
        else:
            result.append(event)
        previous = token
    return result
