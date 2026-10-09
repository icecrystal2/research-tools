# 组件来源与许可

## 新包装

`skills/sciplot/SKILL.md`、分支说明、CSV 示例脚本、测试和包装文档按根目录 Apache License 2.0 发布。

## EditaPlot

`skills/sciplot/components/editaplot/` 来自用户已有的本地 EditaPlot 安装及其 curated runtime。保留上游 LICENSE 和 NOTICE（Apache-2.0）。未改动上游运行源码；排除虚拟环境、缓存、安装状态、本机路径及无关资产。本项目不分发 Origin/OriginPro，不与 OriginLab 关联或获其背书。模板及调色板的解释边界见上游 NOTICE。

## figure-layout-qa

`skills/sciplot/scripts/layout_qa.py`、`raster_checks.py` 及 `references/qa/layout-contract.md` 复制自用户已有的 `figure-layout-qa` 1.2。上游 SKILL.md 元数据标明 MIT，未提供单独版权人声明；这里不臆造版权人。QA 工作流说明仅将机器路径替换为可移植路径、调整合同链接。许可文本见 `licenses/figure-layout-qa-MIT.txt`。

## Python 依赖

SciencePlots、Matplotlib、NumPy、Pillow、pypdf 以及 EditaPlot 声明的 Python 依赖均未以源包、wheel 或二进制形式分发。安装时各自许可继续适用；不能把本仓库许可视为这些依赖的替代许可。
