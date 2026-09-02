"""
CLI interfacke for the slicing dashboard pipeline.

Provides commands:
- discover: Inspect dashboard structure (Phase 1)
- scrape: Extract data from the dashboard
- process: Clean and normalize raw data
- report: Generate Excel reports (daily/weekly/monthly)
- run: Full pipeline (scrape → process → report)
"""
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path
import click
import pandas as pd
from rich.console import Console
from rich.table import Table
from slicing_dashboard.config import DATA_DIR, PROCESSED_DIR, RAW_DIR, REPORTS_DIR, get_settings
console = Console()


@click.group()
@click.option('--verbose', '-v', is_flag=True, help='Enable debug logging')
def cli(verbose: bool) ->None:
    """Video Slicing Dashboard — Data Extraction & Reporting System."""
    pass
@cli.command()
def discover() ->None:
    """Discover dashboard structure (Phase 1).

    Inspects the login page, dashboard layout, table schema,
    and determines whether HTTP or Selenium extraction is needed.
    """
    from slicing_dashboard.extraction.dashboard import DashboardExtractor
    settings = get_settings()
    console.print('\n[bold blue]Dashboard Discovery[/bold blue]')
    console.print(f'Target: {settings.dashboard_url}')
    console.print()
    extractor = DashboardExtractor(settings)
    try:
        discovery = extractor.discover()
        if discovery.get('errors'):
            console.print('[red]Errors:[/red]')
            for error in discovery['errors']:
                console.print(f'  ❌ {error}')
            console.print()
        login_info = discovery.get('login', {})
        if login_info:
            console.print('[bold]Login Page:[/bold]')
            console.print(f"  URL: {login_info.get('url', 'N/A')}")
            console.print(f"  Title: {login_info.get('title', 'N/A')}")
            forms = login_info.get('forms', [])
            console.print(f'  Forms found: {len(forms)}')
            for i, form in enumerate(forms):
                console.print(
                    f"  Form {i}: action={form.get('action')}, method={form.get('method')}"
                    )
                for inp in form.get('inputs', []):
                    console.print(
                        f"    Input: name={inp.get('name')}, type={inp.get('type')}"
                        )
            console.print()
        dash_info = discovery.get('dashboard', {})
        if dash_info:
            console.print('[bold]Dashboard:[/bold]')
            console.print(f"  SPA: {dash_info.get('is_spa', 'unknown')}")
            console.print(f"  Server: {dash_info.get('server', 'unknown')}")
            console.print()
        table_info = discovery.get('table', {})
        if table_info:
            console.print('[bold]Table:[/bold]')
            for key, value in table_info.items():
                console.print(f'  {key}: {value}')
            console.print()
        console.print(
            f"[bold]Recommended extraction method:[/bold] {discovery.get('extraction_method', 'unknown')}"
            )
        console.print(
            f"[bold]Selenium required:[/bold] {discovery.get('selenium_required', 'unknown')}"
            )
        endpoints = discovery.get('api_endpoints', [])
        if endpoints:
            console.print(
                f'\n[bold]Discovered API endpoints ({len(endpoints)}):[/bold]')
            for ep in endpoints:
                console.print(f'  {ep}')
        console.print('\n[green]Discovery results saved to data/raw/[/green]')
    except Exception as e:
        console.print(f'\n[red]Discovery failed: {e}[/red]')
        sys.exit(1)


@cli.command()
@click.option('--method', type=click.Choice(['http', 'selenium', 'auto']),
    default='auto', help='Extraction method to use')
def scrape(method: str) ->None:
    """Extract data from the dashboard.

    Authenticates, navigates to the slicing table, and extracts all records.
    Raw data is saved to data/raw/YYYY-MM-DD/.
    """
    from slicing_dashboard.extraction.dashboard import DashboardExtractor
    settings = get_settings()
    if not settings.has_credentials:
        console.print('[red]Error: Admin credentials not configured.[/red]')
        console.print('Set ADMIN_USERNAME and ADMIN_PASSWORD in .env')
        sys.exit(1)
    console.print('\n[bold blue]Dashboard Extraction[/bold blue]')
    console.print(f'Target: {settings.dashboard_url}')
    console.print(f'Method: {method}')
    console.print()
    scraper = None
    if method == 'selenium':
        try:
            from slicing_dashboard.scraper.selenium_scraper import SeleniumScraper
            scraper = SeleniumScraper(settings)
        except ImportError:
            console.print(
                "[red]Selenium not installed. Install with: uv pip install 'slicing-dashboard[browser]'[/red]"
                )
            sys.exit(1)
    extractor = DashboardExtractor(settings, scraper=scraper)
    try:
        result = extractor.run()
        if result.success:
            console.print(f'[green]✅ Extraction successful![/green]')
            console.print(f'   Records: {result.total_records}')
            console.print(f'   Pages: {result.pages_extracted}')
            console.print(
                f"   Saved to: data/raw/{datetime.now().strftime('%Y-%m-%d')}/"
                )
        else:
            console.print(f'[red]❌ Extraction failed[/red]')
            for error in result.errors:
                console.print(f'   {error}')
        if result.warnings:
            console.print('\n[yellow]Warnings:[/yellow]')
            for warning in result.warnings:
                console.print(f'   ⚠️ {warning}')
    except Exception as e:
        console.print(f'\n[red]Extraction failed: {e}[/red]')
        sys.exit(1)


