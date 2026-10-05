"""Rendering interactions must not refresh unrelated dashboard data."""
import ast
import json
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

import dash
import plotly.graph_objects as go
import plotly.io as pio
from plotly.utils import PlotlyJSONEncoder

from slicing_dashboard.plots.theme import chart_template, empty_fig
from slicing_dashboard.plots.kpi_cards import _make_kpi_card


def callback_namespace():
    """Load the real callback without application startup/database access."""
    path = Path(__file__).parents[1] / 'src/slicing_dashboard/app.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in (
            '_refresh_scope', 'update_dashboard', '_dashboard_sources', '_prepare_pending_sources', '_available_user_names',
        ):
            node.decorator_list = [d for d in node.decorator_list if isinstance(d, ast.Name)]
            nodes.append(node)
    namespace = {'dm': MagicMock(), 'available_users': ['Riya', 'Priya']}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    namespace['_render_tab'] = MagicMock(return_value='selected tab')
    return namespace


class TestDashboardPerformance(unittest.TestCase):
    def test_stale_snapshot_does_not_hide_current_mapped_people(self):
        namespace = callback_namespace()
        users = namespace['_available_user_names'](
            {'available_users': ['Riya']}, {'SSHD-Riya': 'Riya', 'SSHD-Gurharleen': 'Gurharleen', 'SSHD-Test': 'Test'},
        )
        self.assertEqual(users, ['Gurharleen', 'Riya'])

    def test_all_users_selection_does_not_filter_out_new_accounts(self):
        namespace = callback_namespace()
        empty = namespace['pd'].DataFrame()
        namespace['_dashboard_sources'] = MagicMock(return_value=({}, {}, empty, empty, empty))
        namespace['build_kpi_layout'] = MagicMock()
        namespace['_build_work_chart'] = MagicMock()
        namespace['_build_status_banner'] = MagicMock()
        namespace['_build_status_badge'] = MagicMock()
        namespace['dm'].get_server_status.return_value = {'is_live': True, 'is_using_snapshot': False}
        with patch('dash.callback_context', MagicMock(triggered=[{'prop_id': 'theme-toggle.n_clicks'}])):
            namespace['update_dashboard'](0, 0, '2026-10-01', '2026-10-05', None, 1, 'tab-today', None, None, ['Riya', 'Priya'])
        self.assertIsNone(namespace['_build_work_chart'].call_args.args[4])

    def test_scan_overlaps_refresh_and_assigned_rows_use_synced_batches(self):
        namespace = callback_namespace()
        manager = namespace['dm']
        manager._refresh_worker.side_effect = lambda function: function
        scan_started, batches_started, kpis_started, batch_synced = Event(), Event(), Event(), Event()

        def prepare(day):
            scan_started.set()
            self.assertTrue(batch_synced.wait(2), 'scan was not overlapped with batch sync')

        def batches(*args, **kwargs):
            batches_started.set()
            self.assertTrue(scan_started.wait(2))
            self.assertTrue(kpis_started.wait(2), 'batch history did not overlap KPI comparisons')
            batch_synced.set()

        def kpis(*args, **kwargs):
            kpis_started.set()
            self.assertTrue(batches_started.wait(2), 'KPI comparisons ran before batch prefetch')
            return 'kpis'

        def ratio(*args, **kwargs):
            self.assertTrue(batch_synced.is_set(), 'ratio used the previous batch ledger')
            return 'ratio'

        def detailed(*args, **kwargs):
            self.assertTrue(batch_synced.is_set(), 'assigned rows used the previous batch ledger')
            return 'detailed'

        manager.prepare_daily_work.side_effect = prepare
        manager.sync_batches_master.side_effect = batches
        manager.get_summary_kpis.side_effect = kpis
        manager.get_batch_rework_ratio_df.side_effect = ratio
        manager.get_detailed_pending_assigned_df.side_effect = detailed
        result = namespace['_dashboard_sources']('2026-10-01', '2026-10-05', True, None)
        self.assertEqual(result[-2:], ('ratio', 'detailed'))

    def test_switching_tabs_only_sends_the_selected_content(self):
        for tab in ('tab-yesterday', 'tab-manage-mapping', 'tab-manage-settlement', 'tab-overview'):
            with self.subTest(tab=tab):
                namespace = callback_namespace()
                context = MagicMock(triggered=[{'prop_id': 'tabs.active_tab'}])
                with patch('dash.callback_context', context):
                    result = namespace['update_dashboard'](
                        0, 0, '2026-10-01', '2026-10-05', None, 0, tab, None, None, ['Riya', 'Priya'],
                    )
                self.assertEqual(result[7], 'selected tab')
                self.assertTrue(all(value is dash.no_update for i, value in enumerate(result) if i != 7))
                manager = namespace['dm']
                manager.fetch_dashboard_data.assert_not_called()
                manager.get_batch_rework_ratio_df.assert_not_called()
                manager.get_detailed_pending_assigned_df.assert_not_called()
                manager.refresh_scope.assert_called_once()

    def test_compact_templates_preserve_used_chart_defaults(self):
        for dark in (True, False):
            with self.subTest(dark=dark):
                original = pio.templates['plotly_dark' if dark else 'plotly_white'].to_plotly_json()
                compact = chart_template(dark).to_plotly_json()
                self.assertEqual(compact['layout'], original['layout'])
                for kind in ('bar', 'scatter'):
                    self.assertEqual(compact['data'][kind], original['data'][kind])
                figure = go.Figure().update_layout(template=chart_template(dark))
                self.assertEqual(set(figure.layout.template.data.to_plotly_json()), {'bar', 'scatter'})
                self.assertLess(len(figure.to_json()), 3500)
        self.assertLess(len(empty_fig().to_json()), 3600)

    def test_kpi_sparkline_does_not_send_unused_plotly_defaults(self):
        card = _make_kpi_card('Approved', '01:00', 'bi bi-check', '10%', '#10B981', True, [1, 2, 3])
        payload = json.dumps(card, cls=PlotlyJSONEncoder)
        self.assertLess(len(payload), 4000)
        self.assertNotIn('histogram2dcontour', payload)


if __name__ == '__main__':
    unittest.main()
