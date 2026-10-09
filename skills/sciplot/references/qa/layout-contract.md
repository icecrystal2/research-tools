# Figure Layout QA 合同

## 坐标和最小结构

所有页面和元素都使用毫米，原点在页面左上角。`layout_geometry` 可以嵌在 Origin
验证 JSON 中，也可以单独保存：

```json
{
  "schema_version": "figure-layout-qa/1.0",
  "page": {"width_mm": 180, "height_mm": 100},
  "elements": [
    {
      "id": "data_trace",
      "role": "data",
      "left_mm": 18,
      "top_mm": 20,
      "width_mm": 145,
      "height_mm": 55
    },
    {
      "id": "annotation_arrow",
      "role": "arrow",
      "left_mm": 22,
      "top_mm": 62,
      "width_mm": 20,
      "height_mm": 24,
      "anchors": {"end": [38, 61]}
    }
  ]
}
```

元素也可用 `bbox_mm: [left, top, width, height]`。`visible: false` 的元素不参与检查。
允许有意越界时设置 `allow_outside: true`，允许一对对象重叠时在元素上设置
`allow_overlap: true`，或在合同中列出 `ignore_overlap_pairs`。

## 阈值

合同可以覆盖以下默认值：

```json
{
  "thresholds": {
    "edge_tolerance_mm": 0.25,
    "overlap_warn_ratio": 0.05,
    "overlap_fail_ratio": 0.25,
    "blank_warn_content_fraction": 0.18,
    "blank_fail_content_fraction": null,
    "anchor_tolerance_mm": 1.5,
    "raster_background_tolerance": 18,
    "raster_edge_fraction_warn": 0.001
  },
  "required_elements": ["data_trace", "annotation_arrow"],
  "anchors": [
    {
      "id": "annotation_arrow.end",
      "element": "annotation_arrow",
      "actual": "end",
      "target": {"element": "data_trace", "point": "center"},
      "tolerance_mm": 2.0
    }
  ]
}
```

`blank_fail_content_fraction` 默认为空，因此空白过多通常只产生 `warn`。只有在图的
版式确实规定了最小内容占比时才设置它。重叠比例定义为交集面积除以两个元素中较小的
面积；线条的包围盒很薄，是否允许与数据区域重叠应由合同明确决定。

## 空间利用与构图平衡

`blank-space` 检查使用内容包围盒的总占比；`space_balance` 进一步检查四边边距是否
明显失衡、内容重心是否偏移，以及一个小型附加元素是否异常扩大内容跨度。它是跨图形
类型的启发式，不规定页面必须对称或把所有留白填满，默认只产生 `warn`。故意留出的
标题区、图例区或出版版面留白应在目标尺寸下人工确认。

可选合同字段如下：

```json
{
  "space_balance": {
    "enabled": true,
    "include_elements": ["data_region", "annotation_group"],
    "exclude_elements": ["page_frame"],
    "exclude_roles": ["axis", "guide", "frame", "border"],
    "max_margin_ratio": 3.0,
    "max_margin_difference_fraction": 0.18,
    "min_large_margin_fraction": 0.22,
    "max_center_offset_fraction": 0.18,
    "check_isolated_expansion": true,
    "small_element_fraction": 0.08,
    "max_isolated_span_ratio": 0.40,
    "min_isolated_expansion_mm": 8.0
  }
}
```

未提供 `include_elements` 时，检查可见且有面积的元素，并默认排除结构性角色。提供
`include_elements` 后只用列出的对象估计构图范围；这些选择不会跳过边界、重叠、锚点或
关系检查。`allow_space_expansion: true` 可用于确实需要独立占据边缘、但不应触发“单个
小元素撑大范围”提示的注册项；`exclude_from_space_balance: true` 才会将对象完全排除。
若不需要
该启发式，可将 `space_balance` 设为 `false`，但应说明原因。检查器只报告证据和建议，
不会自动移动对象、改变画布比例或裁剪输出。

## 锚点规则

