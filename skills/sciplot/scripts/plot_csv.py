#!/usr/bin/env python3
"""Minimal reproducible numeric CSV plot; complex figures should reuse the QA API."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import sys


def read_numeric_csv(path: Path, x_column: str, y_columns: list[str], delimiter: str = ',') -> dict[str, list[float]]:
    if not y_columns or len(set(y_columns)) != len(y_columns) or x_column in y_columns:
        raise ValueError('Y 列必须非空、互不重复，并不同于 X 列')
    if len(delimiter) != 1:
        raise ValueError('分隔符必须恰好一个字符')
    selected = [x_column, *y_columns]
    result = {name: [] for name in selected}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        headers = next(reader, None)
        if not headers or any(not name.strip() for name in headers) or len(set(headers)) != len(headers):
            raise ValueError('需要非空且唯一的 CSV 表头')
        if not set(selected).issubset(headers):
            raise ValueError(f'所选列不在表头中；可用列为 {headers}')
        positions = {name: headers.index(name) for name in selected}
        for row_number, row in enumerate(reader, 2):
            if len(row) != len(headers):
                raise ValueError(f'第 {row_number} 行列数不符；不会静默跳过')
            for name, index in positions.items():
                text = row[index].strip()
                if not text or text.casefold() == 'nan':
                    value = float('nan')
                else:
                    try:
                        value = float(text)
                    except ValueError as exc:
                        raise ValueError(f'第 {row_number} 行 {name!r} 不是数值: {text!r}') from exc
                    if not math.isfinite(value):
                        raise ValueError(f'第 {row_number} 行 {name!r} 含无穷值')
                result[name].append(value)
    if not result[x_column]:
        raise ValueError('CSV 没有数据行')
    for name in y_columns:
        if not any(math.isfinite(x) and math.isfinite(y) for x, y in zip(result[x_column], result[name])):
            raise ValueError(f'{name!r} 没有可绘制的有限 X/Y 配对')
    return result


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('必须是有限正数')
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('input', type=Path)
    result.add_argument('--x', required=True)
    result.add_argument('--y', nargs='+', required=True)
    result.add_argument('--delimiter', default=',')
    result.add_argument('--kind', choices=('line', 'scatter'), default='line')
    result.add_argument('--xlabel')
    result.add_argument('--ylabel')
    result.add_argument('--title')
    result.add_argument('--styles', nargs='+', default=['science', 'nature', 'no-latex'])
    result.add_argument('--width-mm', type=positive_float, default=90)
    result.add_argument('--height-mm', type=positive_float, default=65)
    result.add_argument('--dpi', type=positive_float, default=300)
    result.add_argument('--contract', type=Path)
    result.add_argument('--outdir', type=Path, required=True)
    return result


def render(args: argparse.Namespace) -> dict:
    source = args.input.resolve(strict=True)
    source_bytes = source.read_bytes()
    data = read_numeric_csv(source, args.x, args.y, args.delimiter)
    if source.read_bytes() != source_bytes:
        raise ValueError('读取期间源文件变化，停止以避免来源记录不一致')
    contract = json.loads(args.contract.read_text(encoding='utf-8')) if args.contract else {}
    if not isinstance(contract, dict):
        raise ValueError('合同必须是 JSON 对象')
    outdir = args.outdir.resolve()
    names = ['figure.png', 'figure.pdf', 'figure.svg', 'figure.layout-qa.json',
             'figure.raster-qa.json', 'provenance.json', 'source-data.csv',
             'plot_csv.py', 'layout_qa.py', 'raster_checks.py', 'layout-contract.json']
    if outdir.exists() and not outdir.is_dir():
        raise ValueError('输出路径不是目录')
    for name in names:
        if (outdir / name).exists():
            raise FileExistsError(f'拒绝覆盖: {outdir / name}')
    # Imports register SciencePlots styles, but do not install dependencies or start Origin.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import scienceplots  # noqa: F401
    from layout_qa import audit_matplotlib_figure
    from raster_checks import audit_raster

    with plt.style.context(args.styles), matplotlib.rc_context({
        'savefig.bbox': None, 'pdf.fonttype': 42, 'ps.fonttype': 42,
    }):
        fig, ax = plt.subplots(figsize=(args.width_mm / 25.4, args.height_mm / 25.4), dpi=args.dpi)
        try:
            registry = {}
            for index, name in enumerate(args.y):
                if args.kind == 'line':
                    artist, = ax.plot(data[args.x], data[name], label=name)
                else:
                    artist = ax.scatter(data[args.x], data[name], label=name)
                # Overlapping data marks within the same plot are intentional, not label exemptions.
                registry[f'data_{index}'] = {'artist': artist, 'role': 'data', 'allow_overlap': True}
            ax.set_xlabel(args.xlabel or args.x)
            ax.set_ylabel(args.ylabel or (args.y[0] if len(args.y) == 1 else 'Value'))
            if args.title:
                ax.set_title(args.title)
                registry['title'] = {'artist': ax.title, 'role': 'label'}
            if len(args.y) > 1:
                legend = ax.legend()
                registry['legend'] = {'artist': legend, 'role': 'legend'}
            fig.tight_layout()  # Adjust internal axes, NOT export canvas cropping.
            fig.canvas.draw()
            registry['xlabel'] = {'artist': ax.xaxis.label, 'role': 'label'}
            registry['ylabel'] = {'artist': ax.yaxis.label, 'role': 'label'}
            for axis_name, axis in [('x', ax.xaxis), ('y', ax.yaxis)]:
                lower, upper = sorted(axis.get_view_interval())
                # Locators return extra ticks outside the axis interval; Matplotlib
                # does not paint those. Audit drawn ticks without exempting clipping.
                for index, (location, label) in enumerate(zip(axis.get_majorticklocs(), axis.get_majorticklabels())):
                    if lower <= location <= upper and label.get_visible() and label.get_text():
                        registry[f'{axis_name}tick_{index}'] = {'artist': label, 'role': 'tick_label'}
            for axis_name, axis in [('x', ax.xaxis), ('y', ax.yaxis)]:
                if axis.offsetText.get_visible() and axis.offsetText.get_text():
                    registry[f'{axis_name}offset'] = {'artist': axis.offsetText, 'role': 'label'}
            report = audit_matplotlib_figure(fig, contract=contract, artists=registry, source='figure.png')
            report['scope'] = '显式注册的数据、标签、刻度与图例；不是全部科学语义或出版合规检查'
            outdir.mkdir(parents=True, exist_ok=True)
            for suffix in ('png', 'pdf', 'svg'):
                fig.savefig(outdir / f'figure.{suffix}', dpi=args.dpi, bbox_inches=None)
        finally:
            plt.close(fig)
    raster = audit_raster(outdir / 'figure.png', contract)
    for name, content in [('figure.layout-qa.json', report), ('figure.raster-qa.json', raster),
                          ('layout-contract.json', contract)]:
        (outdir / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding='utf-8')
    (outdir / 'source-data.csv').write_bytes(source_bytes)
    script_dir = Path(__file__).resolve().parent
    for name in ('plot_csv.py', 'layout_qa.py', 'raster_checks.py'):
        shutil.copy2(script_dir / name, outdir / name)
    parameters = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    provenance = {
        'backend': 'matplotlib/scienceplots',
        'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'source_name': source.name,
        'parameters': parameters,
        'versions': {name: importlib.metadata.version(name) for name in ['matplotlib', 'SciencePlots', 'numpy', 'Pillow']},
        'transformations': ['按用户明确指定列读取；空值/NaN 保留；不排序、不平滑、不插值、不拟合'],
        'qa_status': report['summary']['status'],
        'raster_status': raster['summary']['status'],
        'reproduce': '在新目录用随包 plot_csv.py/source-data.csv，按 parameters 重建命令；缺依赖时在项目环境安装。',
        'remaining_review': ['在目标尺寸下查看实际 PNG/PDF/SVG', '科学解释与精确期刊要求未自动认证'],
    }
    (outdir / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(outdir), 'layout': report['summary'], 'raster': raster['summary']}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = render(args)
    except (ValueError, OSError, ImportError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 1 if any(result[key]['counts'].get('fail', 0) for key in ['layout', 'raster']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
