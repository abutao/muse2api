"""Prompt builders for local muse_engine image/video (aligned with muse2api)."""

from __future__ import annotations

import collections
import re
from typing import Any

from app.muse_engine.engine import MAX_REFERENCE_IMAGES, reference_kind

_KIND_LABELS = {
    "image": ("张", "图片"),
    "video": ("段", "视频"),
    "audio": ("段", "音频"),
    "document": ("份", "文档"),
}
_KIND_TOKENS = {"image": "图", "video": "视频", "audio": "音频", "document": "文档"}
_REFERENCE_TOKEN = re.compile(r"\[(?:图|视频|音频|文档)\d+\]")


class PromptError(ValueError):
    pass


def _image_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        candidate = (
            value.get("image_url")
            or value.get("video_url")
            or value.get("audio_url")
            or value.get("file_url")
            or value.get("url")
            or value.get("b64_json")
            or value.get("data")
        )
        if isinstance(candidate, dict):
            candidate = candidate.get("url")
        return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None
    return None


def reference_list(
    images: list | None,
    legacy: list[Any],
    field: str = "images",
    extra: dict[str, list | None] | None = None,
) -> list[str]:
    primary = next((ref for value in legacy if (ref := _image_value(value))), None)
    if primary and images:
        # 兼容旧客户端：同时传 images 和 image/reference_image 时，
        # 若单图就是 images[0] 则直接忽略旧字段，不再报 400。
        first = _image_value(images[0]) if images else None
        if first and first == primary:
            primary = None
        else:
            raise PromptError(f"{field} 不能与旧版单图字段同时使用")
    groups: list[tuple[str, list]] = []
    if primary:
        groups.append((field, [primary]))
    elif images is not None:
        groups.append((field, images))
    for name, values in (extra or {}).items():
        if values is not None:
            if not isinstance(values, list):
                raise PromptError(f"{name} 必须是数组")
            groups.append((name, values))
    total = sum(len(values) for _, values in groups)
    if total > MAX_REFERENCE_IMAGES:
        raise PromptError(f"参考素材最多 {MAX_REFERENCE_IMAGES} 个（当前 {total} 个）")
    refs: list[str] = []
    for name, values in groups:
        for index, value in enumerate(values):
            ref = _image_value(value)
            if not ref:
                raise PromptError(f"{name}[{index}] 不是有效素材引用")
            refs.append(ref)
    return refs


def reference_kinds(prepared: list[str]) -> list[str]:
    out = []
    for ref in prepared:
        if ref.startswith("data:") and ";" in ref[5:]:
            mime = ref[5:].split(";", 1)[0]
            out.append(reference_kind(mime))
        else:
            out.append("image")
    return out


def _describe_kinds(kinds: list[str]) -> str:
    counts = collections.Counter(kinds)
    return "、".join(
        f"{counts[k]} {_KIND_LABELS[k][0]}{_KIND_LABELS[k][1]}"
        for k in ("image", "video", "audio", "document")
        if counts[k]
    )


def _reference_legend(kinds: list[str]) -> str:
    seen: collections.Counter = collections.Counter()
    parts = []
    for index, kind in enumerate(kinds, 1):
        seen[kind] += 1
        parts.append(
            f"[{_KIND_TOKENS[kind]}{seen[kind]}] 是第 {index} 个附件（{_KIND_LABELS[kind][1]}）"
        )
    return "附件对照：" + "，".join(parts)


def build_image_prompt(
    prompt: str,
    *,
    size: str | None = None,
    aspect_ratio: str | None = None,
    extra: str | None = None,
    kinds: list[str] | None = None,
    ref_count: int = 0,
) -> str:
    kinds = kinds or (["image"] * ref_count)
    has_ref = ref_count > 0
    p = (prompt or "").strip()
    if has_ref and any(k != "image" for k in kinds):
        parts = [
            f"基于我本次上传附带的参考素材（{_describe_kinds(kinds)}；{_reference_legend(kinds)}；"
            f"只使用本次上传的这些附件，各附件的用途以下面的描述为准）"
            f"进行生图/编辑，输出一张图片：{p}"
        ]
    elif ref_count > 1 and _REFERENCE_TOKEN.search(p):
        parts = [
            f"基于我本次上传附带的参考图片（{_reference_legend(kinds)}）进行生图/编辑：{p}"
        ]
    elif has_ref:
        parts = [f"基于我本次上传附带的参考图片进行生图/编辑：{p}"]
    else:
        parts = [
            "全新文生图创作（当前未提供任何参考图，请勿查找历史相册或向用户索要原图，"
            f"直接根据文字描述从零绘制生成一张全新图片）：{p}"
        ]
    ar = (aspect_ratio or "").strip().lower()
    sz = (size or "").strip().lower()
    if any(k in ar or k in sz for k in ("9:16", "9/16", "portrait", "竖屏", "720x1280", "1080x1920")):
        parts.append(
            "【画面构图与比例要求】：严格 9:16 竖屏满屏画幅（9:16 vertical portrait aspect ratio）"
        )
    elif any(k in ar or k in sz for k in ("16:9", "16/9", "landscape", "横屏", "1280x720", "1920x1080")):
        parts.append("【画面构图与比例要求】：16:9 宽屏横屏画幅（16:9 widescreen landscape aspect ratio）")
    elif any(k in ar or k in sz for k in ("1:1", "square", "正方形", "1024x1024")):
        parts.append("【画面构图与比例要求】：1:1 正方形画幅（1:1 square aspect ratio）")
    elif aspect_ratio:
        parts.append(f"【画面构图与比例要求】：{aspect_ratio} 画面比例")
    elif size:
        parts.append(f"尺寸/比例：{size}")
    if extra:
        parts.append(str(extra))
    return "，".join(parts)


