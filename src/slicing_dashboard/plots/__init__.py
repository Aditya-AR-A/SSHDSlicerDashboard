"""
Plot builder modules for the Slicing Dashboard.

Each chart/component has its own module for maintainability.
Import everything through this package for convenience.
"""

from slicing_dashboard.plots.theme import USER_COLORS, format_seconds, theme_ctx, empty_fig
from slicing_dashboard.plots.kpi_cards import build_kpi_layout
from slicing_dashboard.plots.cumulative_chart import build_cumulative_chart
from slicing_dashboard.plots.individual_chart import build_individual_chart
from slicing_dashboard.plots.pending_chart import build_pending_chart
from slicing_dashboard.plots.assigned_chart import build_assigned_chart
from slicing_dashboard.plots.error_rework_chart import build_error_rework_chart
from slicing_dashboard.plots.legend import build_legend_figure
from slicing_dashboard.plots.tables import create_table

__all__ = [
    "USER_COLORS",
    "format_seconds",
    "theme_ctx",
    "build_kpi_layout",
    "build_cumulative_chart",
    "build_individual_chart",
    "build_pending_chart",
    "build_assigned_chart",
    "build_error_rework_chart",
    "build_legend_figure",
    "create_table",
]
