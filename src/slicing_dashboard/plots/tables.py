"""
Styled DataTable builder and tab-content rendering helpers.
"""

import pandas as pd
from dash import dash_table, html, Input, Output, State


def create_table(
    df, is_dark: bool, *, columns=None, table_id=None, page_size=25,
    max_height="320px", tooltip_data=None, tooltip_duration=None,
    duration_columns=(),
):
    """Build a theme-aware Dash DataTable from a DataFrame.

    Parameters
    ----------
    df : pd.DataFrame
    is_dark : bool
    """
    if df.empty:
        return html.Div("No data available")

    font_color = "#f8fafc" if is_dark else "#0f172a"
    header_bg = "#1e293b" if is_dark else "#f1f5f9"
    header_color = "#f8fafc" if is_dark else "#1e293b"
    cell_bg = "rgba(15, 23, 42, 0.7)" if is_dark else "rgba(255, 255, 255, 0.85)"
    odd_bg = "rgba(30, 41, 59, 0.45)" if is_dark else "rgba(241, 245, 249, 0.6)"
    border_col = "rgba(255,255,255,0.08)" if is_dark else "rgba(0,0,0,0.08)"

    options = {"id": table_id} if table_id is not None else {}
    if tooltip_data is not None:
        options.update(tooltip_data=tooltip_data, tooltip_duration=tooltip_duration)
    elif tooltip_duration is not None:
        options["tooltip_duration"] = tooltip_duration
    return dash_table.DataTable(
        **options,
        data=df.astype(object).where(pd.notna(df), None).to_dict("records"),
        columns=columns if columns is not None else [{"name": str(i), "id": str(i)} for i in df.columns],
        sort_action="custom" if duration_columns else "native",
        sort_mode="multi",
        page_action="native",
        page_size=page_size,
        style_header={
            "backgroundColor": header_bg,
            "color": header_color,
            "fontWeight": "700",
            "border": "none",
            "textAlign": "left",
            "fontFamily": "Inter, sans-serif",
            "fontSize": "12px",
            "textTransform": "uppercase",
            "letterSpacing": "0.5px",
            "padding": "10px 12px",
        },
        style_cell={
            "backgroundColor": cell_bg,
            "color": font_color,
            "border": f"1px solid {border_col}",
            "padding": "8px 12px",
            "textAlign": "left",
            "fontFamily": "Inter, sans-serif",
            "fontSize": "12.5px",
        },
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": odd_bg},
            {
                "if": {"filter_query": '{Username} = "All Slicers"'},
                "backgroundColor": (
                    "rgba(59, 130, 246, 0.25)"
                    if is_dark
                    else "rgba(37, 99, 235, 0.15)"
                ),
                "fontWeight": "bold",
            },
            {
                "if": {"filter_query": '{User} = "TOTAL"'},
                "backgroundColor": (
                    "rgba(59, 130, 246, 0.25)"
                    if is_dark
                    else "rgba(37, 99, 235, 0.15)"
                ),
                "fontWeight": "bold",
            },
        ],
        style_table={
            "borderRadius": "10px",
            "overflowX": "auto",
            "maxHeight": max_height,
            "overflowY": "auto",
        },
    )


def sort_duration_rows(rows, sort_by, duration_columns):
    """Sort displayed clock strings by elapsed seconds, including 100+ hours."""
    result = list(rows or [])
    for order in reversed(sort_by or []):
        column = order['column_id']
        def key(row):
            value = row.get(column)
            if column in duration_columns:
                try:
                    parts = [int(part) for part in value.split(':')]
                    return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
                except (AttributeError, ValueError, IndexError):
                    return None
            return value
        available = [row for row in result if key(row) is not None]
        missing = [row for row in result if key(row) is None]
        result = sorted(available, key=key, reverse=order['direction'] == 'desc') + missing
    return result


def register_duration_table_callbacks(app):
    def register(table_id, columns):
        @app.callback(Output(table_id, 'data'), Input(table_id, 'sort_by'), State(table_id, 'data'),
                      prevent_initial_call=True)
        def sort_rows(sort_by, rows):
            return sort_duration_rows(rows, sort_by, columns)
    for table_id in ('daily-today-table', 'daily-yesterday-table'):
        register(table_id, ('Total Duration', 'New Videos (First Time)', 'Same-day Rework', 'Old Rework'))
    register('user-batch-history-table', ('Total Duration',))
