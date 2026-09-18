"""
Shared theme constants, color maps, and formatting utilities.

Used by all plot modules to ensure visual consistency.
"""

import plotly.graph_objects as go
import pandas as pd

# ── Consistent user color palette ────────────────────────────────────────────
USER_COLORS = {
    "Aditya": "#636EFA",
    "Gurharleen": "#478754",
    "Komal": "#EF553B",
    "Priya": "#00CC96",
    "Rajni": "#AB63FA",
    "Ranjeeta": "#FFA15A",
    "Riya": "#19D3F3",
    "Sanddep": "#FF6692",
    "Admin": "#B6E880",
    "Test": "#FF97FF",
    "Dep": "#FECB52",
}

# ── Compact chart height (px) used by all graphs ─────────────────────────────
CHART_HEIGHT = 280


def empty_fig() -> go.Figure:
    """Return a dark, transparent empty figure — used as initial placeholder
    so the browser doesn't flash white default plotly charts while data loads."""
    fig = go.Figure()
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        height=CHART_HEIGHT,
        margin=dict(l=0, r=0, t=0, b=0),
    )
    return fig


def format_seconds(seconds):
    """Convert a duration in seconds to HH:MM string."""
    if pd.isna(seconds) or seconds is None:
        return "00:00"
    seconds = int(seconds)
    h = seconds // 3600
    m = seconds % 3600 // 60
    return f"{h:02d}:{m:02d}"


def theme_ctx(is_dark: bool) -> dict:
    """Return a dict of theme-aware Plotly styling tokens.

    Returns
    -------
    dict with keys: template, font_color, bg_color, hover_bg, hover_fg
    """
    return {
        "template": "plotly_dark" if is_dark else "plotly_white",
        "font_color": "#e0e0e0" if is_dark else "#333333",
        "bg_color": "rgba(0,0,0,0)",
        "hover_bg": "#2a2a35" if is_dark else "#f0f0f0",
        "hover_fg": "#ffffff" if is_dark else "#111111",
    }
