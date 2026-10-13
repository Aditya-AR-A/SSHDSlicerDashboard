"""Settings editors stay mounted while users switch tabs or pages."""
from dash import html
import dash_bootstrap_components as dbc
from slicing_dashboard.management.ui import layout_settlement_management, layout_user_mapping


def layout_settings():
    return html.Div([
        html.Header([html.H1('Settings', className='h3'),
                     html.P('Manage settlement periods and account mappings.', className='text-secondary')]),
        dbc.Tabs([dbc.Tab(label='Settlement periods', tab_id='settings-settlement'),
                  dbc.Tab(label='User mappings', tab_id='settings-mapping')],
                 id='settings-tabs', active_tab='settings-settlement', className='mb-3'),
        html.Div(layout_settlement_management(), id='settings-settlement-panel'),
        html.Div(layout_user_mapping(), id='settings-mapping-panel', style={'display': 'none'}),
    ], className='report-page settings-page')
