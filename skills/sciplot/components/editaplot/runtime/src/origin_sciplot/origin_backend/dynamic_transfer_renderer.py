"""Editable Origin renderer for site-wise dynamic-transfer dumbbells."""

from __future__ import annotations

import math
from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from origin_sciplot.logging_utils import RunLogger
from origin_sciplot.output_manager import RunOutput, write_json
from origin_sciplot.scientific_workflow import (
    ScientificPreparation,
    ScientificWorkflowError,
    prepare_scientific,
)
from origin_sciplot.template_registry import TemplateManifest

from .base_style_contract import page_size_inches, pt_to_origin_width_units
from .export_utils import export_graph
from .safe_errors import OriginDrawError
from .scientific_renderer import (
    _apply_axis_label_font,
    _figure_style,
    _origin_font_code,
    _position_axis_titles_on_page,
    _set_borderless_legend,
    _style_axis,
    _style_label,
)
from .session import OriginSession
from .verify_utils import (
    read_layer_geometry_percent,
    require_nonempty,
    verify_page_and_layer,
    verify_plot_color,
    verify_plot_line_widths,
    verify_symbol_style,
    verify_text_fonts,
    verify_text_sizes,
)


RAW_COLOR = "#5D6770"
IMPROVED_COLOR = "#2F6FA3"
DEGRADED_COLOR = "#C54F54"
CONNECTOR_COLOR = "#9AA3AB"


def _resolve_preparation(
    manifest: TemplateManifest,
    frame: pd.DataFrame,
    output: RunOutput,
    preparation: ScientificPreparation | None,
) -> ScientificPreparation:
    resolved = preparation or prepare_scientific(output.input_copy, manifest.id)
    if resolved.template_id != manifest.id:
        raise OriginDrawError(
            f"Dynamic-transfer preparation {resolved.template_id!r} does not match {manifest.id!r}."
        )
    if tuple(map(str, frame.columns)) != resolved.source_columns:
        raise OriginDrawError("Dynamic-transfer source columns do not match the validated copy.")
    if resolved.requires_confirmation:
        raise OriginDrawError("Column mapping confirmation is required before Origin can run.")
    if resolved.plot_spec.plot_kind != "dynamic_transfer":
        raise OriginDrawError(
            f"Unsupported paired-trajectory plot kind: {resolved.plot_spec.plot_kind!r}."
        )
    return resolved


def _role_columns(preparation: ScientificPreparation) -> dict[str, str]:
    assignments = dict(preparation.assignments)
    required = ("category", "rmse_raw", "rmse_corrected", "ubrmse_raw", "ubrmse_corrected")
    columns: dict[str, str] = {}
    for role in required:
        matches = [column for column, assigned in assignments.items() if assigned == role]
        if len(matches) != 1:
            raise OriginDrawError(f"Dynamic-transfer role {role!r} is not uniquely mapped.")
        columns[role] = matches[0]
    return columns


