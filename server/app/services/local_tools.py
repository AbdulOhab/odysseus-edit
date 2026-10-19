# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Vendor-free image tools: sharpen, Real-ESRGAN denoise/upscale, rembg,
SAM masks, and GFPGAN face restore.

Only Pillow and NumPy are required. Every ML backend is imported lazily so a
base install starts and answers with the same "not installed" errors upstream
returns. All functions here are synchronous and CPU/GPU bound; routes call
them through a threadpool.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import tempfile
import threading
import types
from typing import Any

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

REALESRGAN_MISSING = (
    "realesrgan not installed. Install it into the server environment: "
    "uv pip install --no-build-isolation basicsr realesrgan"
)
REMBG_MISSING = (
    "No background removal model available. Install rembg: uv sync --extra rembg"
)
SAM_MISSING = (
    "SAM mask tools are not installed. Install the sam extra: uv sync --extra sam"
)
GROUNDING_MISSING = (
    "Object mask tools are not installed. Install the sam extra: uv sync --extra sam"
)

_REALESRGAN_GENERAL_X4V3 = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
_REALESRGAN_X4PLUS = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
_GFPGAN_V14 = "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth"

_cache: dict[Any, Any] = {}
_cache_lock = threading.Lock()
# One inference at a time per backend: these models are not re-entrant and a
# parallel second request would only double peak memory.
_infer_locks: dict[str, threading.Lock] = {
    name: threading.Lock() for name in ("realesrgan", "rembg", "sam", "grounding", "gfpgan")
}


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def availability() -> dict[str, bool]:
    """Cheap import-spec probe for /api/capabilities (no imports, no network)."""
    torch = has_module("torch")
    transformers = has_module("transformers")
    realesrgan = has_module("realesrgan") and has_module("basicsr") and torch
    return {
        "sharpen": True,
        "denoise": realesrgan,
        "upscale_local": realesrgan,
        "remove_bg": (has_module("rembg") and has_module("onnxruntime")) or (transformers and torch),
        "mask": torch and transformers,
        # GFPGAN when installed, otherwise the Pillow fallback.
        "enhance_face": True,
    }


def _cached(key: Any, factory):
    with _cache_lock:
        if key not in _cache:
            _cache[key] = factory()
        return _cache[key]


# ---------------------------------------------------------------- sharpen ---

def sharpen(img: Image.Image, amount: float) -> Image.Image:
    """Unsharp mask: radius=2, percent=amount*200, threshold=3 (amount in 0..1+)."""
    from app.imaging import split_alpha, with_alpha

    rgb, alpha = split_alpha(img)
    out = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=int(amount * 200), threshold=3))
    return with_alpha(out, alpha)


# ------------------------------------------------------------ Real-ESRGAN ---

def patch_realesrgan_torchvision_compat() -> None:
    """Restore `torchvision.transforms.functional_tensor`, removed in
    torchvision 0.17 but still imported by BasicSR/Real-ESRGAN."""
    module_name = "torchvision.transforms.functional_tensor"
    if module_name in sys.modules:
        return
    try:
        from torchvision.transforms import functional
    except Exception:
        return
    rgb_to_grayscale = getattr(functional, "rgb_to_grayscale", None)
    if rgb_to_grayscale is None:
        return
    shim = types.ModuleType(module_name)
    shim.rgb_to_grayscale = rgb_to_grayscale
    shim.__getattr__ = lambda name: getattr(functional, name)
    sys.modules[module_name] = shim


def _import_realesrgan():
    """Return the RealESRGANer class or raise ImportError."""
    patch_realesrgan_torchvision_compat()
    from realesrgan import RealESRGANer

    return RealESRGANer


def realesrgan_denoise(rgb: Image.Image, strength: float) -> Image.Image:
    """Real-ESRGAN general-x4v3 at outscale=1. Raises ImportError if missing.

    Upstream passes `dni_weight` with a single model path; RealESRGANer only
    applies DNI when given two paths, so `strength` does not change the
    output. Kept as-is for parity with Odysseus.
    """
    import numpy as np

    RealESRGANer = _import_realesrgan()
    from realesrgan.archs.srvgg_arch import SRVGGNetCompact

    def build():
        model = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64,
                                num_conv=32, upscale=4, act_type="prelu")
        return RealESRGANer(
            scale=4,
            model_path=_REALESRGAN_GENERAL_X4V3,
            dni_weight=[strength, 1.0 - strength],
            model=model,
            tile=400, tile_pad=10, pre_pad=0, half=False,
        )

    upsampler = _cached(("realesrgan-denoise", round(strength, 2)), build)
    with _infer_locks["realesrgan"]:
        output, _ = upsampler.enhance(np.array(rgb), outscale=1)
    return Image.fromarray(output)


