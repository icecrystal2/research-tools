# Python / SciencePlots 路线

先复用项目 Python 环境，检查 `matplotlib`、`scienceplots`、`numpy`、`PIL`。缺依赖时才在项目环境安装 `requirements-python.txt`；不修改全局 Python。SciencePlots 是样式包，必须先 `import scienceplots` 注册样式。

普通数值 CSV 可用随包脚本（`<skill-root>` 换成实际路径）：

```powershell
python "<skill-root>\scripts\plot_csv.py" "<data.csv>" `
  --x "time_s" --y "signal" --xlabel "Time (s)" --ylabel "Signal (a.u.)" `
  --outdir "<delivery-dir>" --width-mm 90 --height-mm 65
```

默认折线图；`--kind scatter` 用于散点。明确传入所有需要的 Y 列，脚本不会自动排序、聚合或拟合。空值/`NaN` 保留为缺失，折线在缺失处断开；非数值、无穷值、歧义表头会报错。CSV 方言默认逗号，可用 `--delimiter`；不在这个脚本里猜测 Excel 或 TXT。复杂文件用可靠解析器先只读检查，保留源文件和转换说明。

`--styles science nature no-latex` 是可更改默认值；用户需要 LaTeX 时显式改变样式并先检查可用环境，失败不能静默改变字体。页面默认 90×65 mm、300 DPI，只是一般起点。精确期刊、文章类型和投稿阶段已知时验证官方要求，不把 DPI 或样式名当成合规证明。脚本的退出码 1 表示 QA 中存在 fail；0 仍可能含 warn/unknown。输出目录存在同名产物时拒绝覆盖。

## 自定义图中的对象检查

把 `<skill-root>/scripts` 加入当前脚本的模块路径后：

```python
from layout_qa import audit_matplotlib_figure
fig.canvas.draw()
report = audit_matplotlib_figure(fig, contract=contract, artists={
    "curve": {"artist": line, "role": "data", "allow_overlap": True},
    "xlabel": {"artist": ax.xaxis.label, "role": "label"},
    "ylabel": {"artist": ax.yaxis.label, "role": "label"},
})
```

只豁免有意的数据区域相交；不要为了让检查通过，把所有对象都设成 `allow_overlap`。包含图例、刻度、子图标题、箭头、复合标注时逐项注册及建立真实关系。

**同一页面坐标必须对应实际产物。** 简单脚本使用固定画布且不启用 `bbox_inches="tight"`，在 PNG 的同一 DPI 下运行对象检查，再导出。若做紧裁切/外部排版，应在最终页面几何上重新检查，不沿用裁切前报告。PNG 需再运行栅格检查；PDF/SVG 渲染器有差异，交付前查看实际产物。

交付源数据哈希、精确依赖版本、参数/样式、可执行脚本和 QA；输出中明确允许的空白、未检查的科学语义和最终尺寸视觉复核结果。
