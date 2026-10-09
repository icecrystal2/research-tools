#!/usr/bin/env python3
"""Pixel-level boundary and whitespace checks for raster figure exports."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from PIL import Image, ImageChops, ImageDraw
except ImportError as exc:  # pragma: no cover - exercised when optional dependency is absent
    Image = ImageChops = ImageDraw = None  # type: ignore[assignment]
    _PIL_ERROR = exc
else:
    _PIL_ERROR = None

try:
    from layout_qa import SCHEMA_VERSION, _issue, _space_balance_checks, _space_balance_config, _thresholds, _write_json
except ImportError:  # pragma: no cover - package/import fallback
    from .layout_qa import SCHEMA_VERSION, _issue, _space_balance_checks, _space_balance_config, _thresholds, _write_json


def _require_pillow() -> None:
    if Image is None:
        raise ImportError("栅格检查需要 Pillow；请在运行环境安装 Pillow") from _PIL_ERROR


def _estimate_background(image: Any) -> tuple[int, int, int, int]:
    width, height = image.size
    points = []
    span_x = max(1, min(width, 12))
    span_y = max(1, min(height, 12))
    for x in range(span_x):
        points.extend((x, 0, x, height - 1))
    for y in range(span_y):
        points.extend((0, y, width - 1, y))
    samples = [image.getpixel((points[i], points[i + 1])) for i in range(0, len(points), 2)]
    buckets: dict[tuple[int, int, int, int], int] = {}
    for sample in samples:
        rgba = tuple(sample) if len(sample) == 4 else (*tuple(sample)[:3], 255)
        key = tuple(int(channel // 8 * 8) for channel in rgba)
        buckets[key] = buckets.get(key, 0) + 1
    chosen = max(buckets, key=buckets.get) if buckets else (255, 255, 255, 255)
    return tuple(min(255, value + 4) for value in chosen)  # type: ignore[return-value]


def _mask(image: Any, background: tuple[int, int, int, int], tolerance: int) -> Any:
    rgba = image.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, background)
    diff = ImageChops.difference(rgba, bg).convert("L")
    alpha = rgba.getchannel("A")
    # Transparent pixels are background regardless of their RGB payload. Keep
    # the color difference for opaque pixels instead of treating alpha=255 as
    # foreground (which would mark every opaque background pixel).
    diff = ImageChops.multiply(
        diff,
        alpha.point(lambda value: 255 if value >= 12 else 0),
    )
    return diff.point(lambda value: 255 if value > max(0, tolerance) else 0)


def _edge_fraction(mask: Any) -> tuple[float, dict[str, int], int]:
    width, height = mask.size
    bbox = mask.getbbox()
    total = 0
    edge = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    pixels = mask.load()
    for y in range(height):
        for x in range(width):
            if pixels[x, y]:
                total += 1
                if x == 0:
                    edge["left"] += 1
                if x == width - 1:
                    edge["right"] += 1
                if y == 0:
                    edge["top"] += 1
                if y == height - 1:
                    edge["bottom"] += 1
    return (max(edge.values()) / total if total else 0.0), edge, total


def _draw_overlay(image: Any, output: Path, bbox: tuple[int, int, int, int] | None, edge: Mapping[str, int], issues: list[Mapping[str, Any]], geometry: list[Mapping[str, Any]] | None = None, page: Mapping[str, Any] | None = None, force: bool = False) -> None:
    if output.exists() and not force:
        raise FileExistsError(f"overlay 已存在，使用 --force 覆盖: {output}")
    canvas = image.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")
    width, height = canvas.size
    if bbox:
        draw.rectangle(bbox, outline=(0, 110, 220, 230), width=max(1, round(min(width, height) / 500)))
    for side, count in edge.items():
        if count <= 0:
            continue
        if side == "left":
            draw.line((0, 0, 0, height), fill=(220, 50, 40, 180), width=2)
        elif side == "right":
            draw.line((width - 1, 0, width - 1, height), fill=(220, 50, 40, 180), width=2)
        elif side == "top":
            draw.line((0, 0, width, 0), fill=(220, 50, 40, 180), width=2)
        else:
            draw.line((0, height - 1, width, height - 1), fill=(220, 50, 40, 180), width=2)
    if geometry and page:
        page_width = float(page.get("width_mm", 0) or 0)
        page_height = float(page.get("height_mm", 0) or 0)
        if page_width > 0 and page_height > 0:
            for item in geometry:
                try:
                    if "bbox_mm" in item and isinstance(item["bbox_mm"], (list, tuple)):
                        left_mm, top_mm, width_mm, height_mm = (float(value) for value in item["bbox_mm"])
                        right_mm, bottom_mm = left_mm + width_mm, top_mm + height_mm
                    else:
                        left_mm, top_mm = float(item["left_mm"]), float(item["top_mm"])
                        right_mm, bottom_mm = float(item.get("right_mm", left_mm + float(item["width_mm"]))), float(item.get("bottom_mm", top_mm + float(item["height_mm"])))
                    x0 = left_mm / page_width * width
                    y0 = top_mm / page_height * height
                    x1 = right_mm / page_width * width
                    y1 = bottom_mm / page_height * height
                except (KeyError, TypeError, ValueError):
                    continue
                status = "pass"
                for issue in issues:
                    if str(item.get("id")) in [str(value) for value in issue.get("elements", [])]:
                        status = str(issue.get("status", "warn"))
                        if status == "fail":
                            break
                color = {"fail": (220, 40, 35, 220), "warn": (230, 140, 20, 220), "unknown": (130, 80, 190, 220)}.get(status, (30, 150, 90, 200))
                draw.rectangle((x0, y0, x1, y1), outline=color, width=2)
                anchors = item.get("anchors")
                if isinstance(anchors, Mapping):
                    for value in anchors.values():
                        if not isinstance(value, (list, tuple)) or len(value) != 2:
                            continue
                        try:
                            ax = float(value[0]) / page_width * width
                            ay = float(value[1]) / page_height * height
                        except (TypeError, ValueError):
                            continue
                        radius = max(3, round(min(width, height) / 180))
                        draw.ellipse((ax - radius, ay - radius, ax + radius, ay + radius), outline=(190, 30, 180, 230), width=2)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def audit_raster(path: str | Path, contract: Mapping[str, Any] | None = None, *, overlay_path: str | Path | None = None, geometry: list[Mapping[str, Any]] | None = None, page: Mapping[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Audit a raster image. Semantic object checks remain unknown without geometry."""
    _require_pillow()
    source = Path(path)
    if contract is not None and not isinstance(contract, Mapping):
        raise ValueError("布局合同根节点必须是对象")
    contract = contract or {}
    thresholds = _thresholds(contract)
    if geometry is None:
        candidate = contract.get("layout_geometry")
        if isinstance(candidate, Mapping):
            geometry = candidate.get("elements") if isinstance(candidate.get("elements"), list) else None
        elif isinstance(contract.get("elements"), list):
            geometry = contract.get("elements")
    if page is None:
        candidate_page = contract.get("page")
        if isinstance(candidate_page, Mapping):
            page = candidate_page
        elif isinstance(contract.get("layout_geometry"), Mapping):
            nested_page = contract["layout_geometry"].get("page")
            if isinstance(nested_page, Mapping):
                page = nested_page
    with Image.open(source) as opened:
        image = opened.convert("RGBA")
        width, height = image.size
        source_dpi = opened.info.get("dpi")
        derived_page = None
        if isinstance(source_dpi, (list, tuple)) and len(source_dpi) >= 2:
            x_dpi = _number_or_none(source_dpi[0])
            y_dpi = _number_or_none(source_dpi[1])
            if x_dpi and y_dpi and x_dpi > 0 and y_dpi > 0:
                derived_page = {"width_mm": width / x_dpi * 25.4, "height_mm": height / y_dpi * 25.4}
        if page is None and derived_page is not None:
            page = derived_page
        if width * height > 40_000_000:
            scale = math.sqrt(40_000_000 / (width * height))
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        background = _estimate_background(image)
        raw_tolerance = _number_or_none(thresholds.get("raster_background_tolerance", 18))
        tolerance = max(0, int(raw_tolerance if raw_tolerance is not None else 18))
        mask = _mask(image, background, tolerance)
        bbox = mask.getbbox()
        edge_fraction, edge_counts, total = _edge_fraction(mask)
        content_fraction = total / (mask.size[0] * mask.size[1]) if mask.size[0] and mask.size[1] else 0.0
        bbox_fraction = (
            ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / (mask.size[0] * mask.size[1]))
            if bbox and mask.size[0] and mask.size[1]
            else 0.0
        )
        checks: list[dict[str, Any]] = []
        if bbox is None:
            checks.append(_issue("raster:content", "unknown", "未检测到明显前景内容；可能是空图或背景估计不可靠", suggestion="确认输入不是全白/全透明图，并检查背景阈值。"))
        else:
            checks.append(_issue("raster:content", "pass", "检测到前景内容", metrics={"content_bbox_px": list(bbox), "content_fraction": content_fraction, "foreground_pixels": total}))
        raw_edge_limit = _number_or_none(thresholds.get("raster_edge_fraction_warn", 0.001))
        edge_limit = max(0.0, raw_edge_limit if raw_edge_limit is not None else 0.001)
        if edge_fraction >= edge_limit:
            checks.append(_issue("raster:edge-touch", "warn", "前景像素触碰图像边缘，存在裁切风险", metrics={"max_edge_fraction": edge_fraction, "edge_pixels": edge_counts}, suggestion="在目标版面尺寸下检查是否需要增加边距；确认不是有意的边框。"))
        else:
            checks.append(_issue("raster:edge-touch", "pass", "未发现显著的边缘触碰", metrics={"max_edge_fraction": edge_fraction, "edge_pixels": edge_counts}))
        has_geometry = bool(geometry and page)
        if not has_geometry:
            checks.append(_issue("raster:semantic-overlap", "unknown", "栅格像素不能可靠分离语义对象，无法判断对象级重叠", suggestion="从 Matplotlib 注册表或 Origin layout_geometry 提供对象包围盒。"))
            checks.append(_issue("raster:semantic-anchor", "unknown", "栅格像素不包含可验证的箭头/标注语义", suggestion="在生成端记录箭头实际端点和目标点。"))
        # A deliberately modest heuristic: very sparse content is a warning, not a verdict.
        blank_limit = _number_or_none(thresholds.get("blank_warn_content_fraction"))
        if blank_limit is not None and bbox_fraction < blank_limit:
            checks.append(_issue("raster:blank-space", "warn", "内容包围盒占页面比例偏低，可能存在大片空白", metrics={"content_bbox_fraction": bbox_fraction, "ink_fraction": content_fraction, "blank_fraction_by_bbox": 1 - bbox_fraction}, suggestion="结合内容包围盒和最终版面人工判断，不要仅凭像素占比裁剪。"))
        else:
            checks.append(_issue("raster:blank-space", "pass", "内容包围盒占比未低于合同阈值", metrics={"content_bbox_fraction": bbox_fraction, "ink_fraction": content_fraction, "blank_fraction_by_bbox": 1 - bbox_fraction}))

        # Without semantic geometry, the raster still provides reliable page
        # coordinates for a coarse directional-space heuristic.  Keep this
        # separate from the geometry-backed check below so the report states
        # exactly what evidence was used.
        space_config = None
        raw_space = contract.get("space_balance", {})
        if raw_space is not False and not has_geometry and bbox is not None:
            space_config = _space_balance_config(contract)
            if space_config.get("enabled", True):
                space_page_width = float(page.get("width_mm")) if isinstance(page, Mapping) and _number_or_none(page.get("width_mm")) else float(width)
                space_page_height = float(page.get("height_mm")) if isinstance(page, Mapping) and _number_or_none(page.get("height_mm")) else float(height)
                if page is not None and _number_or_none(page.get("width_mm")) and _number_or_none(page.get("height_mm")):
                    bbox_for_space = (
                        bbox[0] / width * space_page_width,
                        bbox[1] / height * space_page_height,
                        bbox[2] / width * space_page_width,
                        bbox[3] / height * space_page_height,
                    )
                    coordinate_stub = {
                        "id": "raster_content",
                        "role": "raster-content",
                        "left_mm": bbox_for_space[0],
                        "top_mm": bbox_for_space[1],
                        "width_mm": bbox_for_space[2] - bbox_for_space[0],
                        "height_mm": bbox_for_space[3] - bbox_for_space[1],
                        "right_mm": bbox_for_space[2],
                        "bottom_mm": bbox_for_space[3],
                    }
                    checks.extend(_space_balance_checks(space_page_width, space_page_height, [coordinate_stub], space_config, coordinate_unit="mm"))
                else:
                    # Pixel dimensions are still sufficient for relative
                    # margins, but label the evidence as pixel-based in the
                    # report metadata below.
                    coordinate_stub = {
                        "id": "raster_content",
                        "role": "raster-content",
                        "left_mm": float(bbox[0]),
                        "top_mm": float(bbox[1]),
                        "width_mm": float(bbox[2] - bbox[0]),
                        "height_mm": float(bbox[3] - bbox[1]),
                        "right_mm": float(bbox[2]),
                        "bottom_mm": float(bbox[3]),
                    }
                    checks.extend(_space_balance_checks(float(width), float(height), [coordinate_stub], space_config, coordinate_unit="px"))
        if has_geometry:
            from layout_qa import _normalize_elements, _public_element, audit_boxes
            geometry_report = audit_boxes(page, geometry, contract, backend="raster+geometry", source=str(source.resolve()))
            checks.extend(geometry_report.get("checks", []))
            normalized_geometry, _ = _normalize_elements(geometry)
            report_elements = [_public_element(item) for item in normalized_geometry]
        else:
            report_elements = list(geometry or [])
        overlay = None
        if overlay_path:
            overlay = Path(overlay_path)
            _draw_overlay(image, overlay, bbox, edge_counts, checks, geometry, page, force=force)
        report = {
            "schema_version": SCHEMA_VERSION,
            "backend": "raster",
            "source": str(source.resolve()),
            "page": {
                "width_mm": (_number_or_none((page or {}).get("width_mm")) if page else (derived_page or {}).get("width_mm")),
                "height_mm": (_number_or_none((page or {}).get("height_mm")) if page else (derived_page or {}).get("height_mm")),
            },
            "elements": report_elements,
            "checks": checks,
            "summary": {"status": _overall(checks), "counts": _counts(checks)},
            "metadata": {"format": opened.format, "width_px": width, "height_px": height, "mode": opened.mode, "background_rgba": list(background), "background_tolerance": tolerance, "foreground_pixels": total, "content_bbox_fraction": bbox_fraction, "overlay": str(overlay.resolve()) if overlay else None, "dpi": source_dpi},
            "notice": "QA 不是科学、可访问性或期刊合规认证；unknown 不代表通过。",
        }
        return report


def _number_or_none(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _overall(checks: list[Mapping[str, Any]]) -> str:
    rank = {"pass": 0, "warn": 1, "unknown": 2, "fail": 3}
    return max((str(item.get("status", "unknown")) for item in checks), key=lambda item: rank.get(item, 2), default="unknown")


def _counts(checks: list[Mapping[str, Any]]) -> dict[str, int]:
    result = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
    for item in checks:
        status = str(item.get("status", "unknown"))
        result[status if status in result else "unknown"] += 1
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查栅格科研图的内容边界、裁切风险、空间利用和空白比例")
    parser.add_argument("input", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract = json.loads(args.contract.read_text(encoding="utf-8")) if args.contract else {}
        report = audit_raster(args.input, contract, overlay_path=args.overlay, force=args.force)
        output = args.output or args.input.with_name(args.input.stem + ".layout-qa.json")
        _write_json(output, report, args.force)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["summary"]["status"] == "fail" else 0
    except (OSError, ValueError, ImportError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
