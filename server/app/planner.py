"""Quick Edit planner: turn a plain-language request into editor actions.

The language model never edits pixels. It answers with JSON naming actions
from a fixed vocabulary; the browser runs them as adjustment layers or
existing AI tools, so every step is visible and undoable. This module builds
the prompt and validates whatever the model returns.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

MAX_ACTIONS = 8
MAX_PROMPT_CHARS = 2000

# Actions that only need the browser.
LOCAL_ACTIONS = {
    "brightness_contrast", "exposure", "white_balance", "hue_saturation",
    "vibrance", "black_white", "shadows_highlights", "levels", "curves",
    "color_balance", "gradient_map", "rotate", "flip",
}
# Actions that need the AI server, keyed to the capability that enables them.
AI_ACTIONS = {
    "remove_background": "remove_bg",
    "upscale": "upscale_local",
    "denoise": "denoise",
    "sharpen": "sharpen",
    "enhance_face": "enhance_face",
    "style_edit": "image_edit_model",
}

SYSTEM_PROMPT = """You are the planning engine of Odysseus-Edit, a layered image editor.
The user describes an edit in plain language. You do not edit pixels: you answer
with a JSON plan of actions that the editor runs. Adjustments are added as
non-destructive adjustment layers over the whole image.

Reply with ONE JSON object and nothing else:
{"message": "<one short sentence for the user, in the user's language>",
 "actions": [<action>, ...]}

Actions (numbers are slider units; omit a field to keep it neutral):
- {"type":"brightness_contrast","brightness":-100..100,"contrast":-100..100}
- {"type":"exposure","ev":-5..5}
- {"type":"white_balance","temperature":-100..100,"tint":-100..100}
    temperature > 0 is warmer (more orange), < 0 cooler (more blue);
    tint > 0 is more magenta, < 0 more green
- {"type":"hue_saturation","hue":-180..180,"saturation":-100..100,"lightness":-100..100}
- {"type":"vibrance","amount":-100..100}   boosts muted colours more than saturated ones
- {"type":"black_white"}
- {"type":"shadows_highlights","shadows":-100..100,"highlights":-100..100}
    shadows > 0 lifts dark areas; highlights < 0 recovers bright areas
- {"type":"levels","black":0..253,"white":2..255,"gamma":0.1..9.9}
- {"type":"curves","points":[[x,y],...]}   0..255, e.g. S-curve [[0,0],[64,48],[192,208],[255,255]]
- {"type":"color_balance","shadows":{"r":..,"g":..,"b":..},"midtones":{...},"highlights":{...}}   each -100..100
- {"type":"gradient_map","shadows":"#rrggbb","highlights":"#rrggbb"}
- {"type":"rotate","degrees":90|180|270}   clockwise
- {"type":"flip","axis":"horizontal"|"vertical"}
{ai_actions}
Rules:
- Use the fewest actions that do the job, at most 8, in the order to apply them.
- Prefer subtle values (roughly 10-35) unless the user asks for a strong change.
- Only use the AI actions listed above as available. If the request needs an
  unavailable tool, return no actions and say what is missing in "message".
- If the request is not an image edit or is unclear, return no actions and ask
  a short question in "message".
- "label" (optional, max 40 chars) names the layer an adjustment creates.
- Output JSON only: no markdown, no comments, no text outside the object."""

AI_ACTION_LINES = {
    "remove_background": '- {"type":"remove_background"}   cut the subject out, transparent background',
    "upscale": '- {"type":"upscale","scale":2|4}   AI super-resolution',
    "denoise": '- {"type":"denoise","strength":0.05..1}',
    "sharpen": '- {"type":"sharpen","amount":10..100}',
    "enhance_face": '- {"type":"enhance_face"}   restore faces in portraits',
    "style_edit": '- {"type":"style_edit","prompt":"<English description of the new look>","strength":0.1..0.9}\n'
                  '    generative full-image restyle with an image model; use only when adjustments cannot do it',
}


def available_ai_actions(capabilities: dict[str, Any]) -> list[str]:
    """AI action names usable with the given server capabilities."""
    local = capabilities.get("local_tools") or {}
    has_image_edit = any(
        "image-edit" in (p.get("capabilities") or []) for p in capabilities.get("image_providers") or []
    )
    names = []
    for action, need in AI_ACTIONS.items():
        ok = has_image_edit if need == "image_edit_model" else bool(local.get(need))
        if ok:
            names.append(action)
    return names


def build_system_prompt(ai_actions: list[str]) -> str:
    if ai_actions:
        lines = "AI actions available on this server:\n" + "\n".join(AI_ACTION_LINES[a] for a in ai_actions)
    else:
        lines = "No AI actions are available on this server (no background removal, upscaling or generative edits)."
    return SYSTEM_PROMPT.replace("{ai_actions}", lines + "\n")


def _clean_context(context: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only the small, known document facts the browser sends."""
    context = context if isinstance(context, dict) else {}
    out: dict[str, Any] = {}
    for key in ("width", "height"):
        value = context.get(key)
        if isinstance(value, (int, float)) and math.isfinite(value):
            out[key] = int(value)
    layers = []
    for layer in (context.get("layers") or [])[:25]:
        if not isinstance(layer, dict):
            continue
        item = {
            "name": str(layer.get("name") or "")[:60],
            "kind": str(layer.get("kind") or "raster")[:20],
            "visible": layer.get("visible") is not False,
        }
        if layer.get("adjustment"):
            item["adjustment"] = str(layer["adjustment"])[:30]
        layers.append(item)
    if layers:
        out["layers_bottom_to_top"] = layers
    if context.get("active_layer"):
        out["active_layer"] = str(context["active_layer"])[:60]
    out["has_selection"] = bool(context.get("has_selection"))
    return out


