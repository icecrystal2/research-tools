"""Editable Origin runner for paired or longitudinal trajectories."""

from origin_sciplot.origin_backend.scientific_renderer import run_scientific_template
from origin_sciplot.origin_backend.dynamic_transfer_renderer import run_dynamic_transfer_template


def run(manifest, frame, output, logger, *, keep_origin_open=True, preparation=None):
    if preparation is not None and preparation.plot_spec.plot_kind == "dynamic_transfer":
        return run_dynamic_transfer_template(
            manifest,
            frame,
            output,
            logger,
            keep_origin_open=keep_origin_open,
            preparation=preparation,
        )
    return run_scientific_template(
        manifest,
        frame,
        output,
        logger,
        keep_origin_open=keep_origin_open,
        preparation=preparation,
    )
