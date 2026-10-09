# Research Tools：科研工具仓库

当前包含 **SciPlot 科研绘图与布局质检**，后续科研工具按独立 skill 目录管理。

一个 Codex skill，统一两条绘图路线：

```text
SciencePlots + Matplotlib → Python 科研图 ─┐
                                          ├→ figure-layout-qa → 实际产物复核
editaplot + Origin         → 可编辑 OPJU ──┘
```

skill 名称 **`sciplot`**；调用方式 **`$sciplot`**。不强制两种后端串行运行，不承诺自动转换样式或项目。

## 安装到 Codex

将本仓库的 `skills/sciplot` 整个目录复制到 `$CODEX_HOME/skills/sciplot`（未设置 CODEX_HOME 时为用户目录 `.codex/skills/sciplot`）。保留所有子目录；若已存在，先检查并备份，不盲目覆盖。

重新打开 Codex 或新建聊天，确认 skill 列表后调用：

> 使用 $sciplot，根据这个 CSV 画科研图，用 SciencePlots 的 science + nature 样式，检查布局并交付图和脚本。

> 使用 $sciplot，用 Origin 输出可编辑 OPJU，同时检查导出图是否重叠、裁切和构图失衡。

Python 路线可跨平台运行，使用项目环境安装 `skills/sciplot/requirements-python.txt`。Origin 路线限实体 Windows 10/11 x64 和已安装兼容 Origin，使用随包 EditaPlot 的独立受约束环境。该仓库不包含 Origin、不包含 Python 二进制包/虚拟环境。

## 内容与验证

- 单一 `SKILL.md` 总入口和 UI 元数据；两条分支按需加载。
- 最小数值 CSV 绘图脚本；对象 QA、栅格 QA 和来源清单。
- 保留已有 figure-layout-qa 脚本与合同；unknown 不是通过。
- 随包 EditaPlot 启动器、必要源码、模板与许可，不包含本机配置、历史科研数据、缓存或支付资产。
- `tests/test_sciplot.py`：数值/缺失值、实际导出、几何失败、证据不足和不可覆盖行为检查。

在仓库根目录执行 `python -m unittest discover -s tests -v`。安装依赖与运行 Origin 是不同事项；源码通过和通用测试通过不能替代真实 Origin smoke/render/verify。QA 也不是科学、可访问性或期刊合规认证。

许可：新包装和 EditaPlot 源码使用 Apache-2.0；复用 QA 代码按其 skill 元数据的 MIT 许可保留独立声明。SciencePlots、Matplotlib 和其他依赖不随包分发，由各自许可管辖。详见 `THIRD_PARTY_NOTICES.md`。
