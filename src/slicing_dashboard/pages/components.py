"""Small shared layout helpers that preserve the existing dashboard."""
from dash import dcc, html
import dash_bootstrap_components as dbc


def chart_graph(**kwargs):
    """A measurable chart surface that keeps page scrolling and loading space stable."""
    figure = kwargs.get('figure')
    height = getattr(getattr(figure, 'layout', None), 'height', None) or 340
    style = {'height': f'{height}px', 'width': '100%', 'minWidth': 0, **kwargs.pop('style', {})}
    if style.get('height') in ('auto', '100%'):
        style['height'] = f'{height}px'
    classes = kwargs.pop('className', '')
    panel = 'glass-panel' in classes
    # Put borders/padding on the wrapper, not inside Plotly's measured surface.
    classes = ' '.join(value for value in classes.split() if value not in ('glass-panel', 'p-1', 'rounded', 'shadow-sm', 'chart-compact'))
    kwargs.setdefault('responsive', True)
    kwargs['config'] = {**(kwargs.pop('config', None) or {}), 'scrollZoom': False}
    graph = dcc.Graph(**kwargs, style=style, className='chart-surface ' + classes)
    return html.Div(html.Div(graph, className='chart-viewport'),
                    className='chart-frame' + (' glass-panel' if panel else ''))


def contain_graphs(node):
    """Upgrade existing static layout without changing callback IDs or grid structure."""
    if isinstance(node, dcc.Graph):
        return chart_graph(**node.to_plotly_json()['props'])
    if isinstance(node, (list, tuple)):
        return [contain_graphs(child) for child in node]
    if hasattr(node, 'children'):
        node.children = contain_graphs(node.children)
    return node


def report_section(title, children, description=None):
    heading = [html.H2(title)]
    if description:
        heading.append(html.P(description, className="text-secondary small mb-3"))
    body = list(children) if isinstance(children, (list, tuple)) else [children]
    return html.Section([html.Div(heading, className="report-section-heading"), *body], className="glass-panel report-section")


def add_reporting_shell(existing_layout, today, users=()):
    """Keep dashboard/editor nodes mounted while switching visible pages."""
    from slicing_dashboard.pages.daily_report import layout_daily_report
    from slicing_dashboard.pages.user_report import layout_user_report
    from slicing_dashboard.pages.workflow_history import layout_workflow_history
    from slicing_dashboard.pages.settings import layout_settings

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
            dbc.NavLink('Workflow History', href='/workflow', id='nav-workflow', active=False),
            dbc.NavLink('Settings', href='/settings', id='nav-settings', active=False),
            dbc.Button([html.I(className='bi bi-bell me-2'), 'Notifications ',
                        dbc.Badge('0', id='workflow-unread', color='primary', className='ms-1')],
                       id='workflow-bell', n_clicks=0, color='secondary', outline=True,
                       title='Open shared team notifications'),
        ], pills=True, className="report-nav mb-3"), **{"aria-label": "Reports navigation"}),
        nodes[1],
        *shared,
        dcc.Store(id='workflow-sync-store'),
        dcc.Store(id='workflow-read-change'),
        dcc.Store(id='workflow-notice-seen', storage_type='session'),
        dcc.Interval(id='workflow-notification-interval', interval=60 * 1000, n_intervals=0),
        html.Div(id='workflow-toast-container', className='workflow-toast-container',
                 **{'aria-live': 'polite', 'aria-atomic': 'false'}),
        dbc.Modal([dbc.ModalHeader(dbc.ModalTitle('Shared Team Notifications')),
                   dbc.ModalBody([
                       dbc.Button('Mark all read', id='workflow-mark-all-read', n_clicks=0, size='sm',
                                  color='secondary', outline=True, disabled=True, className='mb-3'),
                       html.Div(id='workflow-notification-content'),
                   ])],
                  id='workflow-notification-modal', is_open=False, size='lg', scrollable=True),
        html.Div(dashboard, id="dashboard-page"),
        html.Div(layout_daily_report(today), id="daily-report-page", style={"display": "none"}),
        html.Div(layout_user_report(today, users), id="user-report-page", style={"display": "none"}),
        html.Div(layout_workflow_history(today), id='workflow-page', style={'display': 'none'}),
        html.Div(layout_settings(), id='settings-page', style={'display': 'none'}),
        html.Div([
            html.H1("Page not found", className="h4"),
            html.P("Choose Dashboard, Daily Report, or User Report from the navigation."),
        ], id="report-not-found", className="glass-panel report-section", style={"display": "none"}),
    ]
    return existing_layout
