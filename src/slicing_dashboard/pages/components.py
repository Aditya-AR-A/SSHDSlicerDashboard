"""Small shared layout helpers that preserve the existing dashboard."""
from dash import dcc, html
import dash_bootstrap_components as dbc


def report_section(title, children, description=None):
    heading = [html.H2(title, className="report-section-heading")]
    if description:
        heading.append(html.P(description, className="text-secondary small mb-3"))
    body = list(children) if isinstance(children, (list, tuple)) else [children]
    return html.Section([html.Div(heading, className="report-section-heading"), *body], className="glass-panel report-section")


def add_reporting_shell(existing_layout, today):
    """Keep dashboard/editor nodes mounted while switching visible pages."""
    from slicing_dashboard.pages.daily_report import layout_daily_report
    from slicing_dashboard.pages.user_report import layout_user_report

    container = existing_layout.children[0]
    nodes = list(container.children)
    header = nodes[0]
    header.children[1].id = "dashboard-period-controls"
    header.children[2].id = "dashboard-date-controls"
    shared = [node for node in nodes[2:] if isinstance(node, (dcc.Store, dcc.Interval))]
    dashboard = [node for node in nodes[2:] if not isinstance(node, (dcc.Store, dcc.Interval))]
    container.children = [
        dcc.Location(id="report-location", refresh=False),
        header,
        html.Nav(dbc.Nav([
            dbc.NavLink("Dashboard", href="/", id="nav-dashboard", active=True),
            dbc.NavLink("Daily Report", href="/reports/daily", id="nav-daily-report", active=False),
            dbc.NavLink("User Report", href="/reports/user", id="nav-user-report", active=False),
        ], pills=True, className="report-nav mb-3"), **{"aria-label": "Reports navigation"}),
        nodes[1],
        *shared,
        html.Div(dashboard, id="dashboard-page"),
        html.Div(layout_daily_report(today), id="daily-report-page", style={"display": "none"}),
        html.Div(layout_user_report(today), id="user-report-page", style={"display": "none"}),
        html.Div([
            html.H1("Page not found", className="h4"),
            html.P("Choose Dashboard, Daily Report, or User Report from the navigation."),
        ], id="report-not-found", className="glass-panel report-section", style={"display": "none"}),
    ]
    return existing_layout