`target` 可以是 `[x_mm, y_mm]`，也可以引用元素并指定 `point`：`center`、`top`、
`bottom`、`left`、`right`、`top_left`、`top_right`、`bottom_left` 或 `bottom_right`。
Matplotlib 的 annotation 会把 `xy` 转成实际目标点；通用 artist 可以在注册描述中
提供 `anchors`。Origin 必须在几何 JSON 中提供同样的实际点。

## 跨元素关系

仅检查单个包围盒不足以判断复合标注是否保持自然的阅读顺序。可在合同中加入通用的
`relations`，把需要维护的空间关系显式化：

```json
{
  "relations": [
    {
      "id": "annotation-source-near-symbol",
      "type": "proximity",
      "element": "annotation_arrow",
      "actual": "start",
      "target": {"element": "symbol_group", "point": "bottom"},
      "max_distance_mm": 6
    },
    {
      "id": "annotation-points-down",
      "type": "direction",
      "element": "annotation_arrow",
      "from": "start",
      "to": "end",
      "expected": "down",
      "min_length_mm": 4,
      "min_axis_ratio": 1.5
    },
    {
      "id": "label-above-symbol",
      "type": "relative_position",
      "element": "annotation_label",
      "actual": "center",
      "target": {"element": "symbol_group", "point": "center"},
      "expected": "above"
    }
  ]
}
```

这些是可复用的关系类型，不规定具体领域、颜色或文字。`proximity` 适合检查属于同一
视觉单元的对象是否被拉开；`relative_position` 适合检查标签、图标和数据区域的阅读顺序；
`direction` 适合检查任意注释引线、流程箭头或事件指示线。页面坐标的正 `y` 向下，因此
`down` 与 `up` 按页面坐标解释。缺少起终点或目标对象时应报告 `unknown`，不能用截图猜测。

## 报告合同

脚本输出 `figure-layout-qa/1.0` JSON：

```json
{
  "schema_version": "figure-layout-qa/1.0",
  "backend": "matplotlib",
  "summary": {"status": "warn", "counts": {"pass": 3, "warn": 1, "fail": 0, "unknown": 0}},
  "checks": [
    {
      "id": "overlap:annotation_arrow:data_trace",
      "status": "warn",
      "elements": ["annotation_arrow", "data_trace"],
      "metrics": {"intersection_area_mm2": 2.1, "overlap_ratio": 0.08},
      "message": "元素包围盒有明显交叠",
      "suggestion": "确认交叠是否是语义需要；否则移动标签或箭头。"
    }
  ],
  "elements": [],
  "notice": "QA 不是科学、可访问性或期刊合规认证。"
}
```

统一状态只有 `pass`、`warn`、`fail`、`unknown`。总体状态优先级为
`fail > unknown > warn > pass`。这使 Origin 缺少几何字段时不会被误报为通过。

## Matplotlib 注册示例

```python
from layout_qa import audit_matplotlib_figure, write_matplotlib_overlay

contract = {
    "required_elements": ["data_trace", "data_label", "annotation_arrow"],
    "ignore_overlap_pairs": [["annotation_arrow", "data_trace"]],
    "anchors": [{
        "id": "annotation_arrow.end",
        "element": "annotation_arrow",
        "actual": "end",
        "target": {"element": "data_trace", "point": "center"},
        "tolerance_mm": 1.5,
    }],
}
report = audit_matplotlib_figure(fig, contract, {
    "data_trace": {"artist": curve, "role": "data", "allow_overlap": True},
    "data_label": {"artist": label, "role": "text"},
    "annotation_arrow": {
        "artist": arrow,
        "role": "arrow",
        "anchor_name": "end",
        "source_anchor_name": "start",
    },
})
write_matplotlib_overlay(fig, report, "figure.overlay.png", force=True)
```

注册表中的 `artist` 必须是实际绘制对象；不要用截图坐标手工伪造 Matplotlib 的实际
包围盒。若注释端点不是 `Annotation.xy`，在注册项中直接提供 `anchors` 毫米坐标。