def build_video_prompt(
    prompt: str,
    *,
    duration: int | None = None,
    size: str | None = None,
    aspect_ratio: str | None = None,
    resolution: str | None = None,
    extra: str | None = None,
    kinds: list[str] | None = None,
    ref_count: int = 0,
) -> str:
    user_prompt = (prompt or "").strip()
    ar = (aspect_ratio or "").strip().lower()
    sz = (size or "").strip().lower()
    dur = duration or 6
    has_ref = ref_count > 0
    kinds = kinds or (["image"] * ref_count)
    if any(k != "image" for k in kinds):
        reference_instruction = (
            f"基于我本次上传的参考素材（{_describe_kinds(kinds)}）生成视频"
            f"（{_reference_legend(kinds)}；只使用本次上传的这些附件，严禁使用历史素材；"
            "各附件的用途以下面的描述为准）"
        )
    elif ref_count > 1 and _REFERENCE_TOKEN.search(user_prompt):
        reference_instruction = (
            f"基于我本次上传的 {ref_count} 张参考图片生成视频（{_reference_legend(kinds)}；"
            "各图片的用途以下面的描述为准，未说明时以第一张作为主要起始画面；严禁使用历史图片或其他图像）"
        )
    elif ref_count > 1:
        reference_instruction = (
            f"基于我本次上传的 {ref_count} 张参考图片生成视频（以第一张作为主要起始画面，"
            "其余图片作为主体、场景与风格参考，严禁使用历史图片或其他图像）"
        )
    else:
        reference_instruction = (
            "基于我本次上传的参考图片附件作为第一帧参考图（严禁使用历史图片或任何其他图像，"
            "必须严格以我当前刚刚上传并附带在此处的这张图片为起始帧）"
        )
    gen_word = "图生视频" if all(k == "image" for k in kinds) else "视频"
    is_vertical = any(
        k in ar or k in sz for k in ("9:16", "9/16", "portrait", "竖屏", "720x1280", "1080x1920")
    )
    parts: list[str] = []
    if is_vertical:
        if has_ref:
            parts.append(
                f"{reference_instruction}：生成一个严格9:16竖屏手机满屏的动态{gen_word}"
                f"（9:16 vertical portrait video，720x1280，时长严格为 {dur} 秒）：{user_prompt}"
            )
        else:
            parts.append(
                f"全新文生视频创作（纯文本全新生成，严禁参考任何历史图片或上下文）："
                f"生成一个严格9:16竖屏手机满屏视频（时长严格为 {dur} 秒）：{user_prompt}"
            )
    elif any(k in ar or k in sz for k in ("16:9", "16/9", "landscape", "横屏", "1280x720", "1920x1080")):
        if has_ref:
            parts.append(
                f"{reference_instruction}：生成一个16:9宽屏横屏{gen_word}"
                f"（时长严格为 {dur} 秒）：{user_prompt}"
            )
        else:
            parts.append(
                f"全新文生视频创作（纯文本全新生成）：生成一个16:9横屏宽屏视频"
                f"（时长严格为 {dur} 秒）：{user_prompt}"
            )
    else:
        if has_ref:
            parts.append(
                f"{reference_instruction}：生成动态{gen_word}（时长严格为 {dur} 秒）：{user_prompt}"
            )
        else:
            parts.append(
                f"全新文生视频创作（纯文本全新生成）：生成一个视频（时长严格为 {dur} 秒）：{user_prompt}"
            )
    if resolution:
        parts.append(f"画质规格：{resolution}")
    if extra:
        parts.append(str(extra))
    return "，".join(parts)