@cli.command()
@click.option('--input', '-i', 'input_path', type=click.Path(exists=True),
    help=
    'Path to raw data file (CSV or JSON). Defaults to latest in data/raw/.')
@click.option('--output', '-o', 'output_path', type=click.Path(), help=
    'Output path for processed data. Defaults to data/processed/slicing_master.csv.'
    )
def process(input_path: (str | None), output_path: (str | None)) ->None:
    """Process raw extracted data.

    Applies cleaning, normalization, deduplication, and validation.
    """
    from slicing_dashboard.processing.cleaning import clean_dataframe
    from slicing_dashboard.processing.deduplication import deduplicate
    from slicing_dashboard.processing.normalization import normalize_dataframe
    from slicing_dashboard.processing.validation import validate_data_quality, validate_extraction
    console.print('\n[bold blue]Data Processing[/bold blue]\n')
    if input_path:
        raw_path = Path(input_path)
    else:
        raw_path = _find_latest_raw_file()
        if raw_path is None:
            console.print(
                "[red]No raw data found in data/raw/. Run 'scrape' first.[/red]"
                )
            sys.exit(1)
    console.print(f'Input: {raw_path}')
    if raw_path.suffix == '.json':
        with open(raw_path) as f:
            data = json.load(f)
        records = data.get('records', data) if isinstance(data, dict) else data
        df = pd.DataFrame(records)
    else:
        df = pd.read_csv(raw_path)
    console.print(f'Loaded {len(df)} records, {len(df.columns)} columns')
    requests_list = []
    requests_files = sorted(list(RAW_DIR.rglob('requests_*.json')))
    for requests_file in requests_files:
        try:
            with open(requests_file) as rf:
                req_data = json.load(rf)
            requests_list.extend(req_data.get('records', []))
        except Exception as e:
            console.print(
                f'[yellow]⚠️ Failed to load requests from {requests_file.name}: {e}[/yellow]'
                )
    if requests_list:
        requests_dict = {r.get('id'): r for r in requests_list if r.get(
            'id') is not None}
        requests_list = list(requests_dict.values())
    console.print(f'Loaded {len(requests_list)} unique requests historically')
    reviewed_tasks_list = []
    reviewed_files = sorted(list(RAW_DIR.rglob('reviewed_tasks_*.json')))
    for reviewed_file in reviewed_files:
        try:
            with open(reviewed_file) as rvf:
                rev_data = json.load(rvf)
            reviewed_tasks_list.extend(rev_data.get('records', []))
        except Exception as e:
            console.print(
                f'[yellow]⚠️ Failed to load reviewed tasks from {reviewed_file.name}: {e}[/yellow]'
                )
    if reviewed_tasks_list:
        unique_revs = {json.dumps(r, sort_keys=True): r for r in
            reviewed_tasks_list}
        reviewed_tasks_list = list(unique_revs.values())
    console.print(
        f'Loaded {len(reviewed_tasks_list)} unique reviewed tasks historically'
        )
    ext_validation = validate_extraction(df)
    if not ext_validation.is_valid:
        console.print('[red]Extraction validation failed:[/red]')
        for error in ext_validation.errors:
            console.print(f'  ❌ {error}')
        sys.exit(1)
    df, clean_warnings = clean_dataframe(df)
    console.print(f'After cleaning: {len(df)} records')
    for w in clean_warnings:
        console.print(f'  ⚠️ {w}')
    settings = get_settings()
    df, norm_warnings = normalize_dataframe(df, timezone=settings.timezone,
        requests=requests_list)
    console.print(f'After normalization: {len(df)} records')
    for w in norm_warnings:
        console.print(f'  ⚠️ {w}')
    df, removed, dedup_warnings = deduplicate(df)
    console.print(f'After deduplication: {len(df)} records ({removed} removed)'
        )
    for w in dedup_warnings:
        console.print(f'  ⚠️ {w}')
    quality_validation = validate_data_quality(df)
    if quality_validation.errors:
        console.print('\n[red]Data quality issues:[/red]')
        for error in quality_validation.errors:
            console.print(f'  ❌ {error}')
    if quality_validation.warnings:
        console.print('\n[yellow]Data quality warnings:[/yellow]')
        for w in quality_validation.warnings:
            console.print(f'  ⚠️ {w}')
    out_path = Path(output_path
        ) if output_path else PROCESSED_DIR / 'slicing_master.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    console.print(
        '[yellow]Processed data CSV writing disabled in serverless mode.[/yellow]'
        )
    from slicing_dashboard.processing.transitions import build_transitions
    transitions_df = build_transitions(df, reviewed_tasks_list)
    transitions_out_path = out_path.parent / 'transitions_master.csv'
    console.print(
        '[yellow]Transitions master CSV writing disabled in serverless mode.[/yellow]'
        )
    from slicing_dashboard.db import DatabaseManager
    db_manager = DatabaseManager()
    if db_manager.is_connected():
        console.print('[cyan]Pushing data to PostgreSQL database...[/cyan]')
        db_manager.upsert_slicing_master(df)
        db_manager.upsert_slicing_history_snapshot(df)
        db_manager.upsert_transitions_master(transitions_df)
        console.print('[green]✅ Database sync complete.[/green]')
    if 'user_name' in df.columns and 'is_completed' in df.columns:
        from slicing_dashboard.extraction.parsers import format_duration
        comp_df = df[(df['is_completed'] == True) & (df['completed_date'] >=
            '2026-07-07')].copy()
        if not comp_df.empty:
            users = comp_df['user_name'].dropna().unique()
            user_rows = []
            for user in users:
                user_df = comp_df[comp_df['user_name'] == user]
                normal_tasks = user_df[user_df['completion_type'] == 'normal']
                error_tasks = user_df[user_df['completion_type'] == 'error']
                n_count = len(normal_tasks)
                n_dur = normal_tasks['duration_seconds'].sum()
                e_count = len(error_tasks)
                e_dur = error_tasks['duration_seconds'].sum()
                t_count = n_count + e_count
                t_dur = n_dur + e_dur
                user_rows.append({'User': user, 'Normal Count': n_count,
                    'Normal Duration': format_duration(n_dur),
                    'Error Count': e_count, 'Error Duration':
                    format_duration(e_dur), 'Total Count': t_count,
                    'Total Duration': format_duration(t_dur),
                    'Total Seconds': t_dur})
            summary = pd.DataFrame(user_rows).sort_values('Total Seconds',
                ascending=False)
            table = Table(title='User Completion Summary (since 2026-07-07)')
            table.add_column('User', style='cyan')
            table.add_column('Normal Tasks', justify='right')
            table.add_column('Normal Duration', justify='right', style='green')
            table.add_column('Error Tasks', justify='right')
            table.add_column('Error Duration', justify='right', style='green')
            table.add_column('Total Tasks', justify='right', style='bold')
            table.add_column('Total Duration', justify='right', style=
                'bold green')
            for _, row in summary.iterrows():
                table.add_row(row['User'], str(row['Normal Count']), row[
                    'Normal Duration'], str(row['Error Count']), row[
                    'Error Duration'], str(row['Total Count']), row[
                    'Total Duration'])
            tot_n_count = summary['Normal Count'].sum()
            tot_n_dur = format_duration(comp_df[comp_df['completion_type'] ==
                'normal']['duration_seconds'].sum())
            tot_e_count = summary['Error Count'].sum()
            tot_e_dur = format_duration(comp_df[comp_df['completion_type'] ==
                'error']['duration_seconds'].sum())
            tot_t_count = summary['Total Count'].sum()
            tot_t_dur = format_duration(comp_df['duration_seconds'].sum())
            table.add_row('TOTAL', str(tot_n_count), tot_n_dur, str(
                tot_e_count), tot_e_dur, str(tot_t_count), tot_t_dur, style
                ='bold')
            console.print()
            console.print(table)
        else:
            console.print(
                '[yellow]No completed tasks found since 2026-07-07.[/yellow]')


