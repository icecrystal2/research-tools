#!/usr/bin/env python3
"""Deterministic geometry QA for rendered scientific figures.

The module intentionally requires semantic objects to be registered explicitly.  A
PNG cannot reliably tell a connector from a data trace or a label.
"""

from __future__ import annotations

import argparse
import io
import itertools
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "figure-layout-qa/1.0"
STATUSES = ("pass", "warn", "fail", "unknown")
STATUS_RANK = {"pass": 0, "warn": 1, "unknown": 2, "fail": 3}
DEFAULT_THRESHOLDS = {
    "edge_tolerance_mm": 0.25,
    "overlap_warn_ratio": 0.05,
    "overlap_fail_ratio": 0.25,
    "blank_warn_content_fraction": 0.18,
    "blank_fail_content_fraction": None,
    "anchor_tolerance_mm": 1.5,
    "raster_background_tolerance": 18,
    "raster_edge_fraction_warn": 0.001,
}

# Composition checks are deliberately conservative.  They are intended to
# surface a layout worth reviewing, not to prescribe a symmetric or densely
# inked page for every figure type.
DEFAULT_SPACE_BALANCE = {
    "enabled": True,
    "max_margin_ratio": 3.0,
    "max_margin_difference_fraction": 0.18,
    "min_large_margin_fraction": 0.22,
    "max_center_offset_fraction": 0.18,
    "check_isolated_expansion": True,
    "small_element_fraction": 0.08,
    "max_isolated_span_ratio": 0.40,
    "min_isolated_expansion_mm": 8.0,
    "exclude_roles": ["axis", "guide", "frame", "border"],
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _issue(
    check_id: str,
    status: str,
    message: str,
    *,
    elements: Iterable[str] = (),
    metrics: Mapping[str, Any] | None = None,
    suggestion: str | None = None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    item: dict[str, Any] = {
        "id": check_id,
        "status": status,
        "message": message,
    }
    ids = list(elements)
    if ids:
        item["elements"] = ids
    if metrics:
        item["metrics"] = dict(metrics)
    if suggestion:
        item["suggestion"] = suggestion
    return item


def _overall_status(checks: Iterable[Mapping[str, Any]]) -> str:
    statuses = [str(item.get("status", "unknown")) for item in checks]
    if not statuses:
        return "unknown"
    return max(statuses, key=lambda value: STATUS_RANK.get(value, 2))


def _counts(checks: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    result = {status: 0 for status in STATUSES}
    for item in checks:
        status = str(item.get("status", "unknown"))
        result[status if status in result else "unknown"] += 1
    return result


def _thresholds(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    result = dict(DEFAULT_THRESHOLDS)
    supplied = contract.get("thresholds", {}) if isinstance(contract, Mapping) else {}
    if isinstance(supplied, Mapping):
        for key, value in supplied.items():
            if key in result:
                result[key] = value
    return result


def _space_balance_config(contract: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a sanitized, generic composition-balance configuration.

    ``space_balance`` is intentionally separate from numeric QA thresholds:
    it describes which registered objects represent the composition and which
    structural objects should not influence its heuristic.
    """
    result = dict(DEFAULT_SPACE_BALANCE)
    raw = contract.get("space_balance", {}) if isinstance(contract, Mapping) else {}
    if raw is False:
        result["enabled"] = False
        return result
    if isinstance(raw, Mapping):
        result.update(raw)
    result["enabled"] = bool(result.get("enabled", True))
    numeric_defaults = {
        "max_margin_ratio": 3.0,
        "max_margin_difference_fraction": 0.18,
        "min_large_margin_fraction": 0.22,
        "max_center_offset_fraction": 0.18,
        "small_element_fraction": 0.08,
        "max_isolated_span_ratio": 0.40,
        "min_isolated_expansion_mm": 8.0,
    }
    for key, fallback in numeric_defaults.items():
        value = _number(result.get(key))
        result[key] = max(0.0, value if value is not None else fallback)
    result["check_isolated_expansion"] = bool(result.get("check_isolated_expansion", True))
    for key in ("include_elements", "exclude_elements", "exclude_roles"):
        value = result.get(key)
        if isinstance(value, (list, tuple)):
            result[key] = [str(item) for item in value]
        elif key == "exclude_roles":
            result[key] = list(DEFAULT_SPACE_BALANCE["exclude_roles"])
        else:
            result[key] = []
    return result


def _page_from_document(document: Mapping[str, Any]) -> dict[str, float] | None:
    page = document.get("page")
    if not isinstance(page, Mapping):
        page = document.get("layout_geometry", {}).get("page") if isinstance(document.get("layout_geometry"), Mapping) else None
    if not isinstance(page, Mapping):
        return None
    width = _number(page.get("width_mm", page.get("width")))
    height = _number(page.get("height_mm", page.get("height")))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return {"width_mm": width, "height_mm": height}


def _geometry_payload(document: Mapping[str, Any]) -> Mapping[str, Any] | None:
    nested = document.get("layout_geometry")
    if isinstance(nested, Mapping):
        return nested
    if isinstance(document.get("page"), Mapping) and isinstance(document.get("elements"), list):
        return document
    return None


def _element_box(raw: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = raw.get("bbox_mm")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        values = [_number(value) for value in bbox]
        if all(value is not None for value in values):
            left, top, width, height = values  # type: ignore[misc]
            return left, top, width, height
    values = [
        _number(raw.get("left_mm", raw.get("left"))),
        _number(raw.get("top_mm", raw.get("top"))),
        _number(raw.get("width_mm", raw.get("width"))),
        _number(raw.get("height_mm", raw.get("height"))),
    ]
    if all(value is not None for value in values):
        return tuple(values)  # type: ignore[return-value]
    return None


def _normalize_elements(raw_elements: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    elements: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not isinstance(raw_elements, list):
        return elements, [_issue("geometry:elements", "unknown", "没有可解析的 elements 列表", suggestion="提供显式的元素几何信息。")] 
    for index, raw in enumerate(raw_elements):
        if not isinstance(raw, Mapping):
            errors.append(_issue(f"geometry:element:{index}", "fail", "元素不是 JSON 对象"))
            continue
        element_id = str(raw.get("id", "")).strip()
        if not element_id:
            errors.append(_issue(f"geometry:element:{index}", "fail", "元素缺少唯一 id"))
            continue
        box = _element_box(raw)
        if box is None:
            errors.append(_issue(f"geometry:{element_id}", "fail", "元素缺少有效的毫米包围盒", elements=[element_id]))
            continue
        left, top, width, height = box
        if width < 0 or height < 0:
            errors.append(_issue(f"geometry:{element_id}", "fail", "元素宽高不能为负数", elements=[element_id]))
            continue
        item = dict(raw)
        item.update({"id": element_id, "role": str(raw.get("role", "unknown")), "left_mm": left, "top_mm": top, "width_mm": width, "height_mm": height})
        item["right_mm"] = left + width
        item["bottom_mm"] = top + height
        item["visible"] = raw.get("visible", True) is not False
        elements.append(item)
    return elements, errors


def _rect_area(element: Mapping[str, Any]) -> float:
    return max(0.0, float(element["width_mm"])) * max(0.0, float(element["height_mm"]))


def _intersection(a: Mapping[str, Any], b: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    left = max(float(a["left_mm"]), float(b["left_mm"]))
    top = max(float(a["top_mm"]), float(b["top_mm"]))
    right = min(float(a["right_mm"]), float(b["right_mm"]))
    bottom = min(float(a["bottom_mm"]), float(b["bottom_mm"]))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _union_area(elements: Iterable[Mapping[str, Any]]) -> float:
    rectangles = [
        (float(item["left_mm"]), float(item["top_mm"]), float(item["right_mm"]), float(item["bottom_mm"]))
        for item in elements
        if item.get("visible", True) and float(item.get("width_mm", 0)) > 0 and float(item.get("height_mm", 0)) > 0
    ]
    if not rectangles:
        return 0.0
    xs = sorted({value for rect in rectangles for value in (rect[0], rect[2])})
    area = 0.0
    for x0, x1 in zip(xs, xs[1:]):
        if x1 <= x0:
            continue
        ys = []
        for left, top, right, bottom in rectangles:
            if left < x1 and right > x0:
                ys.append((top, bottom))
        ys.sort()
        covered = 0.0
        current_top = current_bottom = None
        for top, bottom in ys:
            if current_top is None:
                current_top, current_bottom = top, bottom
            elif top > current_bottom:
                covered += current_bottom - current_top
                current_top, current_bottom = top, bottom
            else:
                current_bottom = max(current_bottom, bottom)
        if current_top is not None:
            covered += current_bottom - current_top
        area += (x1 - x0) * covered
    return area


def _content_bbox(elements: Iterable[Mapping[str, Any]]) -> tuple[float, float, float, float] | None:
    visible = [item for item in elements if item.get("visible", True) and _rect_area(item) > 0]
    if not visible:
        return None
    left = min(float(item["left_mm"]) for item in visible)
    top = min(float(item["top_mm"]) for item in visible)
    right = max(float(item["right_mm"]) for item in visible)
    bottom = max(float(item["bottom_mm"]) for item in visible)
    return left, top, right, bottom


def _composition_elements(
    elements: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Select visible objects that represent the visual composition.

    Structural objects (axes, guides, frames and borders) are excluded by
    default because their job is to define the coordinate system, not to use
    page space.  Contracts can opt in to any object by setting
    ``include_elements`` or changing ``exclude_roles``.
    """
    include = {str(value) for value in config.get("include_elements", [])}
    exclude = {str(value) for value in config.get("exclude_elements", [])}
    exclude_roles = {str(value) for value in config.get("exclude_roles", [])}
    selected: list[Mapping[str, Any]] = []
    for item in elements:
        item_id = str(item.get("id", ""))
        if not item.get("visible", True) or _rect_area(item) <= 0:
            continue
        if include and item_id not in include:
            continue
        if item_id in exclude or str(item.get("role", "unknown")) in exclude_roles:
            continue
        if item.get("exclude_from_space_balance") is True:
            # Exclude an object from composition-balance heuristics while
            # retaining its ordinary boundary/overlap checks.
            continue
        selected.append(item)
    return selected


def _bbox_from_elements(elements: Iterable[Mapping[str, Any]]) -> tuple[float, float, float, float] | None:
    """Return a left/top/right/bottom box for non-empty element geometry."""
    visible = [item for item in elements if item.get("visible", True) and _rect_area(item) > 0]
    if not visible:
        return None
    return (
        min(float(item["left_mm"]) for item in visible),
        min(float(item["top_mm"]) for item in visible),
        max(float(item["right_mm"]) for item in visible),
        max(float(item["bottom_mm"]) for item in visible),
    )


def _space_balance_checks(
    page_width: float | None,
    page_height: float | None,
    elements: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    coordinate_unit: str = "mm",
) -> list[dict[str, Any]]:
    """Create conservative, generic checks for page-space allocation.

    The checks intentionally report ``warn`` rather than ``fail``.  A large
    margin can be a deliberate part of a journal composition, so the result is
    a prompt for visual review, not an automatic crop or rearrangement.
    """
    if not config.get("enabled", True):
        return []
    if page_width is None or page_height is None or page_width <= 0 or page_height <= 0:
        return [_issue(
            "space-balance:page",
            "unknown",
            "缺少页面尺寸，无法评估内容在页面中的空间分配",
            suggestion="提供有效的页面 width_mm/height_mm，或关闭 space_balance。",
        )]
    selected = _composition_elements(elements, config)
    composition = _bbox_from_elements(selected)
    if composition is None:
        return [_issue(
            "space-balance:composition",
            "unknown",
            "没有可用于空间分配评估的可见元素",
            suggestion="注册代表视觉构图的元素，或在合同中配置 include_elements/exclude_roles。",
        )]

    left, top, right, bottom = composition
    unit = str(coordinate_unit or "mm").strip().lower() or "mm"
    if not re.fullmatch(r"[a-z][a-z0-9_]*", unit):
        unit = "unit"
    bbox_key = f"composition_bbox_{unit}"
    margins_key = f"margins_{unit}"
    center_key = f"content_center_{unit}"
    margins = {
        "left_mm": max(0.0, left),
        "top_mm": max(0.0, top),
        "right_mm": max(0.0, page_width - right),
        "bottom_mm": max(0.0, page_height - bottom),
    }
    max_ratio = float(config.get("max_margin_ratio", 3.0))
    max_difference = float(config.get("max_margin_difference_fraction", 0.18))
    min_large = float(config.get("min_large_margin_fraction", 0.22))
    checks: list[dict[str, Any]] = []

    for axis, first_name, second_name, axis_size in (
        ("horizontal", "left_mm", "right_mm", page_width),
        ("vertical", "top_mm", "bottom_mm", page_height),
    ):
        first, second = margins[first_name], margins[second_name]
        largest = max(first, second)
        smallest = min(first, second)
        ratio = largest / max(smallest, 0.5)
        difference_fraction = abs(first - second) / axis_size
        imbalanced = (
            largest / axis_size >= min_large
            and (ratio > max_ratio or difference_fraction > max_difference)
        )
        checks.append(_issue(
            f"space-balance:{axis}",
            "warn" if imbalanced else "pass",
            "页面两侧的内容边距差异较大，可能存在未利用的空间"
            if imbalanced
            else "页面两侧的内容边距处于可接受的启发式范围",
            metrics={
                bbox_key: [left, top, right - left, bottom - top],
                margins_key: margins,
                "margin_ratio": ratio,
                "margin_difference_fraction": difference_fraction,
                "max_margin_fraction": largest / axis_size,
                "thresholds": {
                    "max_margin_ratio": max_ratio,
                    "max_margin_difference_fraction": max_difference,
                    "min_large_margin_fraction": min_large,
                },
            },
            suggestion=(
                "检查是否可缩小页面或把同一信息层级的元素重新分配到空白侧；"
                "不要仅为单个附加对象扩大画布。"
                if imbalanced
                else None
            ),
        ))

    center_offset_x = abs(((left + right) / 2.0) - page_width / 2.0) / page_width
    center_offset_y = abs(((top + bottom) / 2.0) - page_height / 2.0) / page_height
    center_limit = float(config.get("max_center_offset_fraction", 0.18))
    center_warn = center_offset_x > center_limit or center_offset_y > center_limit
    checks.append(_issue(
        "space-balance:center",
        "warn" if center_warn else "pass",
        "主要内容重心明显偏离页面中心，需复核构图平衡"
        if center_warn
        else "主要内容重心未显示明显偏移",
        metrics={
            center_key: [(left + right) / 2.0, (top + bottom) / 2.0],
            f"page_center_{unit}": [page_width / 2.0, page_height / 2.0],
            "offset_fraction": {"x": center_offset_x, "y": center_offset_y},
            "max_center_offset_fraction": center_limit,
        },
        suggestion="检查标题、图例、注释或数据区是否被单侧元素挤到边缘。" if center_warn else None,
    ))

    if config.get("check_isolated_expansion", True) and len(selected) >= 2:
        max_span_ratio = float(config.get("max_isolated_span_ratio", 0.40))
        min_expansion = float(config.get("min_isolated_expansion_mm", 8.0))
        small_fraction = float(config.get("small_element_fraction", 0.08))
        composition_width = max(right - left, 0.5)
        composition_height = max(bottom - top, 0.5)
        for candidate in selected:
            if candidate.get("allow_space_expansion") is True:
                continue
            candidate_span = max(
                float(candidate["width_mm"]) / composition_width,
                float(candidate["height_mm"]) / composition_height,
            )
            if candidate_span > small_fraction:
                continue
            others = [item for item in selected if item is not candidate]
            other_box = _bbox_from_elements(others)
            if other_box is None:
                continue
            other_left, other_top, other_right, other_bottom = other_box
            extensions = {
                "left": max(0.0, other_left - float(candidate["left_mm"])),
                "top": max(0.0, other_top - float(candidate["top_mm"])),
                "right": max(0.0, float(candidate["right_mm"]) - other_right),
                "bottom": max(0.0, float(candidate["bottom_mm"]) - other_bottom),
            }
            largest_side, extension = max(extensions.items(), key=lambda item: item[1])
            reference_span = (
                composition_width if largest_side in {"left", "right"} else composition_height
            )
            span_ratio = extension / max(reference_span, 0.5)
            if extension >= min_expansion and span_ratio > max_span_ratio:
                candidate_id = str(candidate.get("id", "unknown"))
                checks.append(_issue(
                    f"space-balance:isolated-expansion:{candidate_id}",
                    "warn",
                    "小型元素显著扩大了内容包围范围，可能造成单侧大片空白",
                    elements=[candidate_id],
                    metrics={
                        f"element_bbox_{unit}": [
                            float(candidate["left_mm"]),
                            float(candidate["top_mm"]),
                            float(candidate["width_mm"]),
                            float(candidate["height_mm"]),
                        ],
                        "isolated_side": largest_side,
                        "extension_mm": extension,
                        "extension_ratio": span_ratio,
                        "element_span_fraction": candidate_span,
                        "thresholds": {
                            "min_isolated_expansion_mm": min_expansion,
                            "max_isolated_span_ratio": max_span_ratio,
                            "small_element_fraction": small_fraction,
                        },
                    },
                    suggestion="确认该元素是否应移入邻近空白区、缩小页面，或从构图包围范围中独立处理。",
                ))
        if not any(item["id"].startswith("space-balance:isolated-expansion:") for item in checks):
            checks.append(_issue(
                "space-balance:isolated-expansion",
                "pass",
                "未发现由单个小型元素明显撑大页面范围的情况",
                metrics={
                    "min_isolated_expansion_mm": min_expansion,
                    "max_isolated_span_ratio": max_span_ratio,
                    "small_element_fraction": small_fraction,
                },
            ))
    return checks


def _public_element(item: Mapping[str, Any]) -> dict[str, Any]:
    """Remove internal derived coordinates while retaining contract metadata."""
    result = dict(item)
    result.pop("right_mm", None)
    result.pop("bottom_mm", None)
    if result.get("visible", True) is True:
        result.pop("visible", None)
    return result


def _point_from_element(element: Mapping[str, Any], point: str = "center") -> tuple[float, float]:
    left, top = float(element["left_mm"]), float(element["top_mm"])
    right, bottom = float(element["right_mm"]), float(element["bottom_mm"])
    points = {
        "center": ((left + right) / 2, (top + bottom) / 2),
        "top": ((left + right) / 2, top),
        "bottom": ((left + right) / 2, bottom),
        "left": (left, (top + bottom) / 2),
        "right": (right, (top + bottom) / 2),
        "top_left": (left, top),
        "top_right": (right, top),
        "bottom_left": (left, bottom),
        "bottom_right": (right, bottom),
    }
    return points.get(point, points["center"])


def _resolve_point(target: Any, by_id: Mapping[str, Mapping[str, Any]]) -> tuple[float, float] | None:
    if isinstance(target, (list, tuple)) and len(target) == 2:
        x, y = _number(target[0]), _number(target[1])
        return (x, y) if x is not None and y is not None else None
    if not isinstance(target, Mapping):
        return None
    direct = target.get("target_mm", target.get("point_mm"))
    if isinstance(direct, (list, tuple)) and len(direct) == 2:
        return _resolve_point(direct, by_id)
    element_id = target.get("element", target.get("target_element"))
    if element_id is None or str(element_id) not in by_id:
        return None
    return _point_from_element(by_id[str(element_id)], str(target.get("point", "center")))


def _actual_anchor(element: Mapping[str, Any], name: str) -> tuple[float, float] | None:
    anchors = element.get("anchors")
    if not isinstance(anchors, Mapping):
        return None
    value = anchors.get(name)
    return _resolve_point(value, {str(element["id"]): element})


_BOX_POINTS = {
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
}


def _element_point(element: Mapping[str, Any], name: str) -> tuple[float, float] | None:
    """Resolve an explicit anchor first, then a named point on the element box."""
    anchored = _actual_anchor(element, name)
    if anchored is not None:
        return anchored
    if name in _BOX_POINTS:
        return _point_from_element(element, name)
    return None


def _normalize_direction(value: Any) -> str:
    aliases = {
        "north": "up",
        "south": "down",
        "east": "right",
        "west": "left",
        "northeast": "up_right",
        "northwest": "up_left",
        "southeast": "down_right",
        "southwest": "down_left",
    }
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return aliases.get(normalized, normalized)


def _direction_matches(
    dx: float,
    dy: float,
    expected: str,
    min_axis_ratio: float,
) -> bool | None:
    """Check direction in page coordinates, where positive y points downward."""
    if expected == "down":
        return dy > 0 and abs(dy) >= abs(dx) * min_axis_ratio
    if expected == "up":
        return dy < 0 and abs(dy) >= abs(dx) * min_axis_ratio
    if expected == "right":
        return dx > 0 and abs(dx) >= abs(dy) * min_axis_ratio
    if expected == "left":
        return dx < 0 and abs(dx) >= abs(dy) * min_axis_ratio
    if expected == "down_right":
        return dx > 0 and dy > 0
    if expected == "down_left":
        return dx < 0 and dy > 0
    if expected == "up_right":
        return dx > 0 and dy < 0
    if expected == "up_left":
        return dx < 0 and dy < 0
    return None


def audit_boxes(
    page: Mapping[str, Any] | None,
    elements: Iterable[Mapping[str, Any]],
    contract: Mapping[str, Any] | None = None,
    *,
    backend: str = "geometry",
    source: str | None = None,
    extra_checks: Iterable[Mapping[str, Any]] = (),
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit normalized or raw page elements and return the shared report."""
    if contract is not None and not isinstance(contract, Mapping):
        raise ValueError("布局合同根节点必须是对象")
    contract = contract or {}
    normalized, errors = _normalize_elements(list(elements))
    checks: list[dict[str, Any]] = list(errors)
    seen_ids: set[str] = set()
    for item in normalized:
        item_id = str(item["id"])
        if item_id in seen_ids:
            checks.append(_issue(f"geometry:duplicate:{item_id}", "fail", "元素 id 重复，无法稳定关联合同和问题", elements=[item_id], suggestion="为每个可检查对象分配唯一 id。"))
        seen_ids.add(item_id)
    page_width = _number((page or {}).get("width_mm")) if page else None
    page_height = _number((page or {}).get("height_mm")) if page else None
    if page_width is None or page_height is None or page_width <= 0 or page_height <= 0:
        checks.append(_issue("page:dimensions", "unknown", "缺少有效的页面 width_mm/height_mm", suggestion="提供导出页面的物理尺寸。"))
    else:
        checks.append(_issue("page:dimensions", "pass", "页面尺寸有效", metrics={"width_mm": page_width, "height_mm": page_height}))

    if not normalized:
        checks.append(_issue("geometry:registration", "unknown", "没有可检查的显式元素几何信息", suggestion="在 Matplotlib 注册 artist，或在 Origin 导出 layout_geometry。"))

    thresholds = _thresholds(contract)
    tolerance = max(0.0, _number(thresholds.get("edge_tolerance_mm")) or 0.0)
    if page_width is not None and page_height is not None and page_width > 0 and page_height > 0:
        for item in normalized:
            if not item.get("visible", True):
                continue
            outside = {
                "left_mm": max(0.0, -float(item["left_mm"])),
                "top_mm": max(0.0, -float(item["top_mm"])),
                "right_mm": max(0.0, float(item["right_mm"]) - page_width),
                "bottom_mm": max(0.0, float(item["bottom_mm"]) - page_height),
            }
            max_outside = max(outside.values())
            if item.get("allow_outside"):
                status = "pass"
                message = "元素被合同标记为允许越界"
            elif max_outside <= tolerance:
                status = "pass"
                message = "元素在页面边界内"
            else:
                status = "fail"
                message = "元素包围盒超出页面边界"
            checks.append(_issue(f"boundary:{item['id']}", status, message, elements=[str(item["id"])], metrics=outside, suggestion="增大页面留白或移动/缩小元素。" if status == "fail" else None))

    by_id = {str(item["id"]): item for item in normalized}
    required = contract.get("required_elements", [])
    if isinstance(required, list):
        for required_id in required:
            key = str(required_id)
            if key not in by_id or not by_id[key].get("visible", True):
                checks.append(_issue(f"required:{key}", "fail", "合同要求的元素未注册或不可见", elements=[key], suggestion="确认元素确实被绘制并加入 QA 注册表。"))
            else:
                checks.append(_issue(f"required:{key}", "pass", "合同要求的元素已注册", elements=[key]))

    ignored_pairs = set()
    raw_pairs = contract.get("ignore_overlap_pairs", [])
    if isinstance(raw_pairs, list):
        for pair in raw_pairs:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                ignored_pairs.add(tuple(sorted((str(pair[0]), str(pair[1])))))
    warn_value = _number(thresholds.get("overlap_warn_ratio"))
    fail_value = _number(thresholds.get("overlap_fail_ratio"))
    warn_ratio = max(0.0, warn_value if warn_value is not None else 0.05)
    fail_ratio = max(warn_ratio, fail_value if fail_value is not None else 0.25)
    visible = [item for item in normalized if item.get("visible", True) and _rect_area(item) > 0]
    for first, second in itertools.combinations(visible, 2):
        pair = tuple(sorted((str(first["id"]), str(second["id"]))))
        if pair in ignored_pairs or first.get("allow_overlap") or second.get("allow_overlap"):
            continue
        inter = _intersection(first, second)
        if inter is None:
            continue
        area = (inter[2] - inter[0]) * (inter[3] - inter[1])
        denominator = min(_rect_area(first), _rect_area(second))
        ratio = area / denominator if denominator else 0.0
        if ratio < warn_ratio:
            continue
        status = "fail" if ratio >= fail_ratio else "warn"
        checks.append(_issue(f"overlap:{pair[0]}:{pair[1]}", status, "元素包围盒有明显交叠", elements=pair, metrics={"intersection_area_mm2": area, "overlap_ratio": ratio}, suggestion="确认交叠是否是语义需要；否则移动标签、箭头或数据区域。"))

    if page_width is not None and page_height is not None and page_width > 0 and page_height > 0 and visible:
        page_area = page_width * page_height
        occupied = _union_area(visible) / page_area
        content = _content_bbox(visible)
        content_fraction = ((content[2] - content[0]) * (content[3] - content[1]) / page_area) if content else 0.0
        blank_limit = _number(thresholds.get("blank_warn_content_fraction"))
        fail_limit = _number(thresholds.get("blank_fail_content_fraction"))
        if blank_limit is not None and content_fraction < blank_limit:
            status = "fail" if fail_limit is not None and content_fraction < fail_limit else "warn"
            checks.append(_issue("blank-space", status, "内容包围盒占页面比例偏低，可能存在大片空白", metrics={"content_bbox_fraction": content_fraction, "occupied_union_fraction": occupied}, suggestion="缩小页面或重新分配标题、图例、坐标区和注释的空间。"))
        else:
            checks.append(_issue("blank-space", "pass", "内容包围盒占比未低于合同阈值", metrics={"content_bbox_fraction": content_fraction, "occupied_union_fraction": occupied}))
    elif page_width is not None and page_height is not None and page_width > 0 and page_height > 0:
        checks.append(_issue("blank-space", "unknown", "没有足够的可见元素估计空白比例"))

    # Bounding-box occupancy alone cannot show whether the remaining space is
    # concentrated on one side, or whether a small add-on object expanded the
    # canvas disproportionately.  Keep this as a conservative, configurable
    # composition heuristic and leave intentional asymmetry to human review.
    space_config = _space_balance_config(contract)
    if space_config.get("enabled", True):
        checks.extend(_space_balance_checks(page_width, page_height, normalized, space_config))

    # Anchor checks are deliberately contract-driven; no visual guess is made.
    anchors = contract.get("anchors", [])
    if isinstance(anchors, list):
        for rule in anchors:
            if not isinstance(rule, Mapping):
                checks.append(_issue("anchor:invalid", "fail", "锚点规则不是 JSON 对象"))
                continue
            rule_id = str(rule.get("id", f"{rule.get('element', 'unknown')}.{rule.get('actual', 'end')}"))
            element_id = str(rule.get("element", ""))
            actual_name = str(rule.get("actual", "end"))
            actual = _actual_anchor(by_id[element_id], actual_name) if element_id in by_id else None
            target = _resolve_point(rule.get("target"), by_id)
            rule_tolerance = _number(rule.get("tolerance_mm"))
            default_tolerance = _number(thresholds.get("anchor_tolerance_mm"))
            tolerance_mm = max(0.0, rule_tolerance if rule_tolerance is not None else (default_tolerance if default_tolerance is not None else 1.5))
            if actual is None or target is None:
                checks.append(_issue(f"anchor:{rule_id}", "unknown", "无法取得锚点实际位置或目标位置", elements=[element_id] if element_id else (), suggestion="为箭头/annotation 提供实际 anchor 坐标，并确认 target 引用。"))
                continue
            distance = math.hypot(actual[0] - target[0], actual[1] - target[1])
            status = "pass" if distance <= tolerance_mm else ("warn" if distance <= 2 * tolerance_mm else "fail")
            checks.append(_issue(f"anchor:{rule_id}", status, "锚点接近合同目标" if status == "pass" else "锚点偏离合同目标", elements=[element_id], metrics={"actual_mm": list(actual), "target_mm": list(target), "distance_mm": distance, "tolerance_mm": tolerance_mm}, suggestion="把连接线端点移动到合同目标或关联对象的锚点。" if status != "pass" else None))

    # Relations make the layout contract describe how semantic elements work
    # together, rather than validating each bounding box in isolation.
    relations = contract.get("relations", [])
    if isinstance(relations, list):
        for index, rule in enumerate(relations):
            if not isinstance(rule, Mapping):
                checks.append(_issue(f"relation:{index}", "fail", "关系规则不是 JSON 对象"))
                continue
            relation_type = str(rule.get("type", "")).strip().lower()
            relation_id = str(rule.get("id", f"{relation_type or 'invalid'}:{index}"))
            element_id = str(rule.get("element", rule.get("source_element", "")))
            element = by_id.get(element_id)
            if element is None:
                checks.append(_issue(f"relation:{relation_id}", "unknown", "关系规则引用的源元素不存在", elements=[element_id] if element_id else (), suggestion="注册源元素并使用稳定的元素 id。"))
                continue

            if relation_type == "proximity":
                actual_name = str(rule.get("actual", "center"))
                actual = _element_point(element, actual_name)
                target = _resolve_point(rule.get("target"), by_id)
                maximum = _number(rule.get("max_distance_mm"))
                if actual is None or target is None or maximum is None or maximum < 0:
                    checks.append(_issue(f"relation:{relation_id}", "unknown", "邻近关系缺少可解析的点或最大距离", elements=[element_id], suggestion="提供源锚点、目标点和非负 max_distance_mm。"))
                    continue
                distance = math.hypot(actual[0] - target[0], actual[1] - target[1])
                status = "pass" if distance <= maximum else "fail"
                checks.append(_issue(f"relation:{relation_id}", status, "元素间距符合关系合同" if status == "pass" else "相关元素相距过远，视觉分组可能断裂", elements=[element_id], metrics={"actual_mm": list(actual), "target_mm": list(target), "distance_mm": distance, "max_distance_mm": maximum}, suggestion="调整相关标注、图标或连接线的相对位置。" if status == "fail" else None))
                continue

            if relation_type == "relative_position":
                actual_name = str(rule.get("actual", "center"))
                actual = _element_point(element, actual_name)
                target = _resolve_point(rule.get("target"), by_id)
                expected = _normalize_direction(rule.get("expected"))
                gap_value = _number(rule.get("min_gap_mm"))
                min_gap = max(0.0, gap_value if gap_value is not None else 0.0)
                if actual is None or target is None:
                    checks.append(_issue(f"relation:{relation_id}", "unknown", "相对位置关系缺少可解析的点", elements=[element_id], suggestion="注册相关元素并指定可解析的点。"))
                    continue
                dx, dy = actual[0] - target[0], actual[1] - target[1]
                position_ok = {
                    "above": dy <= -min_gap,
                    "below": dy >= min_gap,
                    "left_of": dx <= -min_gap,
                    "right_of": dx >= min_gap,
                }.get(expected)
                if position_ok is None:
                    checks.append(_issue(f"relation:{relation_id}", "fail", "不支持的相对位置类型", elements=[element_id], metrics={"expected": expected}, suggestion="使用 above、below、left_of 或 right_of。"))
                    continue
                status = "pass" if position_ok else "fail"
                checks.append(_issue(f"relation:{relation_id}", status, "元素相对位置符合关系合同" if status == "pass" else "元素相对位置不符合关系合同", elements=[element_id], metrics={"actual_mm": list(actual), "target_mm": list(target), "dx_mm": dx, "dy_mm": dy, "expected": expected, "min_gap_mm": min_gap}, suggestion="重新排列相关标注，使其保持预期的阅读顺序。" if status == "fail" else None))
                continue

            if relation_type == "direction":
                from_name = str(rule.get("from", "start"))
                to_name = str(rule.get("to", "end"))
                start = _element_point(element, from_name)
                end = _element_point(element, to_name)
                expected = _normalize_direction(rule.get("expected"))
                ratio_value = _number(rule.get("min_axis_ratio"))
                min_axis_ratio = max(0.0, ratio_value if ratio_value is not None else 1.0)
                length_value = _number(rule.get("min_length_mm"))
                min_length = max(0.0, length_value if length_value is not None else 0.5)
                if start is None or end is None:
                    checks.append(_issue(f"relation:{relation_id}", "unknown", "方向关系缺少起点或终点锚点", elements=[element_id], suggestion="为连接线注册 start 和 end 锚点。"))
                    continue
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = math.hypot(dx, dy)
                matches = _direction_matches(dx, dy, expected, min_axis_ratio)
                if matches is None:
                    checks.append(_issue(f"relation:{relation_id}", "fail", "不支持的方向类型", elements=[element_id], metrics={"expected": expected}, suggestion="使用 up、down、left、right 或相应对角方向。"))
                    continue
                status = "pass" if matches and length >= min_length else "fail"
                checks.append(_issue(f"relation:{relation_id}", status, "连接方向符合关系合同" if status == "pass" else "连接方向或长度不符合关系合同", elements=[element_id], metrics={"start_mm": list(start), "end_mm": list(end), "dx_mm": dx, "dy_mm": dy, "length_mm": length, "expected": expected, "min_axis_ratio": min_axis_ratio, "min_length_mm": min_length}, suggestion="调整连接线起终点，避免方向含混或长度过短。" if status == "fail" else None))
                continue

            checks.append(_issue(f"relation:{relation_id}", "fail", "不支持的关系规则类型", elements=[element_id], metrics={"type": relation_type}, suggestion="使用 proximity、relative_position 或 direction。"))

    checks.extend(dict(item) for item in extra_checks)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "backend": backend,
        "source": source,
        "page": {"width_mm": page_width, "height_mm": page_height},
        "elements": [_public_element(item) for item in normalized],
        "checks": checks,
        "summary": {"status": _overall_status(checks), "counts": _counts(checks)},
        "metadata": dict(metadata or {}),
        "notice": "QA 不是科学、可访问性或期刊合规认证；unknown 不代表通过。",
    }
    return report


def audit_geometry_document(document: Mapping[str, Any], contract: Mapping[str, Any] | None = None, *, source: str | None = None) -> dict[str, Any]:
    """Audit a standalone geometry document or an Origin report containing one."""
    if not isinstance(document, Mapping):
        raise ValueError("几何文档根节点必须是对象")
    if contract is not None and not isinstance(contract, Mapping):
        raise ValueError("布局合同根节点必须是对象")
    payload = _geometry_payload(document)
    if payload is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "backend": "origin",
            "source": source,
            "page": {"width_mm": None, "height_mm": None},
            "elements": [],
            "checks": [_issue("geometry:availability", "unknown", "文档没有 layout_geometry，无法反读 Origin 对象几何", suggestion="让 Origin 导出 layout_geometry sidecar；不要从截图猜测。")],
            "summary": {"status": "unknown", "counts": {"pass": 0, "warn": 0, "fail": 0, "unknown": 1}},
            "metadata": {"document_keys": sorted(str(key) for key in document)},
            "notice": "QA 不是科学、可访问性或期刊合规认证；unknown 不代表通过。",
        }
    page = _page_from_document(payload)
    return audit_boxes(page, payload.get("elements", []), contract, backend="origin", source=source, metadata={"geometry_source": "layout_geometry"})


def _display_to_mm(point: tuple[float, float], fig: Any) -> tuple[float, float]:
    fx0, fy0, fw, fh = (float(value) for value in fig.bbox.bounds)
    page_width, page_height = (float(value) * 25.4 for value in fig.get_size_inches())
    x, y = point
    return ((x - fx0) / fw * page_width, (fy0 + fh - y) / fh * page_height)


def _artist_extent_mm(artist: Any, renderer: Any, fig: Any) -> tuple[float, float, float, float] | None:
    extent = None
    try:
        extent = artist.get_window_extent(renderer)
    except Exception:
        extent = None
    # Collections such as LineCollection can report an empty window extent
    # even though their data paths are visible.  Use their data limits as a
    # conservative semantic-group box in that case.
    if extent is None or not all(
        math.isfinite(float(value))
        for value in (extent.x0, extent.y0, extent.x1, extent.y1)
    ) or extent.x1 < extent.x0 or extent.y1 < extent.y0:
        axes = getattr(artist, "axes", None)
        get_datalim = getattr(artist, "get_datalim", None)
        transform = getattr(axes, "transData", None) if axes is not None else None
        if get_datalim is None or transform is None:
            return None
        try:
            data_box = get_datalim(transform)
            data_points = transform.transform(
                [(float(data_box.x0), float(data_box.y0)), (float(data_box.x1), float(data_box.y1))]
            )
            display_x = [float(point[0]) for point in data_points]
            display_y = [float(point[1]) for point in data_points]
            display_left, display_right = min(display_x), max(display_x)
            display_bottom, display_top = min(display_y), max(display_y)
            extent = type("_Extent", (), {
                "x0": display_left,
                "y0": display_bottom,
                "x1": display_right,
                "y1": display_top,
                "width": display_right - display_left,
                "height": display_top - display_bottom,
            })()
        except Exception:
            return None
    fx0, fy0, fw, fh = (float(value) for value in fig.bbox.bounds)
    page_width, page_height = (float(value) * 25.4 for value in fig.get_size_inches())
    left = (float(extent.x0) - fx0) / fw * page_width
    top = (fy0 + fh - float(extent.y1)) / fh * page_height
    width = float(extent.width) / fw * page_width
    height = float(extent.height) / fh * page_height
    return left, top, width, height


def _annotation_target_mm(artist: Any, renderer: Any, fig: Any) -> tuple[float, float] | None:
    if not hasattr(artist, "xy"):
        return None
    try:
        point = artist._get_position_xy(renderer)  # Matplotlib's Annotation coordinate resolver.
        return _display_to_mm((float(point[0]), float(point[1])), fig)
    except Exception:
        return None


def _annotation_source_mm(artist: Any, renderer: Any, fig: Any) -> tuple[float, float] | None:
    if not hasattr(artist, "xyann") or not hasattr(artist, "anncoords"):
        return None
    try:
        point = artist._get_xy(renderer, artist.xyann, artist.anncoords)
        return _display_to_mm((float(point[0]), float(point[1])), fig)
    except Exception:
        return None


def audit_matplotlib_figure(
    fig: Any,
    contract: Mapping[str, Any] | None = None,
    artists: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    """Render a Matplotlib figure and audit explicitly registered artists.

    A mapping value can be an artist or ``{"artist": artist, "role": ...,
    "anchor_name": "end", "source_anchor_name": "start"}``. Annotation
    ``xy`` and ``xytext`` are registered as the requested endpoint anchors.
    """
    if contract is not None and not isinstance(contract, Mapping):
        raise ValueError("布局合同根节点必须是对象")
    try:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
    except Exception as exc:
        return {"schema_version": SCHEMA_VERSION, "backend": "matplotlib", "source": source, "checks": [_issue("matplotlib:render", "fail", f"无法渲染 figure: {exc}")], "summary": {"status": "fail", "counts": {"pass": 0, "warn": 0, "fail": 1, "unknown": 0}}, "elements": [], "page": {"width_mm": None, "height_mm": None}, "notice": "QA 不是科学、可访问性或期刊合规认证；unknown 不代表通过。"}
    specs: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(artists, Mapping):
        for key, value in artists.items():
            specs.append((str(key), value if isinstance(value, Mapping) else {"artist": value}))
    elif artists is not None:
        for index, value in enumerate(artists):
            if isinstance(value, Mapping):
                specs.append((str(value.get("id", index)), value))
            else:
                specs.append((str(index), {"artist": value}))
    elements: list[dict[str, Any]] = []
    registration_checks: list[dict[str, Any]] = []
    for element_id, spec in specs:
        artist = spec.get("artist")
        if artist is None:
            registration_checks.append(_issue(f"artist:{element_id}", "fail", "注册项缺少 artist 对象", elements=[element_id]))
            continue
        box = _artist_extent_mm(artist, renderer, fig)
        if box is None:
            registration_checks.append(_issue(f"artist:{element_id}", "unknown", "无法读取 artist 的渲染包围盒", elements=[element_id]))
            continue
        left, top, width, height = box
        item: dict[str, Any] = {"id": element_id, "role": str(spec.get("role", "unknown")), "bbox_mm": [left, top, width, height]}
        if spec.get("allow_outside") is not None:
            item["allow_outside"] = bool(spec["allow_outside"])
        if spec.get("allow_overlap") is not None:
            item["allow_overlap"] = bool(spec["allow_overlap"])
        if spec.get("allow_space_expansion") is not None:
            item["allow_space_expansion"] = bool(spec["allow_space_expansion"])
        if spec.get("exclude_from_space_balance") is not None:
            item["exclude_from_space_balance"] = bool(spec["exclude_from_space_balance"])
        anchors: dict[str, list[float]] = {}
        supplied_anchors = spec.get("anchors")
        if isinstance(supplied_anchors, Mapping):
            for name, value in supplied_anchors.items():
                resolved = _resolve_point(value, {})
                if resolved is not None:
                    anchors[str(name)] = list(resolved)
        anchor_name = spec.get("anchor_name")
        if anchor_name:
            point = _annotation_target_mm(artist, renderer, fig)
            if point is not None:
                anchors[str(anchor_name)] = list(point)
        source_anchor_name = spec.get("source_anchor_name", spec.get("start_anchor_name"))
        if source_anchor_name:
            point = _annotation_source_mm(artist, renderer, fig)
            if point is not None:
                anchors[str(source_anchor_name)] = list(point)
        if anchors:
            item["anchors"] = anchors
        elements.append(item)
    page = {"width_mm": float(fig.get_size_inches()[0]) * 25.4, "height_mm": float(fig.get_size_inches()[1]) * 25.4}
    if not elements:
        registration_checks.append(_issue("artist:registration", "unknown", "没有显式注册的 Matplotlib artist", suggestion="把曲线、标签、箭头和关键标记加入 artists 注册表。"))
    return audit_boxes(page, elements, contract, backend="matplotlib", source=source, extra_checks=registration_checks, metadata={"matplotlib_version": _matplotlib_version()})


def write_matplotlib_overlay(
    fig: Any,
    report: Mapping[str, Any],
    output: str | Path,
    *,
    dpi: float = 300,
    force: bool = False,
) -> Path:
    """Save a non-destructive PNG overlay for a Matplotlib QA report."""
    output_path = Path(output)
    if output_path.exists() and not force:
        raise FileExistsError(f"overlay 已存在，使用 force=True 覆盖: {output_path}")
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError("Matplotlib overlay 需要 Pillow") from exc
    try:
        from raster_checks import _draw_overlay
    except ImportError:  # pragma: no cover - package import fallback
        from .raster_checks import _draw_overlay

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=float(dpi), facecolor="white", bbox_inches=None)
    buffer.seek(0)
    with Image.open(buffer) as rendered:
        page = report.get("page") if isinstance(report.get("page"), Mapping) else None
        _draw_overlay(
            rendered.convert("RGBA"),
            output_path,
            None,
            {},
            list(report.get("checks", [])),
            list(report.get("elements", [])),
            page,
            force=force,
        )
    return output_path.resolve()


def _matplotlib_version() -> str | None:
    try:
        import matplotlib
        return str(matplotlib.__version__)
    except Exception:
        return None


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("JSON 根节点必须是对象")
    return value


def _write_json(path: Path, document: Mapping[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"输出已存在，使用 --force 覆盖: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _length_mm(value: Any, *, default_unit: str = "px") -> float | None:
    if isinstance(value, (int, float)):
        number = _number(value)
        return number if number is not None else None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([a-zA-Z]*)\s*", value)
    if not match:
        return None
    number = _number(match.group(1))
    if number is None:
        return None
    unit = (match.group(2) or default_unit).lower()
    factors = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72.0, "px": 25.4 / 96.0}
    factor = factors.get(unit)
    return number * factor if factor is not None else None


def _vector_page(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    metadata: dict[str, Any] = {"format": suffix.lstrip(".").upper()}
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            page = reader.pages[0]
            width_pt = float(page.mediabox.width)
            height_pt = float(page.mediabox.height)
            metadata.update({"page_count": len(reader.pages), "page_width_pt": width_pt, "page_height_pt": height_pt})
            return {"page": {"width_mm": width_pt * 25.4 / 72.0, "height_mm": height_pt * 25.4 / 72.0}, "metadata": metadata}
        except Exception as exc:
            metadata["metadata_error"] = str(exc)
            return {"page": {"width_mm": None, "height_mm": None}, "metadata": metadata}
    if suffix == ".svg":
        try:
            root = ET.parse(path).getroot()
            width = _length_mm(root.attrib.get("width"))
            height = _length_mm(root.attrib.get("height"))
            view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
            if (width is None or height is None) and view_box:
                values = [_number(value) for value in re.split(r"[ ,]+", view_box.strip())]
                if len(values) == 4 and all(value is not None for value in values) and values[2] > 0 and values[3] > 0:
                    width = _length_mm(values[2])
                    height = _length_mm(values[3])
                    metadata["viewBox"] = values
            metadata.update({"declared_width": root.attrib.get("width"), "declared_height": root.attrib.get("height")})
            return {"page": {"width_mm": width, "height_mm": height}, "metadata": metadata}
        except (OSError, ET.ParseError) as exc:
            metadata["metadata_error"] = str(exc)
    return {"page": {"width_mm": None, "height_mm": None}, "metadata": metadata}


def _load_sidecar(path: Path | None) -> Mapping[str, Any] | None:
    if path is None:
        return None
    if not path.is_file():
        raise FileNotFoundError(f"几何 sidecar 不存在: {path}")
    return _load_json(path)


def _file_report(path: Path, contract: Mapping[str, Any], overlay: Path | None, *, force: bool = False, geometry_path: Path | None = None) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return audit_geometry_document(_load_json(path), contract, source=str(path.resolve()))
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}:
        from raster_checks import audit_raster
        sidecar_path = geometry_path
        if sidecar_path is None:
            candidate = path.with_suffix(".layout.json")
            sidecar_path = candidate if candidate.is_file() else None
        sidecar = _load_sidecar(sidecar_path)
        geometry_payload = _geometry_payload(sidecar) if sidecar else None
        geometry = geometry_payload.get("elements") if geometry_payload and isinstance(geometry_payload.get("elements"), list) else None
        page = _page_from_document(geometry_payload) if geometry_payload else None
        return audit_raster(path, contract, overlay_path=overlay, geometry=geometry, page=page, force=force)
    if suffix in {".pdf", ".svg", ".eps", ".ps"}:
        vector_info = _vector_page(path)
        sidecar_path = geometry_path
        if sidecar_path is None:
            candidate = path.with_suffix(".layout.json")
            sidecar_path = candidate if candidate.is_file() else None
        sidecar = _load_sidecar(sidecar_path)
        if sidecar is not None and _geometry_payload(sidecar) is not None:
            geometry_payload = _geometry_payload(sidecar)
            vector_page = vector_info["page"]
            if vector_page.get("width_mm") and vector_page.get("height_mm") and geometry_payload is not None:
                report = audit_boxes(vector_page, geometry_payload.get("elements", []), contract, backend="vector+geometry", source=str(path.resolve()), metadata={"geometry_sidecar": str(sidecar_path.resolve())})
            else:
                report = audit_geometry_document(sidecar, contract, source=str(path.resolve()))
            report["backend"] = "vector+geometry"
            report["page"] = vector_info["page"] if vector_info["page"].get("width_mm") else report.get("page")
            report["metadata"] = {**vector_info["metadata"], "geometry_sidecar": str(sidecar_path.resolve())}
            return report
        return {
            "schema_version": SCHEMA_VERSION,
            "backend": "vector-file",
            "source": str(path.resolve()),
            "page": vector_info["page"],
            "elements": [],
            "checks": [_issue("vector:geometry", "unknown", "矢量文件没有可验证的语义元素几何；请提供 layout_geometry sidecar", suggestion="从生成端导出对象边界和锚点后再运行 QA。")],
            "summary": {"status": "unknown", "counts": {"pass": 0, "warn": 0, "fail": 0, "unknown": 1}},
            "metadata": vector_info["metadata"],
            "notice": "QA 不是科学、可访问性或期刊合规认证；unknown 不代表通过。",
        }
    raise ValueError(f"不支持的输入格式: {path.suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查科研图布局、空间利用和标注关系并输出 JSON QA 报告")
    parser.add_argument("--input", required=True, type=Path, help="PNG/JPEG/TIFF、Origin 几何 JSON 或 PDF/SVG")
    parser.add_argument("--contract", type=Path, help="布局合同 JSON")
    parser.add_argument("--output", type=Path, help="报告路径，默认在输入旁生成 .layout-qa.json")
    parser.add_argument("--overlay", type=Path, help="栅格图 overlay 输出路径")
    parser.add_argument("--geometry", type=Path, help="可选的 layout_geometry sidecar JSON")
    parser.add_argument("--force", action="store_true", help="允许覆盖报告/overlay")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        contract = _load_json(args.contract) if args.contract else {}
        output = args.output or args.input.with_name(args.input.stem + ".layout-qa.json")
        overlay = args.overlay
        if output.resolve() == args.input.resolve():
            raise ValueError("报告输出路径不能覆盖输入图像")
        if overlay is not None and overlay.resolve() == args.input.resolve():
            raise ValueError("overlay 输出路径不能覆盖输入图像")
        report = _file_report(args.input, contract, overlay, force=args.force, geometry_path=args.geometry)
        _write_json(output, report, args.force)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("summary", {}).get("status") == "fail" else 0
    except (OSError, ValueError, ImportError, FileExistsError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
