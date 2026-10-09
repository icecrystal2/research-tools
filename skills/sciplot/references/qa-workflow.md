# Figure Layout QA

这是一个独立的后渲染 QA 层。它和 `scientific-visualization`、`editaplot` 的绘图或
Origin/SciencePlots 样式选择分开：不改数据、不移动对象、不把 Origin API 和 Matplotlib
强行统一，也不把检查结果说成期刊合规证明。

## 何时使用

- 图已经渲染或导出，准备交付 PNG、PDF、SVG 或 Origin 项目时；
- 需要发现页面越界、对象重叠、边缘裁切、方向性空白、构图失衡，或连接线/复合标注的相对关系不合理时；
- Matplotlib/SciencePlots 和 Origin/editaplot 需要输出同一份可审计 QA 报告时。

不要仅凭 PNG 猜测对象的语义角色、分组或阅读方向。Matplotlib 工作流应在绘图脚本中
显式注册关键 artist 及其关系；Origin 工作流应提供 `layout_geometry` sidecar 或验证报告字段。
没有这些信息时，语义重叠、锚点和关系检查必须是 `unknown`，不能当作通过。

## 工作流

1. 为页面、对象和对象关系建立合同。页面坐标使用左上角为原点的毫米：`left_mm`、
   `top_mm`、`width_mm`、`height_mm`。合同格式和阈值见
   [qa/layout-contract.md](qa/layout-contract.md)。
2. Matplotlib 在 `fig.canvas.draw()` 后调用 `audit_matplotlib_figure`，传入显式的
   artist 注册表；连接线应注册起点和终点，复合标注应注册组成对象。需要图形化诊断时再
   调用 `write_matplotlib_overlay`。可使用 `--contract` 保存同样的锚点、方向、邻近和
   相对位置规则。
3. Origin/editaplot 使用 `audit_geometry_document` 检查 `layout_geometry`。如果现有
   `origin_verify_report.json` 没有该字段，保留 `unknown`，不要凭截图补填几何数据。
4. 对 PNG/JPEG/TIFF 运行栅格复核；它能检查内容边界、方向性空白和边缘触碰，但不能恢复
   语义对象。PDF/SVG 只有页面元数据或 sidecar 几何时，其他项目应保持 `unknown`。
5. 阅读 JSON 和可选 overlay。`fail` 需要修正后重跑，`warn` 需要人工判断，`unknown`
   表示证据不足；最终仍需在目标版面尺寸下人工查看。

## 命令行

```powershell
python `
  "<skill-root>\scripts\layout_qa.py" `
  --input figure.png --contract layout-contract.json `
  --output figure.layout-qa.json --overlay figure.overlay.png --force
```

几何 JSON 也可以直接作为 `--input`。脚本默认不覆盖已有报告；需要覆盖时显式加
`--force`。退出码为 `0`（pass/warn/unknown）或 `1`（存在 fail），因此 `unknown` 不会
被静默当作成功。PDF/SVG 可用 `--geometry path/to/figure.layout.json` 提供 sidecar；
未提供时只报告页面元数据和 `unknown`。

## 关系检查

`relations` 用来描述跨对象的可复核几何约束，不把某一种图或某一个标签名称写死。当前
支持三类通用关系：

- `proximity`：一个对象的锚点/包围盒点与另一个对象的点保持在 `max_distance_mm` 内；
- `relative_position`：一个对象相对另一个对象位于 `above`、`below`、`left_of` 或
  `right_of`，可设置 `min_gap_mm`；
- `direction`：连接线的 `start` 到 `end` 符合 `up`、`down`、`left`、`right` 或对角
  方向，并满足最小长度/轴向比例。

## 空间利用与构图平衡

包围盒占比只能回答“内容总体有多大”，不能回答空白是否集中在某一侧，或一个很小的
附加对象是否把页面跨度异常撑大。检查器默认运行保守的 `space_balance` 启发式，比较
内容包围盒的四边边距、内容重心和单个小元素造成的跨度增量；异常只报告 `warn`，不会
自动裁剪、移动或强行填满留白。留白可能是有意的，应在目标尺寸下结合渲染图判断。

需要更准确地表达构图范围时，可在合同中配置内容集合和豁免项：

```json
{
  "space_balance": {
    "include_elements": ["data_region", "annotation_group"],
    "exclude_roles": ["axis", "guide", "frame"],
    "max_margin_ratio": 3.0,
    "max_center_offset_fraction": 0.18,
    "check_isolated_expansion": true
  }
}
```

`include_elements`、`exclude_elements` 和 `exclude_roles` 只改变空间启发式的取样范围，
不会跳过边界、重叠或锚点检查。对确实应独立占据边缘的对象，可在注册项设置
`allow_space_expansion: true`，仅豁免“单个小元素撑大范围”的提示；若对象完全不应参与
构图范围估计，再设置 `exclude_from_space_balance: true`。不要把“充分利用空间”解释为
消除所有空白：目标是让信息层级、功能分区和页面比例协调，避免为了单个附加对象引入新的大
面积空白。

Matplotlib 注释注册项可用 `anchor_name: "end"` 和 `source_anchor_name: "start"` 记录
`xy`/`xytext`。这些关系检查的是空间组织和阅读顺序，不是对科学含义或美学的自动认证；
应将真正需要保持的关系写进合同，并在最终尺寸下人工复核。

## 解释边界

- 空白比例、边距平衡和孤立元素跨度都是启发式指标，不等于“美观”或“期刊规范”；
  阈值和取样范围应按图类型调整。
- 栅格图的像素颜色无法可靠分离曲线、文本和连接线，因此对象级重叠/锚点/关系没有几何
  证据时报告 `unknown`。
- 包围盒检查不能替代视觉层级判断：线条是否与图标混淆、文字是否形成自然的复合标注、
  连接线是否从正确的语义来源发出，都需要注册关系并结合渲染图人工复核。
- 检查器只报告证据和调整建议，不自动修改图。样式（包括 `science + nature`）仍由
  SciencePlots 或 Origin 分支分别管理。
