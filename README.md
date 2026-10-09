# SSHDSlicerDashBoard

Video Slicing Dashboard — a scraping, data processing, and reporting system built with Dash.

## Features

- **Interactive Dashboard** — Plotly Dash-based web UI with Bootstrap styling
- **Data Scraping** — HTTP-based scraper for extracting slicing data
- **Data Processing** — Cleaning, normalization, and aggregation pipelines
- **Reporting** — Automated weekly report generation
- **Database** — PostgreSQL-backed storage via SQLAlchemy
- **CLI** — Command-line interface for data operations

## Tech Stack

- Python 3.12+
- Dash / Plotly / Dash Bootstrap Components
- Pandas / NumPy
- SQLAlchemy + PostgreSQL (psycopg2)
- Pydantic / Pydantic Settings
- httpx / BeautifulSoup / lxml

## Setup

```bash
# Clone the repo
git clone https://github.com/adityasshd/SSHDSlicerDashBoard.git
cd SSHDSlicerDashBoard

# Create virtual environment and install dependencies
uv sync

# Copy environment config
cp .env.example .env
# Edit .env with your database credentials and API keys

# Run the dashboard
PYTHONPATH=src uv run python src/slicing_dashboard/app.py
```

On Windows, the local runner keeps debug tools enabled but disables automatic process/browser reloading to avoid Werkzeug's `WinError 10038` socket failure during reload. Restart the server after Python changes, then refresh the browser. In PowerShell, start it with `uv run python -m slicing_dashboard.app`.

## Workflow history and shared inbox

Open `/workflow` for persisted batch actions, return cycles and explicitly labeled synthetic approval prerequisites. A completed batch implies Leader, Auditor and Admin approval under the configured business rule; missing review times and reviewer identities remain unknown. New source evidence supersedes synthetic entries while retaining projection history.

The navigation bell shows the latest ten sampling/rework notices, with a full unread count and persistent shared read state. Its source is `/api/requests`; upstream request status is independent of local read/unread. Source evidence and inbox state use configured MongoDB/PostgreSQL, with SQLite for local installations without a configured database. Collection occurs during application refreshes.

To resume a bounded history scan:

```bash
uv run slicing-dashboard workflow-sync --backfill --max-rounds 5
```

Remaining cursors resume on later refreshes; available API history may be incomplete. Synthetic milestones are excluded from daily approval hours because their actual event times are unknown. See [workflow policy and implementation](docs/batch-workflow-plan.md).

The dashboard trend is an area chart. Dated Leader/Auditor/Admin batch approvals use batch video duration at capture as an explicitly labeled estimate; task-level review logs and undated synthetic milestones do not add hours. The current assigned/rework chart reads fully paginated task-status queues, independently of settlement dates and historical batch totals. Queue captures expire after 60 seconds and manual/automatic refresh bypasses them. A failed queue read displays an unavailable/incomplete message.

Assignment dates and age colors use the original timestamp, exact batch/legacy-return links, or an account's unambiguous active batch date. Account-level dates are labeled in the hover; ambiguous dates remain Unknown. Batch totals never create extra assigned hours.

Open `/settings` for Settlement periods and User mappings. Both editors retain unsaved changes across tab, theme and page changes. The dashboard removes the bottom Slice Data Overview and Settlement Overview tables and management tabs.

The inbox also records Admin batch approvals/completions and batch returns by any reviewer. Completion without a dated Admin action is explicitly labeled inferred. New recent events show in-app pop-ups; history imports do not replay as pop-ups. Notifications poll every minute while the application is open, retain their read state, and offer individual history links and Mark all read.

Daily Report has redesigned output/composition cards, grouped charts, and a per-person comparison with the previous day. User Report includes a 90-day activity calendar: grey means no recorded capture, while recorded zero output has its own color. Both additions use retained evidence without extra API calls.

Total daily work is **new work + same-day rework**. Old rework remains visible separately and is excluded from work totals, day comparisons, daily trends, activity calendars, recorded scope sums and averages. Rework share uses same-day rework divided by this total; the work-breakdown donut follows the same definition. Saved summary rows are recalculated on read without rewriting retained evidence.

Report routes share a prepared payload for up to 60 seconds; manual/automatic Refresh bypasses it. The dashboard legend and trend load independently of aggregate/queue callbacks, and graphs resize when a hidden page becomes visible. Failed workflow polls retain the last known approval evidence with an error message. Unchanged workflow evidence skips projection writes.

User Report presents eight cards, including completed video duration and task count for the current settlement date range. Completion uses the efficiency API's current completed status, independently of daily submissions; it does not reconstruct approval times for historical dates. Completion captures are reused for 60 seconds and remapped when user mappings change. Failed or incomplete completion reads show unavailable data. The activity calendar and individual trend share equal desktop columns and stack on mobile. All User Report graphs have explicit container heights to prevent Dash's default `height: 100%` from extending below their section headings.

Pending Review now loads independently from a fully paginated current efficiency capture, with targeted refresh, mapping/workflow invalidation, a 60-second reuse limit, and protection against older requests finishing last. Shared chart wrappers contain dense charts, preserve full labels and values in expandable tables, and clearly mark previous data during refresh. See [chart correctness and verification](docs/dashboard-chart-audit.md).

Daily Report reconciles yesterday against the current submission source on entry and Refresh, retaining already observed tasks and adding later submissions. An afternoon capture no longer becomes the final daily total at midnight. Work and comparison charts use the same per-person totals and include the same zero-output roster.

Scheduled capture and recovery for all plot sources is available through the
protected `/api/data-refresh` and `/api/data-health` endpoints or
`slicing-dashboard data-refresh`. Vercel Hobby has a daily fallback capture;
frequent independent captures require an external scheduler. See the
[deployment and recovery setup](docs/data-recovery.md) before enabling it.

Current assignable video duration and task count come only from
`/api/slice/pool-inventory`, summing the normal and urgent unassigned pools. A
verified empty pool displays zero; failed or invalid captures display unavailable.
Historical overview/funnel values do not override this inventory.

## Project Structure

```
src/slicing_dashboard/
├── app.py              # Dash web application
├── cli.py              # Click CLI
├── config.py           # Configuration & settings
├── data_manager.py     # Data fetching & caching
├── db.py               # Database connection
├── assets/             # Static assets (CSS, images)
├── extraction/         # Data extraction logic
├── models/             # Pydantic / SQLAlchemy models
├── processing/         # Data cleaning & normalization
├── reporting/          # Weekly report generation
└── scraper/            # HTTP scraper
```