def realesrgan_upscale(rgb: Image.Image, scale: int) -> Image.Image:
    """Real-ESRGAN x4plus at outscale=scale (2 or 4). Raises ImportError if missing."""
    import numpy as np

    RealESRGANer = _import_realesrgan()
    from basicsr.archs.rrdbnet_arch import RRDBNet

    def build():
        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4)
        return RealESRGANer(
            scale=4,
            model_path=_REALESRGAN_X4PLUS,
            model=model,
            tile=400, tile_pad=10, pre_pad=0, half=False,
        )

    upsampler = _cached("realesrgan-x4plus", build)
    with _infer_locks["realesrgan"]:
        output, _ = upsampler.enhance(np.array(rgb), outscale=scale)
    return Image.fromarray(output)


# ------------------------------------------------------------------ rembg ---

def _rembg_backend():
    """Return a callable PIL -> PIL cutout, or raise ImportError."""
    # rembg exits the process (sys.exit) when onnxruntime is missing, so
    # check for it before importing.
    if not has_module("onnxruntime"):
        raise ImportError("onnxruntime not installed")
    try:
        from rembg import new_session, remove
    except SystemExit as exc:
        raise ImportError("rembg could not load an onnxruntime backend") from exc

    # Upstream calls remove() without a session, i.e. rembg's default model.
    # Recent rembg (checked 2.0.85) defaults to bria-rmbg (RMBG-2.0, CC BY-NC 4.0),
    # so pin u2net (Apache-2.0) unless the user opts into another model.
    session = _cached("rembg-session", lambda: new_session(os.getenv("ODYSSEUS_EDIT_REMBG_MODEL", "u2net")))
    return lambda img: remove(img, session=session)


def _transformers_rmbg_backend():
    """Upstream's fallback: briaai/RMBG-1.4 through a transformers pipeline.

    Note: the model ships custom code (trust_remote_code=True), fetched from
    the Hugging Face Hub on first use.
    """
    from transformers import pipeline

    pipe = _cached(
        "rmbg-pipeline",
        lambda: pipeline("image-segmentation", model="briaai/RMBG-1.4", trust_remote_code=True),
    )

    def run(img: Image.Image) -> Image.Image:
        mask_img = pipe(img, return_mask=True).convert("L")
        tmp = img.copy()
        tmp.putalpha(mask_img)
        return tmp

    return run


class BackgroundRemovalUnavailable(RuntimeError):
    pass


def remove_background(img: Image.Image, hint: Image.Image | None = None) -> Image.Image:
    """Cut out the subject. `hint` is an L-mode mask (white = keep) at image size.

    With a hint: crop to its bbox (+8 px), run the model on the crop, paste
    back, then multiply the alpha by the hint so nothing outside it survives.
    """
    W, H = img.size
    bbox = None
    if hint is not None:
        bbox = hint.getbbox()
        if bbox:
            pad = 8
            bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                    min(W, bbox[2] + pad), min(H, bbox[3] + pad))
    crop = img.crop(bbox) if bbox else img

    try:
        backend = _rembg_backend()
    except ImportError:
        try:
            backend = _transformers_rmbg_backend()
        except Exception as exc:
            raise BackgroundRemovalUnavailable(REMBG_MISSING) from exc

    with _infer_locks["rembg"]:
        cut = backend(crop)

    if bbox:
        result = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cut = cut.convert("RGBA")
        result.paste(cut, (bbox[0], bbox[1]), cut)
    else:
        result = cut.convert("RGBA")

    if hint is not None:
        r, g, b, a = result.split()
        result = Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, hint)))
    return result


# -------------------------------------------------------------------- SAM ---

def _torch_device(torch) -> str:
    try:
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _load_sam_backend() -> dict[str, Any]:
    model_id = os.getenv("ODYSSEUS_SAM_MODEL", "facebook/sam-vit-base")
    cached = _cache.get(("sam", model_id))
    if cached:
        return cached
    try:
        import torch
        from transformers import SamModel, SamProcessor
    except Exception as exc:
        raise HTTPException(501, SAM_MISSING) from exc
    device = _torch_device(torch)
    try:
        processor = SamProcessor.from_pretrained(model_id)
        model = SamModel.from_pretrained(model_id)
        model.to(device)
        model.eval()
    except Exception as exc:
        raise HTTPException(500, f"Failed to load SAM model {model_id}: {exc}") from exc
    backend = {"torch": torch, "processor": processor, "model": model, "device": device, "model_id": model_id}
    with _cache_lock:
        _cache[("sam", model_id)] = backend
    return backend