def _ordered_rows(
    frame: pd.DataFrame,
    preparation: ScientificPreparation,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    columns = _role_columns(preparation)
    category_values = frame[columns["category"]].astype(str).str.strip().tolist()
    order = tuple(preparation.plot_spec.category_order)
    if not order:
        raw = frame[columns["rmse_raw"]].to_numpy(dtype=float, copy=True)
        order = tuple(category_values[index] for index in np.argsort(raw, kind="mergesort"))
    row_by_category = {value: index for index, value in enumerate(category_values)}
    if len(row_by_category) != len(category_values):
        raise OriginDrawError("Dynamic-transfer site labels are not unique.")
    try:
        indices = [row_by_category[value] for value in order]
    except KeyError as exc:
        raise OriginDrawError("Dynamic-transfer category order does not match the source rows.") from exc
    return frame.iloc[indices].copy(deep=True), order


def _build_helper_frames(
    frame: pd.DataFrame,
    preparation: ScientificPreparation,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Create connector/point datasets while leaving the source frame untouched."""

    columns = _role_columns(preparation)
    ordered, labels = _ordered_rows(frame, preparation)
    n = len(ordered)
    y = np.arange(1, n + 1, dtype=float)
    rmse_raw = ordered[columns["rmse_raw"]].to_numpy(dtype=float, copy=True)
    rmse_corrected = ordered[columns["rmse_corrected"]].to_numpy(dtype=float, copy=True)
    ubrmse_raw = ordered[columns["ubrmse_raw"]].to_numpy(dtype=float, copy=True)
    ubrmse_corrected = ordered[columns["ubrmse_corrected"]].to_numpy(dtype=float, copy=True)

    def connector(raw: np.ndarray, corrected: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        length = max(1, n * 3)
        improve_x = np.full(length, np.nan, dtype=float)
        improve_y = np.full(length, np.nan, dtype=float)
        degrade_x = np.full(length, np.nan, dtype=float)
        degrade_y = np.full(length, np.nan, dtype=float)
        improved = corrected < raw
        for index in range(n):
            start = index * 3
            target_x = improve_x if improved[index] else degrade_x
            target_y = improve_y if improved[index] else degrade_y
            target_x[start : start + 2] = (raw[index], corrected[index])
            target_y[start : start + 2] = (y[index], y[index])
        return improve_x, improve_y, degrade_x, degrade_y

    rmse_ix, rmse_iy, rmse_dx, rmse_dy = connector(rmse_raw, rmse_corrected)
    ubrmse_ix, ubrmse_iy, ubrmse_dx, ubrmse_dy = connector(ubrmse_raw, ubrmse_corrected)
    helper_length = max(1, n * 3)

    def point(values: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x_values = np.full(helper_length, np.nan, dtype=float)
        y_values = np.full(helper_length, np.nan, dtype=float)
        x_values[:n] = np.where(mask, values, np.nan)
        y_values[:n] = np.where(mask, y, np.nan)
        return x_values, y_values

    all_true = np.ones(n, dtype=bool)
    rmse_improved = rmse_corrected < rmse_raw
    ubrmse_improved = ubrmse_corrected < ubrmse_raw
    rmse_raw_x, rmse_raw_y = point(rmse_raw, all_true)
    rmse_ix_points, rmse_iy_points = point(rmse_corrected, rmse_improved)
    rmse_dx_points, rmse_dy_points = point(rmse_corrected, ~rmse_improved)
    ubrmse_raw_x, ubrmse_raw_y = point(ubrmse_raw, all_true)
    ubrmse_ix_points, ubrmse_iy_points = point(ubrmse_corrected, ubrmse_improved)
    ubrmse_dx_points, ubrmse_dy_points = point(ubrmse_corrected, ~ubrmse_improved)

    helper = pd.DataFrame(
        {
            "__YPos": np.pad(y, (0, helper_length - n), constant_values=np.nan),
            "__RMSE_Improve_X": rmse_ix,
            "__RMSE_Improve_Y": rmse_iy,
            "__RMSE_Degraded_X": rmse_dx,
            "__RMSE_Degraded_Y": rmse_dy,
            "__RMSE_Raw_X": rmse_raw_x,
            "__RMSE_Raw_Y": rmse_raw_y,
            "__RMSE_Improved_X": rmse_ix_points,
            "__RMSE_Improved_Y": rmse_iy_points,
            "__RMSE_DegradedPoint_X": rmse_dx_points,
            "__RMSE_DegradedPoint_Y": rmse_dy_points,
            "__ubRMSE_Improve_X": ubrmse_ix,
            "__ubRMSE_Improve_Y": ubrmse_iy,
            "__ubRMSE_Degraded_X": ubrmse_dx,
            "__ubRMSE_Degraded_Y": ubrmse_dy,
            "__ubRMSE_Raw_X": ubrmse_raw_x,
            "__ubRMSE_Raw_Y": ubrmse_raw_y,
            "__ubRMSE_Improved_X": ubrmse_ix_points,
            "__ubRMSE_Improved_Y": ubrmse_iy_points,
            "__ubRMSE_DegradedPoint_X": ubrmse_dx_points,
            "__ubRMSE_DegradedPoint_Y": ubrmse_dy_points,
        }
    )
    labels_frame = pd.DataFrame({"Site": list(labels)})
    state = {
        "category_order_bottom_to_top": list(labels),
        "category_order_top_to_bottom": list(reversed(labels)),
        "row_count": n,
        "rmse_improved_count": int(np.sum(rmse_improved)),
        "rmse_degraded_count": int(np.sum(~rmse_improved)),
        "ubrmse_improved_count": int(np.sum(ubrmse_improved)),
        "ubrmse_degraded_count": int(np.sum(~ubrmse_improved)),
    }
    return helper, labels_frame, state


def _set_page_size(graph: Any, style: Any) -> dict[str, float]:
    width_in, height_in = page_size_inches(style)
    graph.activate()
    if not graph.obj.LT_execute(
        "page.updatetoprinter=0;page.kar=0;"
        f"page.width=({width_in:g})*page.resx;"
        f"page.height=({height_in:g})*page.resy;doc -uw;"
    ):
        raise OriginDrawError("Origin could not set the dynamic-transfer page size.")
    return {
        "page_width_cm": float(graph.obj.GetWidth() * 2.54),
        "page_height_cm": float(graph.obj.GetHeight() * 2.54),
    }


def _plot(
    op: Any,
    layer: Any,
    helper_sheet: Any,
    *,
    y_column: str,
    x_column: str,
    plot_type: str,
    color: str,
    style: Any,
    marker_size_pt: float,
    interior: int | None = None,
) -> Any:
    plot = layer.add_plot(helper_sheet, y_column, x_column, type=plot_type)
    if plot is None:
        raise OriginDrawError(f"Origin could not add dynamic-transfer plot {y_column}.")
    color_code = op.ocolor(color)
    plot.color = color_code
    plot.set_cmd(f"-c color({color})", f"-cl {color_code}")
    if plot_type == "l":
        plot.set_cmd(f"-w {pt_to_origin_width_units(style.plot_line_width_pt * 0.72):g}", "-d 0")
    else:
        plot.set_cmd(f"-w {pt_to_origin_width_units(style.frame_line_width_pt):g}")
        plot.symbol_kind = 2
        plot.symbol_interior = int(interior if interior is not None else 1)
        plot.symbol_size = marker_size_pt
        plot.set_cmd(f"-kh 50")
    return plot


def _style_dynamic_axes(
    op: Any,
    layer: Any,
    *,
    style: Any,
    x_plan: Any,
    labels_sheet: Any,
    y_count: int,
    x_title: str,
    show_y_labels: bool,
) -> dict[str, Any]:
    font_code = _origin_font_code(op, style.font_family)
    _style_axis(
        layer,
        "x",
        visible=True,
        numeric_labels=True,
        minor_ticks=1,
        style=style,
        font_code=font_code,
    )
    _style_axis(
        layer,
        "y",
        visible=show_y_labels,
        numeric_labels=True,
        minor_ticks=0,
        style=style,
        font_code=font_code,
    )
    _style_axis(layer, "x2", visible=False, numeric_labels=True, minor_ticks=0, style=style, font_code=font_code)
    _style_axis(layer, "y2", visible=False, numeric_labels=True, minor_ticks=0, style=style, font_code=font_code)
    if x_plan.x_from is None or x_plan.x_to is None:
        raise OriginDrawError("Dynamic-transfer X axis plan is incomplete.")
    layer.axis("x").scale = "linear"
    layer.axis("x").set_limits(x_plan.x_from, x_plan.x_to, x_plan.x_step)
    layer.axis("y").scale = "linear"
    layer.axis("y").set_limits(x_plan.y_from, x_plan.y_to, x_plan.y_step)
    if show_y_labels:
        label_index = labels_sheet.lt_col_index("Site")
        if label_index < 1:
            raise OriginDrawError("Origin dynamic-transfer label column could not be resolved.")
        dataset = f"{labels_sheet.lt_range(False)}!col({label_index})"
        if not layer.obj.LT_execute(
            f"range __dynamic_categories={dataset};axis -ps Y T __dynamic_categories;"
        ):
            raise OriginDrawError("Origin could not bind dynamic-transfer site labels.")
        layer.set_int("y.minorTicks", 0)
        if int(layer.get_int("y.label.type")) != 2:
            raise OriginDrawError("Origin did not keep dynamic-transfer labels as Text from Dataset.")
        if int(layer.get_int("y.label.table")) != 0:
            raise OriginDrawError("Origin retained an inherited dynamic-transfer label table.")
        # Binding a text dataset can normalize a reversed Y increment to a
        # positive value on Origin 2024b. Reapply the confirmed numeric axis
        # contract after the label binding so the readback preserves the
        # top-to-bottom site order.
        layer.axis("y").set_limits(x_plan.y_from, x_plan.y_to, x_plan.y_step)
    else:
        for prop in ("showAxes", "ticks", "minorTicks", "showLabels", "showlabel", "label.show"):
            layer.set_int(f"y.{prop}", 0)
    layer.axis("x").title = x_title
    layer.axis("y").title = "Site" if show_y_labels else ""
    labels: dict[str, Any] = {}
    x_label = layer.label("xb")
    if x_label is None:
        raise OriginDrawError("Origin dynamic-transfer X title object is missing.")
    labels["x_title"] = x_label
    _style_label(x_label, style.axis_title_size_pt)
    x_label.set_int("attach", 1)
    x_label.set_int("font", font_code)
    x_label.text = rf"\b({x_title})"
    if show_y_labels:
        y_label = layer.label("yl")
        if y_label is None:
            raise OriginDrawError("Origin dynamic-transfer Y title object is missing.")
        labels["y_title"] = y_label
        _style_label(y_label, style.axis_title_size_pt)
        y_label.set_int("attach", 1)
        y_label.set_int("font", font_code)
        y_label.text = r"\b(Site)"
    else:
        old = layer.label("yl")
        if old is not None:
            old.set_int("show", 0)
    op.lt_exec("doc -uw;")
    _position_axis_titles_on_page(op, layer, labels)
    op.lt_exec("doc -uw;")
    _apply_axis_label_font(op, layer, ("x", "y"), style)
    return labels


def _style_legend(op: Any, layer: Any, plots: tuple[Any, Any, Any], style: Any) -> Any:
    legend = layer.label("legend")
    if legend is None:
        raise OriginDrawError("Origin dynamic-transfer legend object is missing.")
    indices = {plot.lt_range(): index for index, plot in enumerate(layer.plot_list(), start=1)}
    lines = (
        rf"\L({indices[plots[0].lt_range()]}) Raw mHM",
        rf"\L({indices[plots[1].lt_range()]}) Corrected (improved)",
        rf"\L({indices[plots[2].lt_range()]}) Corrected (degraded)",
    )
    legend.set_int("link", 1)
    legend.text = "\n".join(lines)
    _style_label(legend, style.legend_size_pt, bold=False)
    legend.set_int("font", _origin_font_code(op, style.font_family))
    legend.set_int("color", 1)
    _set_borderless_legend(legend)
    legend.set_int("attach", 1)
    op.lt_exec("doc -uw;")
    page_width = float(op.lt_float("page.width"))
    page_height = float(op.lt_float("page.height"))
    legend.set_float("left", page_width * 0.25)
    legend.set_float("top", page_height * 0.012)
    op.lt_exec("doc -uw;")
    return legend


def _read_axis_state(layer: Any) -> dict[str, Any]:
    state: dict[str, Any] = {}
    for axis_name in ("x", "y"):
        state[axis_name] = {
            "from": float(layer.get_float(f"{axis_name}.from")),
            "to": float(layer.get_float(f"{axis_name}.to")),
            "inc": float(layer.get_float(f"{axis_name}.inc")),
            "type": int(layer.get_int(f"{axis_name}.type")),
            "label_type": int(layer.get_int(f"{axis_name}.label.type")),
            "label_table": int(layer.get_int(f"{axis_name}.label.table")),
        }
    return state


def _build_origin_graph(
    op: Any,
    frame: pd.DataFrame,
    output: RunOutput,
    preparation: ScientificPreparation,
) -> tuple[Any, dict[str, Any]]:
    source_snapshot = frame.copy(deep=True)
    helper_frame, labels_frame, helper_state = _build_helper_frames(frame, preparation)
    style = _figure_style(preparation)
    marker_size_pt = float(preparation.plot_spec.display_plan.marker_size_pt)
    # Resolve all expected OColor values before any LabTalk plot readback. In
    # Origin 2024b, interleaving ``ocolor()`` with ``get rr -c`` can invalidate
    # the temporary range used by later color reads.
    expected_color_codes = {
        RAW_COLOR: float(op.ocolor(RAW_COLOR)),
        IMPROVED_COLOR: float(op.ocolor(IMPROVED_COLOR)),
        DEGRADED_COLOR: float(op.ocolor(DEGRADED_COLOR)),
    }

    source_sheet = op.new_sheet("w", "DYNAMIC TRANSFER Source")
    if source_sheet is None:
        raise OriginDrawError("Origin could not create the dynamic-transfer source worksheet.")
    source_sheet.from_df(frame.copy(deep=True))
    source_sheet.cols_axis()
    helper_sheet = op.new_sheet("w", "DYNAMIC TRANSFER Helpers")
    if helper_sheet is None:
        raise OriginDrawError("Origin could not create the dynamic-transfer helper worksheet.")
    helper_sheet.from_df(helper_frame)
    helper_sheet.cols_axis("xy")
    labels_sheet = op.new_sheet("w", "DYNAMIC TRANSFER Labels")
    if labels_sheet is None:
        raise OriginDrawError("Origin could not create the dynamic-transfer label worksheet.")
    labels_sheet.from_df(labels_frame)
    labels_sheet.cols_axis()

    graph = op.new_graph("DYNAMIC TRANSFER Figure", template="Line")
    if graph is None:
        raise OriginDrawError("Origin could not create the dynamic-transfer graph.")
    graph.set_int("background", op.ocolor("#FFFFFF"))
    page_state = _set_page_size(graph, style)
    first = graph[0]
    second = graph.add_layer(0)
    if second is None or len(graph) != 2:
        raise OriginDrawError("Origin could not create the two dynamic-transfer layers.")
    # The first panel owns the site labels; the second panel keeps its Y frame
    # but hides duplicate labels for a clean shared-axis presentation.
    geometries = (
        {"left_percent": 16.0, "top_percent": style.layer_top_percent, "width_percent": 36.0, "height_percent": style.layer_height_percent},
        {"left_percent": 57.0, "top_percent": style.layer_top_percent, "width_percent": 36.0, "height_percent": style.layer_height_percent},
    )
    layers = (first, second)
    layer_reports: list[dict[str, Any]] = []
    plot_reports: list[dict[str, Any]] = []
    title_labels: dict[str, Any] = {}
    panel_plots: list[tuple[Any, Any, Any, Any, Any]] = []
    plans = (preparation.plot_spec.axis_plan, preparation.plot_spec.secondary_axis_plan)
    panel_prefixes = ("RMSE", "ubRMSE")
    for panel_index, (layer, geometry, plan, prefix) in enumerate(
        zip(layers, geometries, plans, panel_prefixes, strict=True)
    ):
        if plan is None:
            raise OriginDrawError(f"Dynamic-transfer {prefix} axis plan is missing.")
        layer.set_int("unit", 1)
        layer.set_float("left", geometry["left_percent"])
        layer.set_float("top", geometry["top_percent"])
        layer.set_float("width", geometry["width_percent"])
        layer.set_float("height", geometry["height_percent"])
        layer.set_int("fixed", style.layer_fixed)
        layer.set_float("factor", style.layer_factor)
        op.lt_exec("doc -uw;")
        layer_reports.append(
            verify_page_and_layer(
                graph,
                layer,
                origin=op,
                style=style,
                expected_layer=geometry,
            )
        )
        if prefix == "RMSE":
            connector_improve = _plot(op, layer, helper_sheet, y_column="__RMSE_Improve_Y", x_column="__RMSE_Improve_X", plot_type="l", color=CONNECTOR_COLOR, style=style, marker_size_pt=marker_size_pt)
            connector_degraded = _plot(op, layer, helper_sheet, y_column="__RMSE_Degraded_Y", x_column="__RMSE_Degraded_X", plot_type="l", color=DEGRADED_COLOR, style=style, marker_size_pt=marker_size_pt)
            # Origin's symbol interior code 1 is hollow and 2 is filled.
            raw_plot = _plot(op, layer, helper_sheet, y_column="__RMSE_Raw_Y", x_column="__RMSE_Raw_X", plot_type="s", color=RAW_COLOR, style=style, marker_size_pt=marker_size_pt, interior=1)
            improved_plot = _plot(op, layer, helper_sheet, y_column="__RMSE_Improved_Y", x_column="__RMSE_Improved_X", plot_type="s", color=IMPROVED_COLOR, style=style, marker_size_pt=marker_size_pt, interior=2)
            degraded_plot = _plot(op, layer, helper_sheet, y_column="__RMSE_DegradedPoint_Y", x_column="__RMSE_DegradedPoint_X", plot_type="s", color=DEGRADED_COLOR, style=style, marker_size_pt=marker_size_pt, interior=2)
            x_title = preparation.plot_spec.x_title
        else:
            connector_improve = _plot(op, layer, helper_sheet, y_column="__ubRMSE_Improve_Y", x_column="__ubRMSE_Improve_X", plot_type="l", color=CONNECTOR_COLOR, style=style, marker_size_pt=marker_size_pt)
            connector_degraded = _plot(op, layer, helper_sheet, y_column="__ubRMSE_Degraded_Y", x_column="__ubRMSE_Degraded_X", plot_type="l", color=DEGRADED_COLOR, style=style, marker_size_pt=marker_size_pt)
            raw_plot = _plot(op, layer, helper_sheet, y_column="__ubRMSE_Raw_Y", x_column="__ubRMSE_Raw_X", plot_type="s", color=RAW_COLOR, style=style, marker_size_pt=marker_size_pt, interior=1)
            improved_plot = _plot(op, layer, helper_sheet, y_column="__ubRMSE_Improved_Y", x_column="__ubRMSE_Improved_X", plot_type="s", color=IMPROVED_COLOR, style=style, marker_size_pt=marker_size_pt, interior=2)
            degraded_plot = _plot(op, layer, helper_sheet, y_column="__ubRMSE_DegradedPoint_Y", x_column="__ubRMSE_DegradedPoint_X", plot_type="s", color=DEGRADED_COLOR, style=style, marker_size_pt=marker_size_pt, interior=2)
            x_title = preparation.plot_spec.secondary_x_title or "ubRMSE"
        layer.rescale()
        labels = _style_dynamic_axes(
            op,
            layer,
            style=style,
            x_plan=plan,
            labels_sheet=labels_sheet,
            y_count=len(labels_frame),
            x_title=x_title,
            show_y_labels=panel_index == 0,
        )
        if panel_index == 0:
            title_labels.update({f"panel_{name}": label for name, label in labels.items()})
        panel_plots.append((connector_improve, connector_degraded, raw_plot, improved_plot, degraded_plot))
        plot_reports.append(
            {
                "panel": prefix,
                "plot_count": len(layer.plot_list()),
                "connector_improved": connector_improve.lt_range(),
                "connector_degraded": connector_degraded.lt_range(),
                "raw_plot": raw_plot.lt_range(),
                "corrected_improved_plot": improved_plot.lt_range(),
                "corrected_degraded_plot": degraded_plot.lt_range(),
                "colors": {
                    "raw": RAW_COLOR,
                    "improved": IMPROVED_COLOR,
                    "degraded": DEGRADED_COLOR,
                },
                "line_widths": verify_plot_line_widths(
                    op,
                    {
                        "improved connector": connector_improve,
                        "degraded connector": connector_degraded,
                    },
                    style.plot_line_width_pt * 0.72,
                ),
                "symbol_styles": {
                    "raw": verify_symbol_style(
                        op,
                        raw_plot,
                        expected_size_pt=marker_size_pt,
                        expected_edge_percent=50.0,
                        expected_symbol_kind=2,
                        expected_symbol_interior=1,
                    ),
                    "improved": verify_symbol_style(
                        op,
                        improved_plot,
                        expected_size_pt=marker_size_pt,
                        expected_edge_percent=50.0,
                        expected_symbol_kind=2,
                        expected_symbol_interior=2,
                    ),
                    "degraded": verify_symbol_style(
                        op,
                        degraded_plot,
                        expected_size_pt=marker_size_pt,
                        expected_edge_percent=50.0,
                        expected_symbol_kind=2,
                        expected_symbol_interior=2,
                    ),
                },
                "color_readback": {
                    "raw": verify_plot_color(
                        op,
                        raw_plot,
                        RAW_COLOR,
                        variable_name=f"__dynamic_raw_color_{panel_index}",
                        expected_origin_code=expected_color_codes[RAW_COLOR],
                    ),
                    "improved": verify_plot_color(
                        op,
                        improved_plot,
                        IMPROVED_COLOR,
                        variable_name=f"__dynamic_improved_color_{panel_index}",
                        expected_origin_code=expected_color_codes[IMPROVED_COLOR],
                    ),
                    "degraded": verify_plot_color(
                        op,
                        degraded_plot,
                        DEGRADED_COLOR,
                        variable_name=f"__dynamic_degraded_color_{panel_index}",
                        expected_origin_code=expected_color_codes[DEGRADED_COLOR],
                    ),
                },
            }
        )

    legend = _style_legend(op, first, (panel_plots[0][2], panel_plots[0][3], panel_plots[0][4]), style)
    title_labels["legend"] = legend
    op.lt_exec("doc -uw;")
    title_state = verify_text_sizes(
        title_labels,
        {
            "panel_x_title": style.axis_title_size_pt,
            "panel_y_title": style.axis_title_size_pt,
            "legend": style.legend_size_pt,
        },
    )
    title_state.update(verify_text_fonts(op, title_labels, style.font_family))

    # Confirm the exact source frame was not changed while constructing helper
    # datasets.  The helpers live only in the editable Origin workbook.
    try:
        pd.testing.assert_frame_equal(frame, source_snapshot, check_exact=True, check_dtype=True, check_names=True)
    except AssertionError as exc:
        raise OriginDrawError("Dynamic-transfer source data were modified.") from exc

    axis_state = []
    for index, (layer, plan) in enumerate(zip(layers, plans, strict=True)):
        state = _read_axis_state(layer)
        if plan is None:
            raise OriginDrawError("Dynamic-transfer axis plan is missing during readback.")
        expected = {
            "x": (plan.x_from, plan.x_to, plan.x_step),
            "y": (plan.y_from, plan.y_to, plan.y_step),
        }
        for name, values in expected.items():
            if values[0] is None or values[1] is None:
                continue
            actual = state[name]
            if (
                abs(actual["from"] - float(values[0])) > 0.06
                or abs(actual["to"] - float(values[1])) > 0.06
                or (values[2] is not None and abs(actual["inc"] - float(values[2])) > 0.06)
            ):
                raise OriginDrawError(f"Origin dynamic-transfer {name} axis failed readback in panel {index + 1}.")
        if index == 0 and state["y"]["label_type"] != 2:
            raise OriginDrawError("Origin dynamic-transfer Y labels failed dataset readback.")
        axis_state.append(state)

    output.result_opju.unlink(missing_ok=True)
    if not op.save(str(output.result_opju)):
        raise OriginDrawError("Origin did not save dynamic-transfer result.opju")
    require_nonempty(output.result_opju)
    report = {
        **page_state,
        "template_id": preparation.template_id,
        "plot_kind": "dynamic_transfer",
        "plan_digest": preparation.plan_digest,
        "plot_spec": asdict(preparation.plot_spec),
        "source_sha256": preparation.source_sha256,
        "source_columns": list(preparation.source_columns),
        "origin_helper_columns": list(helper_frame.columns),
        "origin_label_helper_columns": list(labels_frame.columns),
        "origin_axis_state": {
            "plot_kind": "dynamic_transfer",
            "layer_count": len(graph),
            "panels": axis_state,
            "category_labels": helper_state,
        },
        "origin_plot_state": {
            "panels": plot_reports,
            "source_sheet": "DYNAMIC TRANSFER Source",
            "helper_sheet": "DYNAMIC TRANSFER Helpers",
            "label_sheet": "DYNAMIC TRANSFER Labels",
            "editable_plot_count": sum(len(layer.plot_list()) for layer in layers),
        },
        "origin_text_state": {
            **title_state,
            "font_family_expected": style.font_family,
            "axis_title_size_pt": style.axis_title_size_pt,
            "tick_label_size_pt": style.tick_label_size_pt,
            "legend_size_pt": style.legend_size_pt,
            "adaptive_profile": style.to_dict(),
            "legend.showframe": int(legend.get_int("showframe")),
        },
        "source_data_modified": False,
        "calculation_performed": False,
    }
    return graph, report


def run_dynamic_transfer_template(
    manifest: TemplateManifest,
    frame: pd.DataFrame,
    output: RunOutput,
    logger: RunLogger,
    *,
    keep_origin_open: bool = True,
    preparation: ScientificPreparation | None = None,
) -> dict[str, Any]:
    """Render and verify the dynamic-transfer route in an isolated Origin."""

    resolved = _resolve_preparation(manifest, frame, output, preparation)
    with OriginSession(keep_open=keep_origin_open) as session:
        op = session.op
        if op is None or session.environment is None:
            raise OriginDrawError("Origin session was not initialized.")
        logger.write(f"Origin connected: version {session.environment.origin_version}")
        graph, verify_report = _build_origin_graph(op, frame, output, resolved)
        exports = export_graph(
            op,
            graph,
            output.result_png,
            output.result_pdf,
            output.result_tif,
        )
        verify_report["exports"] = exports
        write_json(output.origin_verify_report, verify_report)
        write_json(
            output.environment_report,
            {
                "backend": "Origin",
                **session.environment.to_dict(),
            },
        )
        logger.write("Dynamic-transfer Origin graph verified and exported")
    return {
        "opju": str(output.result_opju),
        "png": str(output.result_png),
        "pdf": str(output.result_pdf),
        "tif": str(output.result_tif),
        "verify": verify_report,
    }


__all__ = ["run_dynamic_transfer_template"]
