"""Cost-safe manual title-video workflow; not the body/contain export layout.

Preflight is an explicit visual review, not automatic object recognition. The
review is bound to the reference, prompt, model and destination crop. One job
file reserves one paid call, including on failure; retry requires new approval.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cover_window(width: int, height: int, target: tuple[int, int]) -> tuple[float, ...]:
    if min(width, height, *target) <= 0:
        raise ValueError("Framing dimensions must be positive")
    ratio = target[0] / target[1]
    source = width / height
    w, h = (ratio / source, 1.0) if source >= ratio else (1.0, source / ratio)
    return ((1 - w) / 2, (1 - h) / 2, (1 + w) / 2, (1 + h) / 2)


def cover_filter(target: tuple[int, int]) -> str:
    if min(target) <= 0 or any(n % 2 for n in target):
        raise ValueError("H.264 destination dimensions must be positive and even")
    w, h = target
    # Never use pad/contain to hide a failed title composition.
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def make_preflight(reference: Path, prompt: str, *, model: str,
                   aspect_ratio: str, target: tuple[int, int],
                   subject_bounds: tuple[float, ...], reviewed: bool) -> dict:
    from PIL import Image

    if not reviewed or not prompt.strip() or not model.strip():
        raise ValueError("Inspect the final crop and product appearance before approving generation")
    a, b = map(int, aspect_ratio.split(":"))
    if min(a, b) <= 0:
        raise ValueError("Invalid provider aspect ratio")
    with Image.open(reference) as im:
        width, height = im.size
    if not math.isclose(width / height, a / b, rel_tol=.01):
        raise ValueError("Reference aspect differs from requested output; preview the provider crop first")
    crop = cover_window(width, height, target)
    cover_filter(target)
    if len(subject_bounds) != 4 or not all(math.isfinite(n) for n in subject_bounds):
        raise ValueError("Record the complete subject AND intended motion bounds")
    x0, y0, x1, y1 = subject_bounds
    margin_x, margin_y = (crop[2] - crop[0]) * .04, (crop[3] - crop[1]) * .04
    if not (crop[0] + margin_x <= x0 < x1 <= crop[2] - margin_x
            and crop[1] + margin_y <= y0 < y1 <= crop[3] - margin_y):
        raise ValueError("Subject/motion does not fit the final crop with safe margins; no paid call")
    return dict(version=1, reference=str(reference.resolve()), reference_sha256=digest(reference),
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(), model=model,
                aspect_ratio=aspect_ratio, target=list(target), source_size=[width, height],
                cover_window=list(crop), subject_and_motion_bounds=list(subject_bounds),
                visual_reviewed=True, review_scope="whole products, labels, crop, margins, no bars",
                automatic_paid_retries=0)


def validate_preflight(stamp: dict, reference: Path, prompt: str, *, model: str) -> None:
    current = make_preflight(reference, prompt, model=model,
        aspect_ratio=stamp["aspect_ratio"], target=tuple(stamp["target"]),
        subject_bounds=tuple(stamp["subject_and_motion_bounds"]),
        reviewed=stamp.get("visual_reviewed") is True)
    if current != stamp:
        raise ValueError("Preflight is stale or changed; inspect again BEFORE spending")


def generate_once(provider, request, *, preflight: dict, reference: Path,
                  job_path: Path, output_path: Path, approved: bool,
                  estimated_cost_usd: float, secrets: tuple[str, ...] = ()) -> dict:
    if not approved:
        raise ValueError("Explicit paid approval is required")
    validate_preflight(preflight, reference, request.prompt, model=request.model)
    if (request.aspect_ratio != preflight["aspect_ratio"] or request.duration_seconds != 4
            or request.source_image != reference.read_bytes()):
        raise ValueError("Actual request differs from reviewed framing/reference")
    if output_path.exists():
        raise FileExistsError("Do not overwrite a prior generation")
    job = dict(status="submitting", attempts=1, automatic_paid_retries=0,
               estimated_cost_usd=estimated_cost_usd, actual_cost_usd=None,
               model=request.model, seconds=4, prompt=request.prompt, preflight=preflight)
    job_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents concurrent duplicate calls. A failed or
    # interrupted job stays reserved; never clear it automatically to retry.
    with job_path.open("x", encoding="utf8") as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    try:
        result = provider.submit(request)
        job["external_id"] = result.id
        if not result.completed or result.asset is None:
            raise RuntimeError("External job not complete; inspect it without resubmitting")
        output_path.write_bytes(result.asset.data)
        job.update(status="generated_pending_visual_qa", file=str(output_path))
    except Exception as exc:
        from ..automation.executor import _safe_error_text
        detail = _safe_error_text(exc)
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "[마스킹]")
        job.update(status="failed", error=detail)
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf8")
        raise RuntimeError("Generation failed; redacted cause saved. NO automatic retry") from None
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf8")
    return job


def black_edge_bands(frame) -> dict[str, int]:
    """Conservative near-black full-edge screen; natural dark scenes need review.

    This detects bars, NOT product identity, subject clipping or all AI defects.
    Examine every decoded frame plus the actual destination crop visually.
    """
    import numpy as np
    rgb = np.asarray(frame.convert("RGB"))
    dark = rgb.max(axis=2) <= 12
    rows, columns = (dark.mean(axis=1) >= .99), (dark.mean(axis=0) >= .99)
    def run(values):
        count = 0
        for value in values:
            if not value:
                break
            count += 1
        return count
    h, w = dark.shape
    bands = dict(top=run(rows), bottom=run(rows[::-1]),
                 left=run(columns), right=run(columns[::-1]))
    return {k: n if n >= max(2, math.ceil((h if k in ("top", "bottom") else w) * .02))
            else 0 for k, n in bands.items()}


def inspect_video(path: Path) -> dict:
    """Offline all-frame black-band check. No external API or paid retry."""
    from PIL import Image
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    width, height = video["width"], video["height"]
    sw, sh = 320, max(2, round(height * 320 / width / 2) * 2)
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frames, findings = 0, []
    try:
        while True:
            raw = proc.stdout.read(sw * sh * 3)
            if not raw:
                break
            if len(raw) != sw * sh * 3:
                raise RuntimeError("Truncated frame; QA is incomplete")
            bands = black_edge_bands(Image.frombytes("RGB", (sw, sh), raw))
            if any(bands.values()):
                findings.append(dict(frame=frames, bands=bands))
            frames += 1
        error = proc.stderr.read()
        if proc.wait() != 0:
            raise RuntimeError(f"Video decoding failed: {error[:300]!r}")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.stderr.close()
    if not frames:
        raise ValueError("No frames decoded")
    duration = float(probe["format"]["duration"])
    return dict(file=str(path), sha256=digest(path), width=width, height=height,
                duration_sec=duration, decoded_frames=frames,
                audio_streams=sum(s["codec_type"] == "audio" for s in probe["streams"]),
                black_band_frames=len(findings), black_band_examples=findings[:5],
                passed_no_black_bands=not findings,
                manual_product_and_motion_review_required=True)


def frame_title(source: Path, destination: Path, target: tuple[int, int]) -> dict:
    if destination.exists():
        raise FileExistsError("Keep prior media intact until new QA succeeds")
    source_qa = inspect_video(source)
    if not source_qa["passed_no_black_bands"]:
        raise ValueError("Source contains possible black bars; inspect before framing")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-an", "-vf",
        cover_filter(target), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(destination)], check=True)
    report = inspect_video(destination)
    if ((report["width"], report["height"]) != target or report["audio_streams"]
            or not report["passed_no_black_bands"]):
        raise ValueError("Framed title failed output QA; do not publish")
    return report
