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

    font_color = "#e0e0e0" if is_dark else "#333333"
    header_bg = (
        "rgba(40, 40, 40, 0.9)" if is_dark else "rgba(230, 230, 230, 0.9)"
    )
    cell_bg = (
        "rgba(20, 20, 20, 0.5)" if is_dark else "rgba(255, 255, 255, 0.5)"
    )
    odd_bg = (
        "rgba(30, 30, 30, 0.5)" if is_dark else "rgba(240, 240, 240, 0.5)"
    )
    border_col = (
        "rgba(255,255,255,0.05)" if is_dark else "rgba(0,0,0,0.05)"
    )

    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[{"name": str(i), "id": str(i)} for i in df.columns],
        style_header={
            "backgroundColor": header_bg,
            "color": font_color,
            "fontWeight": "bold",
            "border": "none",
            "textAlign": "left",
            "fontFamily": "Inter, sans-serif",
            "fontSize": "13px",
        },
        style_cell={
            "backgroundColor": cell_bg,
            "color": font_color,
            "border": f"1px solid {border_col}",
            "padding": "8px 10px",
            "textAlign": "left",
            "fontFamily": "Inter, sans-serif",
            "fontSize": "12px",
        },
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": odd_bg},
            {
                "if": {"filter_query": '{Username} = "All Slicers"'},
                "backgroundColor": (
                    "rgba(45, 125, 246, 0.25)"
                    if is_dark
                    else "rgba(45, 125, 246, 0.15)"
                ),
                "fontWeight": "bold",
            },
            {
                "if": {"filter_query": '{User} = "TOTAL"'},
                "backgroundColor": (
                    "rgba(45, 125, 246, 0.25)"
                    if is_dark
                    else "rgba(45, 125, 246, 0.15)"
                ),
                "fontWeight": "bold",
            },
        ],
        style_table={
            "borderRadius": "8px",
            "overflow": "hidden",
            "maxHeight": "220px",
            "overflowY": "auto",
        },
    )