@cli.command()
@click.option('--daily', is_flag=True, help='Generate daily report')
@click.option('--weekly', is_flag=True, help='Generate weekly report')
@click.option('--monthly', is_flag=True, help='Generate monthly report')
@click.option('--date', type=str, help='Target date (YYYY-MM-DD)')
@click.option('--week', type=str, help='Target week (YYYY-Www)')
@click.option('--month', type=str, help='Target month (YYYY-MM)')
@click.option('--start-date', type=str, help=
    'Start date for range generation (YYYY-MM-DD)')
@click.option('--end-date', type=str, help=
    'End date for range generation (YYYY-MM-DD)')
@click.option('--input', '-i', 'input_path', type=click.Path(exists=True),
    help='Path to processed data file')
def report(daily: bool, weekly: bool, monthly: bool, date: (str | None),
    week: (str | None), month: (str | None), start_date: (str | None),
    end_date: (str | None), input_path: (str | None)) ->None:
    """Generate Excel reports from processed data."""
    from slicing_dashboard.reporting.daily import generate_daily_report
    from slicing_dashboard.reporting.excel import write_daily_report, write_monthly_report, write_weekly_report
    from slicing_dashboard.reporting.monthly import generate_monthly_report
    from slicing_dashboard.reporting.weekly import generate_weekly_report
    if not any([daily, weekly, monthly]):
        console.print(
            '[yellow]No report type specified. Use --daily, --weekly, or --monthly.[/yellow]'
            )
        sys.exit(1)
    if input_path:
        data_path = Path(input_path)
    else:
        data_path = PROCESSED_DIR / 'slicing_master.csv'
    if not data_path.exists():
        console.print(f'[red]Processed data not found: {data_path}[/red]')
        console.print("Run 'process' first.")
        sys.exit(1)
    df = pd.read_csv(data_path)
    console.print(f'\n[bold blue]Report Generation[/bold blue]')
    console.print(f'Data: {data_path} ({len(df)} records)\n')
    if daily:
        if start_date:
            from datetime import timedelta
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_val = end_date if end_date else datetime.now().strftime(
                '%Y-%m-%d')
            end_dt = datetime.strptime(end_val, '%Y-%m-%d')
            curr_dt = start_dt
            generated_count = 0
            while curr_dt <= end_dt:
                curr_str = curr_dt.strftime('%Y-%m-%d')
                report_data = generate_daily_report(df, date=curr_str)
                filepath = write_daily_report(report_data)
                console.print(
                    f'[green]✅ Daily report generated for {curr_str}: {filepath}[/green]'
                    )
                curr_dt += timedelta(days=1)
                generated_count += 1
            console.print(
                f"""
[green]Generated {generated_count} daily reports from {start_date} to {end_val}.[/green]"""
                )
        else:
            report_data = generate_daily_report(df, date=date)
            filepath = write_daily_report(report_data)
            console.print(f'[green]✅ Daily report: {filepath}[/green]')
    if weekly:
        report_data = generate_weekly_report(df, week=week)
        filepath = write_weekly_report(report_data)
        console.print(f'[green]✅ Weekly report: {filepath}[/green]')
    if monthly:
        report_data = generate_monthly_report(df, month=month)
        filepath = write_monthly_report(report_data)
        console.print(f'[green]✅ Monthly report: {filepath}[/green]')