def _load_grounding_backend() -> dict[str, Any]:
    model_id = os.getenv("ODYSSEUS_GROUNDING_MODEL", "google/owlvit-base-patch32")
    cached = _cache.get(("grounding", model_id))
    if cached:
        return cached
    try:
        import torch
        from transformers import OwlViTForObjectDetection, OwlViTProcessor
    except Exception as exc:
        raise HTTPException(501, GROUNDING_MISSING) from exc
    device = _torch_device(torch)
    try:
        processor = OwlViTProcessor.from_pretrained(model_id)
        model = OwlViTForObjectDetection.from_pretrained(model_id)
        model.to(device)
        model.eval()
    except Exception as exc:
        raise HTTPException(500, f"Failed to load object mask model {model_id}: {exc}") from exc
    backend = {"torch": torch, "processor": processor, "model": model, "device": device, "model_id": model_id}
    with _cache_lock:
        _cache[("grounding", model_id)] = backend
    return backend


def _model_inputs_to_device(inputs, device: str, torch) -> dict[str, Any]:
    def move(value):
        if not hasattr(value, "to"):
            return value
        # MPS has no float64.
        if device == "mps" and getattr(value, "dtype", None) == getattr(torch, "float64", None):
            return value.to(device=device, dtype=torch.float32)
        return value.to(device)

    return {key: move(value) for key, value in inputs.items()}


