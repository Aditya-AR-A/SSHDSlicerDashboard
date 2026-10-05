"""
Styled DataTable builder and tab-content rendering helpers.
"""

import pandas as pd
from dash import dash_table, html


def create_table(df, is_dark: bool):
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

    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": str(i), "id": str(i)} for i in df.columns],
        sort_action="native",
        page_action="native",
        page_size=25,
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
            "overflow": "hidden",
            "maxHeight": "320px",
            "overflowY": "auto",
        },
    )
