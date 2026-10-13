"""
Return-weighted rework ratio 100% stacked bar chart builder.
"""

import pandas as pd
import plotly.graph_objects as go
from html import escape
from slicing_dashboard.plots.guardrails import safe_chart, checked_frame

from slicing_dashboard.plots.theme import (
    CHART_HEIGHT,
    theme_ctx,
    format_seconds,
)


@safe_chart
def build_rework_ratio_chart(
    ratio_df: pd.DataFrame,
    start_date: str,
    is_dark: bool,
    effective_users: list | None = None,
    is_filtering: bool = False,
) -> go.Figure:
    """Build a 100% stack weighted by returns, with no rework weighted as one.

    Parameters
    ----------
    ratio_df : DataFrame with 'User', rework tier counts and 'Total Batches'.
        Optional '5+ Return Count' preserves exact weights above five returns;
        older data without it uses a multiplier of five for that grouped tier.
    start_date : str used in the title
    is_dark : bool
    effective_users : list of selected users (or None for all)
    is_filtering : bool whether user filtering is active
    """
    t = theme_ctx(is_dark)
    ratio_df = checked_frame(ratio_df, ['User', 'Total Batches'],
        ['Total Batches', 'No Rework', '1 Rework', '2 Reworks', '3 Reworks', '4 Reworks', '5+ Reworks', '5+ Return Count',
         'Reworked Once', 'Reworked Twice+', 'Total Duration (hrs)', 'Avg Batch Duration (hrs)'])
    fig = go.Figure()

    if ratio_df.empty:
        fig.add_annotation(
            text="No data available",
            showarrow=False,
            font={"size": 14, "color": t["font_color"]},
        )
        fig.update_layout(title=f"Return-weighted Batch Reworks (From {start_date})")
    else:
        df = ratio_df.copy()
        if is_filtering and effective_users:
            df = df[df["User"].isin(effective_users)]

        # Exclude any user records with zero submitted batches to avoid misleading 100% no-rework stats
        if "Total Batches" in df.columns:
            df = df[df["Total Batches"] > 0]

        if df.empty:
            fig.add_annotation(
                text="No submitted batch data available",
                showarrow=False,
                font={"size": 14, "color": t["font_color"]},
            )
            fig.update_layout(
                title=f"Return-weighted Batch Reworks (From {start_date})",
                template=t["template"],
                paper_bgcolor=t["bg_color"],
                plot_bgcolor=t["bg_color"],
                font=dict(family="Inter, sans-serif", color=t["font_color"]),
                height=CHART_HEIGHT,
            )
            return fig

        # Sort by Total Batches descending or alphabetical
        df = df.sort_values("User", ascending=True)

        # Keep raw batch totals for tooltips and average durations.
        totals = df["Total Batches"].replace(0, 1)  # avoid div by zero

        # Define 6 tiers: (column_name, dark_color, light_color)
        tiers = [
            ("No Rework", "#10b981", "#059669"),   # Emerald Green
            ("1 Rework",  "#facc15", "#eab308"),   # Vibrant Yellow (clear contrast against green)
            ("2 Reworks", "#f59e0b", "#d97706"),   # Warm Amber
            ("3 Reworks", "#f97316", "#ea580c"),   # Orange
            ("4 Reworks", "#ef4444", "#dc2626"),   # Coral Red
            ("5+ Reworks","#991b1b", "#7f1d1d"),   # Deep Red
        ]

        # Backward compatibility for legacy column names
        if "Reworked Once" in df.columns and "1 Rework" not in df.columns:
            df["1 Rework"] = df["Reworked Once"]
        if "Reworked Twice+" in df.columns and "2 Reworks" not in df.columns:
            df["2 Reworks"] = df["Reworked Twice+"]

        tier_columns = [column for column, _, _ in tiers]
        for column in tier_columns:
            if column not in df:
                df[column] = 0
        if not (df[tier_columns].sum(axis=1) == df['Total Batches']).all():
            raise ValueError('Rework tiers do not match submitted batch totals')
        if df['User'].duplicated().any():
            raise ValueError('Duplicate user batch totals')

        weighted = df[tier_columns].mul([1, 1, 2, 3, 4, 5], axis='columns')
        if '5+ Return Count' in df:
            if ((df['5+ Return Count'] < df['5+ Reworks'] * 5)
                    | ((df['5+ Reworks'] == 0) & (df['5+ Return Count'] != 0))).any():
                raise ValueError('Invalid return count for the 5+ rework tier')
            weighted['5+ Reworks'] = df['5+ Return Count']
        weighted_totals = weighted.sum(axis=1)

        for col, dark_c, light_c in tiers:
            pct = (weighted[col] / weighted_totals) * 100
            batch_pct = (df[col] / totals) * 100
            bar_color = dark_c if is_dark else light_c

            customdata = list(zip(df[col], df['Total Batches'], batch_pct, weighted[col]))
            hovertemplate = f"<b>%{{x}}</b><br>{col}<br>"
            if "Total Duration (hrs)" in df.columns:
                avg_series = df.get("Avg Batch Duration (hrs)", df["Total Duration (hrs)"] / totals)
                customdata = [(*values, duration, average) for values, duration, average in
                              zip(customdata, df['Total Duration (hrs)'], avg_series)]
                duration_index = len(customdata[0])
                customdata = [(*values, format_seconds(duration * 3600))
                              for values, duration in zip(customdata, df['Total Duration (hrs)'])]
                hovertemplate += f"Total: %{{customdata[{duration_index}]}}"
            else:
                hovertemplate += "Total: Unavailable"
            ids = [escape(str(scoped.get(col) or 'Unavailable')) if isinstance(scoped, dict)
                   else 'Unavailable' for scoped in df.get('Tier IDs', [None] * len(df))]
            id_index = len(customdata[0])
            customdata = [(*values, account_ids) for values, account_ids in zip(customdata, ids)]
            hovertemplate += f'<br>IDs: %{{customdata[{id_index}]}}<extra></extra>'

            fig.add_trace(
                go.Bar(
                    name='No recorded rework' if col == 'No Rework' else col,
                    x=df["User"],
                    y=pct,
                    marker=dict(color=bar_color, line=dict(width=1, color="rgba(0,0,0,0.1)")),
                    customdata=customdata,
                    hovertemplate=hovertemplate,
                    text=pct.apply(lambda x: f"{x:.0f}%" if x >= 6 else ""),
                    textposition="inside",
                )
            )

        fig.update_layout(
            barmode="stack",
            title=f"Return-weighted Batch Reworks (From {start_date})",
            yaxis_title="% of Weighted Total",
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