def ground_text_to_box(image: Image.Image, text: str, *, threshold: float = 0.05) -> dict[str, Any]:
    """OWL-ViT: best-scoring box for a free-text object query."""
    query = (text or "").strip()
    if not query:
        raise HTTPException(400, "Missing object text")
    backend = _load_grounding_backend()
    torch = backend["torch"]
    processor = backend["processor"]
    model = backend["model"]
    device = backend["device"]

    labels = [query]
    if not query.lower().startswith(("a ", "an ", "the ")):
        labels.append(f"a photo of {query}")
    try:
        inputs = processor(text=[labels], images=image, return_tensors="pt")
        with _infer_locks["grounding"], torch.no_grad():
            outputs = model(**_model_inputs_to_device(inputs, device, torch))
        target_sizes = torch.tensor([[image.height, image.width]])
        if hasattr(processor, "post_process_object_detection"):
            results = processor.post_process_object_detection(
                outputs=outputs, target_sizes=target_sizes, threshold=float(threshold),
            )
        elif hasattr(processor, "post_process_grounded_object_detection"):
            results = processor.post_process_grounded_object_detection(
                outputs=outputs, target_sizes=target_sizes, threshold=float(threshold),
                text_labels=[labels],
            )
        else:
            raise HTTPException(500, "Installed Transformers does not expose OWL-ViT object detection post-processing")
        boxes = results[0].get("boxes")
        scores = results[0].get("scores")
        labels_idx = results[0].get("labels")
        text_labels = results[0].get("text_labels") or results[0].get("labels_text")
        if boxes is None or scores is None or len(boxes) == 0:
            raise HTTPException(404, f"No visible object matched '{query}'")
        idx = int(torch.argmax(scores).item())
        box = [float(v) for v in boxes[idx].detach().cpu().tolist()]
        label_idx = int(labels_idx[idx].detach().cpu().item()) if labels_idx is not None and len(labels_idx) else 0
        label = labels[min(label_idx, len(labels) - 1)]
        if text_labels and len(text_labels) > idx:
            label = str(text_labels[idx])
        return {
            "box": box,
            "score": float(scores[idx].detach().cpu().item()),
            "label": label,
            "model": backend["model_id"],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ground_text_to_box failed")
        raise HTTPException(500, f"Object mask failed: {exc}") from exc


def sam_mask(image: Image.Image, points: list, box: Any, text: str) -> dict[str, Any]:
    """Binary mask (white = selected) from click points, a box, or object text."""
    grounded = None
    if not points and not box and text:
        grounded = ground_text_to_box(image, text)
        box = grounded["box"]
    if not points and not box:
        raise HTTPException(400, "Provide at least one point, box, or object text")

    backend = _load_sam_backend()
    torch = backend["torch"]
    processor = backend["processor"]
    model = backend["model"]
    device = backend["device"]

    kwargs: dict[str, Any] = {"return_tensors": "pt"}
    input_points = []
    if points:
        if not isinstance(points, list):
            raise HTTPException(400, "Invalid point format")
        input_labels = []
        for p in points:
            try:
                input_points.append([float(p["x"]), float(p["y"])])
                input_labels.append(int(p.get("label", 1)))
            except Exception as exc:
                raise HTTPException(400, "Invalid point format") from exc
        kwargs["input_points"] = [input_points]
        kwargs["input_labels"] = [input_labels]
    if box:
        if not isinstance(box, list) or len(box) != 4:
            raise HTTPException(400, "Box must be [x1, y1, x2, y2]")
        try:
            kwargs["input_boxes"] = [[[float(v) for v in box]]]
        except Exception as exc:
            raise HTTPException(400, "Invalid box format") from exc

    try:
        inputs = processor(image, **kwargs)
        with _infer_locks["sam"], torch.no_grad():
            outputs = model(**_model_inputs_to_device(inputs, device, torch))
        masks = processor.image_processor.post_process_masks(
            outputs.pred_masks.detach().cpu(),
            inputs["original_sizes"].detach().cpu(),
            inputs["reshaped_input_sizes"].detach().cpu(),
        )
        mask_tensor = masks[0]
        while getattr(mask_tensor, "ndim", 0) > 3:
            mask_tensor = mask_tensor[0]
        if getattr(mask_tensor, "ndim", 0) == 3:
            scores = outputs.iou_scores.detach().cpu()[0]
            while getattr(scores, "ndim", 0) > 1:
                scores = scores[0]
            # SAM returns several candidates per click; the top-IoU one is
            # often the whole image. Prefer one that contains the click and
            # is not near-full-frame, penalising broad masks.
            point_xy = None
            if input_points:
                try:
                    point_xy = (int(round(input_points[0][0])), int(round(input_points[0][1])))
                except Exception:
                    point_xy = None
            best_idx = 0
            best_rank = None
            total_px = max(1, int(mask_tensor.shape[-1]) * int(mask_tensor.shape[-2]))
            for i in range(int(mask_tensor.shape[0])):
                candidate = mask_tensor[i]
                area_ratio = float(candidate.sum().item()) / float(total_px)
                if area_ratio >= 0.985:
                    continue
                if point_xy:
                    px = max(0, min(int(candidate.shape[-1]) - 1, point_xy[0]))
                    py = max(0, min(int(candidate.shape[-2]) - 1, point_xy[1]))
                    if not bool(candidate[py, px].item()):
                        continue
                score = float(scores[min(i, len(scores) - 1)].item()) if len(scores) else 0.0
                rank = score - (area_ratio * 0.35)
                if best_rank is None or rank > best_rank:
                    best_rank = rank
                    best_idx = i
            if best_rank is None and len(scores):
                best_idx = int(torch.argmax(scores).item())
            mask_tensor = mask_tensor[min(best_idx, mask_tensor.shape[0] - 1)]
        mask_array = (mask_tensor.numpy() > 0).astype("uint8") * 255
        mask_img = Image.fromarray(mask_array, mode="L")
        if mask_img.size != image.size:
            mask_img = mask_img.resize(image.size, Image.NEAREST)
        bbox = mask_img.getbbox()
        from app.imaging import pil_to_b64

        result: dict[str, Any] = {
            "mask": pil_to_b64(mask_img),
            "bbox": list(bbox) if bbox else None,
            "model": backend["model_id"],
            "device": device,
        }
        if grounded:
            result["grounding"] = grounded
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("sam_mask failed")
        raise HTTPException(500, f"SAM mask failed: {exc}") from exc


# ------------------------------------------------------------ enhance-face ---

def _gfpgan_enhance(rgb: Image.Image) -> Image.Image:
    """GFPGAN v1.4 face restore. Raises ImportError when gfpgan/cv2 are missing."""
    import numpy as np

    patch_realesrgan_torchvision_compat()  # gfpgan imports basicsr too
    import cv2
    from gfpgan import GFPGANer

    def build():
        model_root = os.path.join(tempfile.gettempdir(), "gfpgan_models")
        os.makedirs(model_root, exist_ok=True)
        return GFPGANer(
            model_path=_GFPGAN_V14,
            upscale=1,
            arch="clean",
            channel_multiplier=2,
            bg_upsampler=None,
            model_rootpath=model_root,
        )

    restorer = _cached("gfpgan", build)
    img_bgr = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
    with _infer_locks["gfpgan"]:
        _, _, output = restorer.enhance(img_bgr, has_aligned=False, only_center_face=False, paste_back=True)
    return Image.fromarray(cv2.cvtColor(output, cv2.COLOR_BGR2RGB))


def pil_enhance(rgb: Image.Image) -> Image.Image:
    """No-AI fallback: denoise, sharpen, then small contrast/colour/brightness lifts."""
    enhanced = rgb.filter(ImageFilter.MedianFilter(size=3))
    enhanced = enhanced.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    enhanced = ImageEnhance.Contrast(enhanced).enhance(1.15)
    enhanced = ImageEnhance.Color(enhanced).enhance(1.1)
    return ImageEnhance.Brightness(enhanced).enhance(1.05)


def enhance_face(rgb: Image.Image) -> tuple[Image.Image, str]:
    """Return (image, method) where method is "gfpgan" or "pil"."""
    try:
        return _gfpgan_enhance(rgb), "gfpgan"
    except ImportError:
        logger.info("GFPGAN not available, using the Pillow enhancement fallback")
        return pil_enhance(rgb), "pil"