def build_user_message(prompt: str, context: dict[str, Any] | None, has_image: bool) -> str:
    doc = json.dumps(_clean_context(context), ensure_ascii=False)
    seen = "The attached image is a preview of the current canvas." if has_image else \
        "You cannot see the image; plan from the request and the document facts."
    return f"Document: {doc}\n{seen}\n\nRequest: {prompt.strip()[:MAX_PROMPT_CHARS]}"


# ── Validation ─────────────────────────────────────────────────────────────

def _num(value: Any, lo: float, hi: float, default: float | None = None) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(n):
        return default
    return max(lo, min(hi, n))


def _rgb(value: Any) -> dict[str, float]:
    value = value if isinstance(value, dict) else {}
    return {c: _num(value.get(c), -100, 100, 0) for c in ("r", "g", "b")}


def _hex(value: Any, default: str) -> str:
    text = str(value or "")
    return text if re.fullmatch(r"#[0-9a-fA-F]{6}", text) else default


def _label(action: dict[str, Any]) -> dict[str, str]:
    label = action.get("label")
    if isinstance(label, str) and label.strip():
        return {"label": label.strip()[:40]}
    return {}


def _validate_one(action: Any, allowed_ai: set[str]) -> dict[str, Any] | None:
    if not isinstance(action, dict):
        return None
    kind = str(action.get("type") or "").strip().lower()
    a = action
    if kind == "brightness_contrast":
        return {"type": kind, "brightness": _num(a.get("brightness"), -100, 100, 0),
                "contrast": _num(a.get("contrast"), -100, 100, 0), **_label(a)}
    if kind == "exposure":
        return {"type": kind, "ev": _num(a.get("ev"), -5, 5, 0), **_label(a)}
    if kind == "white_balance":
        return {"type": kind, "temperature": _num(a.get("temperature"), -100, 100, 0),
                "tint": _num(a.get("tint"), -100, 100, 0), **_label(a)}
    if kind == "hue_saturation":
        return {"type": kind, "hue": _num(a.get("hue"), -180, 180, 0),
                "saturation": _num(a.get("saturation"), -100, 100, 0),
                "lightness": _num(a.get("lightness"), -100, 100, 0), **_label(a)}
    if kind == "vibrance":
        return {"type": kind, "amount": _num(a.get("amount"), -100, 100, 0), **_label(a)}
    if kind == "black_white":
        return {"type": kind, **_label(a)}
    if kind == "shadows_highlights":
        return {"type": kind, "shadows": _num(a.get("shadows"), -100, 100, 0),
                "highlights": _num(a.get("highlights"), -100, 100, 0), **_label(a)}
    if kind == "levels":
        black = _num(a.get("black"), 0, 253, 0)
        white = _num(a.get("white"), black + 2, 255, 255)
        return {"type": kind, "black": round(black), "white": round(white),
                "gamma": _num(a.get("gamma"), 0.1, 9.9, 1.0), **_label(a)}
    if kind == "curves":
        points = []
        for point in (a.get("points") or [])[:16]:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                x, y = _num(point[0], 0, 255), _num(point[1], 0, 255)
                if x is not None and y is not None:
                    points.append([round(x), round(y)])
        if len(points) < 2:
            return None
        return {"type": kind, "points": sorted(points), **_label(a)}
    if kind == "color_balance":
        tones = {t: _rgb(a.get(t)) for t in ("shadows", "midtones", "highlights") if isinstance(a.get(t), dict)}
        if not tones:
            tone = a.get("tone") if a.get("tone") in ("shadows", "midtones", "highlights") else "midtones"
            tones = {tone: _rgb(a)}
        return {"type": kind, **tones, **_label(a)}
    if kind == "gradient_map":
        return {"type": kind, "shadows": _hex(a.get("shadows"), "#000000"),
                "highlights": _hex(a.get("highlights"), "#ffffff"), **_label(a)}
    if kind == "rotate":
        degrees = int(_num(a.get("degrees"), -360, 360, 90)) % 360
        return {"type": kind, "degrees": degrees if degrees in (90, 180, 270) else 90}
    if kind == "flip":
        axis = "vertical" if str(a.get("axis") or "").lower().startswith("v") else "horizontal"
        return {"type": kind, "axis": axis}
    if kind not in allowed_ai:
        return None
    if kind == "remove_background":
        return {"type": kind}
    if kind == "upscale":
        return {"type": kind, "scale": 4 if _num(a.get("scale"), 1, 8, 2) >= 4 else 2}
    if kind == "denoise":
        return {"type": kind, "strength": _num(a.get("strength"), 0.05, 1, 0.55)}
    if kind == "sharpen":
        return {"type": kind, "amount": _num(a.get("amount"), 10, 100, 65)}
    if kind == "enhance_face":
        return {"type": kind}
    if kind == "style_edit":
        prompt = str(a.get("prompt") or "").strip()[:500]
        if not prompt:
            return None
        return {"type": kind, "prompt": prompt, "strength": _num(a.get("strength"), 0.1, 0.9, 0.55)}
    return None


def validate_plan(raw: dict[str, Any], allowed_ai: list[str]) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Return (message, valid actions, dropped action types)."""
    message = str(raw.get("message") or "").strip()[:400]
    actions_in = raw.get("actions")
    if not isinstance(actions_in, list):
        actions_in = []
    allowed = set(allowed_ai)
    actions: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in actions_in[: MAX_ACTIONS * 2]:
        valid = _validate_one(item, allowed)
        if valid is None:
            dropped.append(str(item.get("type") if isinstance(item, dict) else type(item).__name__))
            continue
        if len(actions) < MAX_ACTIONS:
            actions.append(valid)
    return message, actions, dropped
