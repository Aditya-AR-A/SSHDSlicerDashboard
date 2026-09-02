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
