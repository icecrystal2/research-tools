# editaplot / Origin 路线

随包完整入口为 [EditaPlot 指令](../components/editaplot/SKILL.md)，必须先阅读；其平台门禁、语义确认、隔离实例、模板能力与产物反读要求继续生效。详细 CLI 见 [runtime](../components/editaplot/references/runtime.md)，产物门禁见 [verification](../components/editaplot/references/verification.md)。仅加载当前任务需要的其他参考。

路径不依赖原作者电脑：

- 启动器：`<skill-root>/components/editaplot/editaplot.cmd`
- 随包 engine：`<skill-root>/components/editaplot/runtime`
- 先运行启动器的 `doctor --engine-home <engine>`；普通 doctor 不授权修复或安装。只有确有缺失且当前任务允许依赖安装时，才执行按上游指令限定的 repair。
- 按上游顺序执行 start/语义确认、冻结计划、实时 `origin-smoke`、能力门禁、render、verify。不要根据一个成功连接宣称所有图型可用。

实体 Windows 10/11 x64、兼容的 CPython 3.10–3.12 和已安装可调用的 Origin/OriginPro 2021+ 是该分支前提。VM、WSL、macOS、Linux 不属于此随包 EditaPlot 支持范围；其他 2021+ 版本仍需实际 capability/产物验证。不能安装 Origin、改变 DCOM/注册表/防火墙、操纵或关闭用户自己的 Origin 项目。无合适执行权限时报告限制，不绕过运行环境。

完成上游反读后，用实际 `layout_geometry` 对接根目录 `scripts/layout_qa.py`。验证报告缺少几何时保留 unknown，可额外检查导出的 PNG，但不能从 PNG 猜出几何对象。Origin 的样式独立管理，SciencePlots 样式不能自动应用给 Origin。

当前包装只做源码/诊断验证，**不等同于在新机器上完成实时 Origin 绘图验证**。每次实际任务仍需完整门禁。最终交付 OPJU、指定导出格式、冻结计划、反读报告、统一布局 QA，以及任何未满足项。
