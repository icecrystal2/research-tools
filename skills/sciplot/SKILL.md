---
name: sciplot
description: 科研绘图与布局质检统一入口。使用 SciencePlots/Matplotlib 生成可复现 Python 图，或通过 editaplot/Origin 生成可编辑项目，再检查重叠、裁切、留白和标注关系。用于从数据绘图、参考样式重绘、期刊版式准备及成图 QA；Origin 分支仅支持本地实体 Windows 和已安装的 Origin。
metadata:
  version: "1.0.0"
---

# SciPlot：科研绘图与布局质检

面向用户使用简体中文。该 skill 把两个互斥或按需并用的绘图后端与一个共用 QA 层组合起来；不是先 SciencePlots、再 Origin 的转换流水线。

## 选择路线

- 用户要求 Python、SciencePlots、代码复现、SVG，或未指定后端：使用 **SciencePlots + Matplotlib**。先读 [Python 路线](references/python-workflow.md)。
- 用户要求 Origin、OPJU、在 Origin 中继续编辑：使用 **editaplot + 本地 Origin**。先读 [Origin 路线](references/origin-workflow.md)，再按需读随包的完整 EditaPlot 指令。不能用 Python 图片冒充 Origin 项目，也不能在 Origin 不可用时静默替换路线。
- 仅检查已有图：直接用 QA 脚本；PNG 只能支持栅格证据，不能恢复对象语义。
- 用户明确要求两套产物：分别运行两条路线，共享数据语义和颜色映射，但不承诺自动样式/项目互转。

## 共用流程

1. 只读检查数据，确定变量、单位、列角色、缺失值和误差定义。能从输入可靠确定时直接执行；会改变科学解释的信息缺失时再询问。参考图不授权推测或编造数据。
2. 明确输出尺寸、格式和后端；用户指定的工具与样式优先。未指定期刊要求时给出一般科研图，不宣称期刊合规。默认 `science + nature + no-latex` 只是可更改的样式起点，不是 Nature 认证。
3. 保留源数据和变换记录。不默默平滑、插值、删点、拟合或改变误差定义；不跨缺失值连接曲线。只安装工作流必要的 Python 依赖，不自行安装/修改 Origin 或系统配置。
4. 渲染后执行 [布局 QA](references/qa-workflow.md)，合同结构见 [layout-contract](references/qa/layout-contract.md)。Python 显式注册关键 artist；Origin 使用实际读回的 `layout_geometry`。缺少几何/语义证据必须保留 `unknown`。
5. 对实际导出图再做栅格复核，并在最终尺寸下视觉检查。QA 只报告，不自动移动对象；调整后要重新导出与检查。`fail` 应修复或明确未交付状态，`warn` 需判断，`unknown` 绝不能写成通过。
6. 交付图、绘图脚本/Origin 项目、数据来源与变换摘要、QA JSON 和剩余限制。QA 与进程退出码不能替代科学含义、可访问性或期刊规则审查。

## 随包工具

所有路径相对本 skill 目录，执行时换成实际绝对路径：

- `scripts/plot_csv.py`：严格数值 CSV 的简单折线/散点起点；导出 PNG/PDF/SVG、对象 QA、栅格 QA 和来源清单。复杂图另写脚本并复用 QA API。
- `scripts/layout_qa.py`：栅格、PDF/SVG 元数据及几何 JSON 检查。
- `scripts/raster_checks.py`：像素边界、留白检查。
- `components/editaplot/`：保留许可的 EditaPlot 指令、启动器和必要源码，不含 Origin 程序、私人路径或虚拟环境。
- `requirements-python.txt`：Python 路线依赖约束；优先复用满足要求的环境，需安装时在项目环境中安装。

此 skill 不授权上传数据、修改仓库或发布产物；这些动作仍需来自当前用户请求。
