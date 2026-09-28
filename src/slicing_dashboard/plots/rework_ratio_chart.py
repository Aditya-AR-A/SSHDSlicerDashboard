"""
Rework ratio 100% stacked bar chart builder.
"""

import pandas as pd
import plotly.graph_objects as go

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    theme_ctx,
)


def build_rework_ratio_chart(
    ratio_df: pd.DataFrame,
    start_date: str,
    is_dark: bool,
    effective_users: list | None = None,
    is_filtering: bool = False,
) -> go.Figure:
    """Build the 100% stacked bar chart for batch rework ratio.

    Parameters
    ----------
    ratio_df : DataFrame with 'User', 'No Rework', 'Reworked Once', 'Reworked Twice+', 'Total Batches'
    start_date : str used in the title
    is_dark : bool
    effective_users : list of selected users (or None for all)
    is_filtering : bool whether user filtering is active
    """
    t = theme_ctx(is_dark)
    fig = go.Figure()

    if ratio_df.empty:
        fig.add_annotation(
            text="No data available",
            showarrow=False,
            font={"size": 14, "color": t["font_color"]},
        )
        fig.update_layout(title=f"Batch Rework Ratio (From {start_date})")
    else:
        df = ratio_df.copy()
        if is_filtering and effective_users:
            df = df[df["User"].isin(effective_users)]
            
        # Sort by Total Batches descending or alphabetical
        df = df.sort_values("User", ascending=True)

        # Calculate percentages
        totals = df["Total Batches"].replace(0, 1)  # avoid div by zero

        # Define 6 tiers: (column_name, dark_color, light_color)
        tiers = [
            ("No Rework", "#10b981", "#059669"),   # Emerald
            ("1 Rework",  "#84cc16", "#65a30d"),   # Lime
            ("2 Reworks", "#eab308", "#ca8a04"),   # Amber
            ("3 Reworks", "#f97316", "#ea580c"),   # Orange
            ("4 Reworks", "#ef4444", "#dc2626"),   # Coral Red
            ("5+ Reworks","#991b1b", "#7f1d1d"),   # Deep Red
        ]

        # Backward compatibility for legacy column names
        if "Reworked Once" in df.columns and "1 Rework" not in df.columns:
            df["1 Rework"] = df["Reworked Once"]
        if "Reworked Twice+" in df.columns and "2 Reworks" not in df.columns:
            df["2 Reworks"] = df["Reworked Twice+"]

        for col, dark_c, light_c in tiers:
            if col not in df.columns:
                df[col] = 0

            pct = (df[col] / totals) * 100
            bar_color = dark_c if is_dark else light_c

            fig.add_trace(
                go.Bar(
                    name=col,
                    x=df["User"],
                    y=pct,
                    marker=dict(color=bar_color, line=dict(width=1, color="rgba(0,0,0,0.1)")),
                    customdata=list(zip(df[col], df["Total Batches"])),
                    hovertemplate=(
                        f"<b>%{{x}}</b><br>"
                        f"{col}: %{{y:.1f}}%<br>"
                        f"(%{{customdata[0]}} / %{{customdata[1]}} batches)<extra></extra>"
                    ),
                    text=pct.apply(lambda x: f"{x:.0f}%" if x >= 6 else ""),
                    textposition="inside",
                )
            )

        fig.update_layout(
            barmode="stack",
            title=f"Batch Rework Ratio (From {start_date})",
            yaxis_title="% of Batches",
            showlegend=True,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
                font=dict(size=11, color=t["font_color"]),
                bgcolor="rgba(0,0,0,0)",
            ),
        )

    fig.update_layout(
        template=t["template"],
        plot_bgcolor=t["bg_color"],
        paper_bgcolor=t["bg_color"],
        font=dict(family="Inter, sans-serif", size=12, color=t["font_color"]),
        title_font=dict(size=15, weight="bold"),
        height=CHART_HEIGHT,
        margin=dict(l=40, r=15, t=45, b=35),
        bargap=0.35,
        hoverlabel=dict(bgcolor=t["hover_bg"], font_color=t["hover_fg"], font_size=13),
        xaxis=dict(showgrid=False, zeroline=False, automargin=True, tickfont=dict(size=11, weight="bold")),
        yaxis=dict(
            range=[0, 100],
            showgrid=True,
            gridcolor="rgba(128,128,128,0.2)",
            zeroline=False,
            automargin=True,
            tickfont=dict(size=11),
            ticksuffix="%",
        ),
    )
    
    # Improve text visibility
    fig.update_traces(
        textfont=dict(size=10, weight="bold", color="white"),
        constraintext="none",
    )
    
    return fig