@cli.command()
@click.option('--method', type=click.Choice(['http', 'selenium', 'auto']),
    default='auto')
@click.option('--daily', is_flag=True, default=True)
@click.option('--weekly', is_flag=True)
@click.option('--monthly', is_flag=True)
def run(method: str, daily: bool, weekly: bool, monthly: bool) ->None:
    """Run the full pipeline: scrape → process → report."""
    console.print('\n[bold blue]Full Pipeline Execution[/bold blue]\n')
    console.print('[bold]Step 1: Extraction[/bold]')
    ctx = click.Context(scrape)
    ctx.invoke(scrape, method=method)
    console.print('\n[bold]Step 2: Processing[/bold]')
    ctx = click.Context(process)
    ctx.invoke(process, input_path=None, output_path=None)
    console.print('\n[bold]Step 3: Reporting[/bold]')
    ctx = click.Context(report)
    ctx.invoke(report, daily=daily, weekly=weekly, monthly=monthly, date=
        None, week=None, month=None, input_path=None)
    console.print('\n[green]✅ Pipeline complete![/green]')


def _find_latest_raw_file() ->(Path | None):
    """Find the most recent raw data file."""
    if not RAW_DIR.exists():
        return None
    dated_dirs = sorted([d for d in RAW_DIR.iterdir() if d.is_dir() and d.
        name[:4].isdigit()], reverse=True)
    for dated_dir in dated_dirs:
        for ext in ['.json', '.csv']:
            files = sorted(dated_dir.glob(f'slicing*{ext}'), reverse=True)
            if files:
                return files[0]
    for ext in ['.json', '.csv']:
        files = sorted(RAW_DIR.glob(f'*{ext}'), reverse=True)
        if files:
            return files[0]
    return None


if __name__ == '__main__':
    cli()
