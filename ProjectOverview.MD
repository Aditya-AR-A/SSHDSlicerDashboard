# Video Slicing Dashboard — Scraping, Data Processing & Reporting System

## 1. Project Overview

Build a Python-based data extraction and reporting system for the video-slicing dashboard hosted at:

`http://120.79.192.226/`

The system will authenticate using the administrator account, access the admin slicing dashboard, extract the **video split/slicing completion table for all users**, clean and normalize the data, store it in a structured local dataset, and generate Excel-based reports.

The project will initially operate in a **manual/semi-automated extraction mode** to understand and validate the dashboard's data structure. Once the extraction and transformation logic has been verified against the dashboard, it will be converted into an automated pipeline capable of producing daily, weekly, and monthly reports.

### Primary objective

Extract reliable user-level video slicing completion data from the admin dashboard and transform it into reporting-ready datasets that can answer:

* How much video slicing work was completed?
* How much was completed by each user?
* When was the work completed?
* What work belongs to which reporting period/cycle?
* What is the daily completion?
* What is the weekly completion?
* What is the monthly completion?
* How much total work has been completed?
* How does the current period compare with previous periods?

---

# 2. Core Requirements

## 2.1 Authentication

The system must:

1. Open the dashboard website.
2. Navigate to the login page if required.
3. Authenticate using administrator credentials.
4. Maintain the authenticated session.
5. Navigate to the relevant slicing dashboard page.
6. Verify that the administrator dashboard has loaded successfully.
7. Extract data from the admin-level table.

Credentials must **never be hardcoded into source code**.

Use environment variables or a local `.env` file:

```text
DASHBOARD_URL=http://120.79.192.226/
ADMIN_USERNAME=
ADMIN_PASSWORD=
```

`.env` must be excluded from Git using `.gitignore`.

---

# 3. Extraction Strategy

The extraction architecture must support two methods.

## Method 1 — Normal HTTP/HTML extraction

Try normal HTTP-based extraction first.

Potential tools:

* `requests`
* `httpx`
* `BeautifulSoup`
* `pandas.read_html()`

The goal is to determine whether the dashboard data is directly available in the HTML or through an accessible backend/API endpoint.

Before implementing Selenium, inspect:

* HTML source
* forms
* login requests
* cookies
* redirects
* network/API endpoints if discoverable
* XHR/fetch requests
* table HTML
* pagination parameters
* query parameters
* JSON responses

If the data can be extracted reliably without a browser, prefer this method.

### Important

Do not automatically use Selenium just because the site has a login page.

Determine whether:

```text
Browser
   ↓
Login
   ↓
HTTP request/API
   ↓
Table data
```

can be reproduced directly.

---

# 4. Selenium Fallback

If normal HTTP extraction cannot reliably access the dashboard data, use Selenium.

Selenium should be implemented as a dedicated browser extraction layer rather than mixed into the data-processing code.

Recommended structure:

```text
scraper/
    base.py
    http_scraper.py
    selenium_scraper.py
```

The Selenium scraper must:

1. Start Chrome/Chromium.
2. Navigate to the dashboard.
3. Enter admin credentials.
4. Submit login.
5. Wait for authentication to complete.
6. Verify successful login.
7. Navigate to the slicing dashboard.
8. Wait for the table to become available.
9. Extract the table.
10. Handle pagination if present.
11. Handle dynamic loading if present.
12. Save a raw extraction snapshot.
13. Return structured records to the processing layer.

Use explicit waits rather than arbitrary `sleep()` calls wherever possible.

---

# 5. Dashboard Discovery Phase

Before building the final scraper, perform a discovery pass.

The agent must document:

### Login

* Login URL
* Username field selector
* Password field selector
* Login button selector
* Successful-login indicator
* Failure-login indicator

### Dashboard

* Slicing dashboard URL
* Navigation path
* Table location
* Table identifier/class
* Pagination mechanism
* Filters
* Sorting
* Date selectors
* User selectors
* Export functionality, if available

### Table

Identify every column in the target table.

The expected table should contain some combination of fields such as:

```text
User
Video
Task
Duration
Completed Duration
Status
Date
Created At
Completed At
```

Do **not assume these exact columns exist**.

The actual dashboard schema discovered during the first extraction must become the source of truth.

---

# 6. Raw Data Extraction

The scraper must preserve the original extracted data before cleaning.

Create a raw layer:

```text
data/
    raw/
    processed/
    reports/
```

Example:

```text
data/raw/
    slicing_2026-08-26.csv
```

or, preferably, if the source is HTML:

```text
data/raw/
    slicing_2026-08-26.html
```

If the dashboard returns JSON:

```text
data/raw/
    slicing_2026-08-26.json
```

The raw data should remain unchanged.

This is important because if a transformation bug is discovered later, the processing can be repeated without scraping the dashboard again.

---

# 7. Data Processing Pipeline

Use Pandas for all data manipulation.

Pipeline:

```text
Dashboard
    ↓
Authentication
    ↓
Raw Extraction
    ↓
Raw Snapshot
    ↓
Pandas DataFrame
    ↓
Cleaning
    ↓
Normalization
    ↓
Validation
    ↓
Processed Dataset
    ↓
Reports
```

---

# 8. Data Cleaning

The processing layer must handle:

## User normalization

The same person may appear under multiple dashboard identifiers.

Known mappings from the existing project should be supported through a configurable mapping table rather than hardcoded transformation logic.

Example:

```python
USER_MAPPING = {
    "SSHD-Adi": "Aditya",
    "SSHD-Aditya": "Aditya",
    "SSHD-Slicer1": "Aditya",

    "SSHD-Komal": "Komal",
    "SSHD-Ranjeeta": "Komal",

    "SSHD-Priya": "Priya",
    "SSHD-Slicer2": "Priya",

    "SSHD-Rajni": "Rajni",
}
```

The mapping must live in configuration/data rather than being scattered throughout the code.

Unknown users must **not be silently discarded**.

Instead:

```text
Unknown user → validation warning → preserve original identifier
```

---

# 9. Duration Normalization

Duration values may appear in formats such as:

```text
10:33
00:33:00
1:45:20
10.5
10 min
```

The system must normalize duration into a consistent representation.

Recommended internal representation:

```text
duration_seconds
```

or Pandas:

```python
pd.Timedelta
```

For reporting, expose:

```text
HH:MM:SS
```

Example:

```text
10:33:00
05:08:00
03:07:00
01:45:00
```

Do not store durations as formatted strings internally.

---

# 10. Date and Time Normalization

All timestamps should be parsed into proper Pandas datetime values.

Example:

```python
df["completed_at"] = pd.to_datetime(
    df["completed_at"],
    errors="coerce"
)
```

The system must explicitly handle:

* invalid timestamps
* missing timestamps
* timezone information
* duplicate timestamps
* date-only fields

The canonical timezone should be configurable.

For this project, use:

```text
Asia/Kolkata
```

unless the dashboard data is proven to already represent another timezone.

---

# 11. Deduplication

The scraper may encounter duplicate records because of:

* pagination
* repeated extraction
* dashboard refreshes
* overlapping date ranges
* task updates

The processing layer must implement deterministic deduplication.

Ideally derive a unique task identifier from the dashboard.

Preferred:

```text
task_id
```

Fallback composite key:

```text
user + video/task identifier + completion timestamp
```

Do not simply call:

```python
df.drop_duplicates()
```

unless the dashboard schema proves that every complete row is uniquely identifiable.

---

# 12. Canonical Dataset

Create a normalized dataset with a stable schema.

The exact source columns will be determined during discovery, but the processed dataset should aim for a structure similar to:

```text
record_id
user_id
user_name
video_id
video_name
task_id
status
duration_seconds
duration
completed_at
completed_date
completed_week
completed_month
source_extracted_at
```

Additional source-specific fields should be preserved where useful.

---

# 13. Historical Data

The system must **not overwrite historical extraction data**.

Every successful extraction should have a timestamp.

Example:

```text
data/
├── raw/
│   ├── 2026-08-26/
│   │   └── slicing.csv
│   ├── 2026-08-27/
│   │   └── slicing.csv
│
├── processed/
│   ├── slicing_master.parquet
│
└── reports/
    ├── daily/
    ├── weekly/
    └── monthly/
```

For the initial implementation, CSV is acceptable.

For larger datasets, use Parquet as the canonical processed storage format.

Pandas must remain the primary manipulation layer.

---

# 14. Reporting Requirements

The system must eventually generate three reporting levels.

## Daily Report

Example:

```text
Date: 26-Aug-2026

User       Completed Tasks    Completed Duration
--------------------------------------------------
Aditya          42                 05:08:00
Komal           31                 03:07:00
Priya           18                 01:45:00
Rajni            0                 00:00:00
--------------------------------------------------
Total           91                 10:00:00
```

The exact metrics depend on the dashboard schema.

---

# 15. Weekly Report

Weekly report should aggregate all records belonging to the selected week.

Include:

* Total completed duration
* Total completed tasks
* User-wise completion
* Daily breakdown
* Percentage contribution by user
* Comparison with previous week where data permits

Example:

```text
Week          Total Duration
2026-W34        42:17:00
2026-W35        51:43:00
```

---

# 16. Monthly Report

Monthly report should include:

* Total completed duration
* Total tasks
* User-wise totals
* Weekly breakdown
* Daily trend
* User contribution
* Month-over-month comparison

Example:

```text
August 2026

User       Total Duration
Aditya        81:22:00
Komal         64:13:00
Priya         41:35:00
Rajni         12:20:00
-------------------------
Total        199:30:00
```

---

# 17. Billing-Cycle Support

The existing workflow uses project/billing cycles, so the data model should not be designed only around calendar days.

Support:

```text
cycle_id
cycle_start
cycle_end
```

A reporting cycle must preserve its historical snapshot.

For example:

```text
Cycle 1
Start: 2026-08-01
End:   2026-08-07
```

Once Cycle 1 is finalized, later dashboard changes must not silently rewrite the historical report.

This is particularly important for determining:

* completed work
* settled work
* remaining work
* user allocation
* incentive/billing calculations

---

# 18. Settled vs Unsettled Data

The reporting architecture should leave room for settlement tracking.

The scraper itself should extract **actual dashboard facts**.

Settlement should be a separate business layer.

For example:

```text
Extracted Completion
        ↓
Processed Completion
        ↓
Cycle Assignment
        ↓
Settlement Status
```

Potential fields:

```text
cycle_id
completed_duration
settled_duration
remaining_duration
settlement_status
```

This prevents financial/business logic from contaminating the scraping layer.

---

# 19. Excel Output

Excel is the primary user-facing output.

Use:

```text
openpyxl
```

for Excel formatting where required, while using Pandas for dataset creation.

Reports should contain separate worksheets.

Example:

```text
Daily Report.xlsx

Sheets:
1. Summary
2. User Summary
3. Daily Data
4. Raw/Source Data
5. Validation
```

Weekly:

```text
Weekly Report.xlsx

Sheets:
1. Summary
2. User Summary
3. Daily Breakdown
4. Raw Data
5. Validation
```

Monthly:

```text
Monthly Report.xlsx

Sheets:
1. Summary
2. User Summary
3. Weekly Breakdown
4. Daily Breakdown
5. Raw Data
6. Validation
```

---

# 20. Jupyter Notebook

A Jupyter notebook should be included during the initial development phase.

Purpose:

* inspect scraped data
* understand dashboard fields
* identify anomalies
* test cleaning logic
* validate user mappings
* validate durations
* compare dashboard totals with Pandas totals
* experiment with report calculations
* visualize trends

Example:

```text
notebooks/
    01_dashboard_exploration.ipynb
    02_data_cleaning_validation.ipynb
    03_reporting_analysis.ipynb
```

The notebook is for **exploration and validation**, not the final production pipeline.

Production logic must eventually live inside Python modules.

---

# 21. Visualization

Use Pandas/Matplotlib/Plotly where useful.

Initial visualizations:

### Daily completion

```text
Date → Total completed duration
```

### User productivity

```text
User → Total completed duration
```

### User contribution

```text
User → Percentage of total work
```

### Weekly trend

```text
Week → Total completed duration
```

Visualization should be generated from the processed dataset rather than directly from the scraper.

---

# 22. Project Structure

Use `uv` for Python package management and project management.

Recommended structure:

```text
video-slicing-dashboard/
│
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── .gitignore
│
├── src/
│   └── slicing_dashboard/
│       ├── __init__.py
│       │
│       ├── config.py
│       │
│       ├── scraper/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── http_scraper.py
│       │   └── selenium_scraper.py
│       │
│       ├── extraction/
│       │   ├── __init__.py
│       │   ├── dashboard.py
│       │   └── parsers.py
│       │
│       ├── processing/
│       │   ├── __init__.py
│       │   ├── cleaning.py
│       │   ├── normalization.py
│       │   ├── deduplication.py
│       │   └── validation.py
│       │
│       ├── reporting/
│       │   ├── __init__.py
│       │   ├── daily.py
│       │   ├── weekly.py
│       │   ├── monthly.py
│       │   └── excel.py
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   └── schemas.py
│       │
│       └── cli.py
│
├── notebooks/
│   ├── 01_dashboard_exploration.ipynb
│   ├── 02_data_cleaning_validation.ipynb
│   └── 03_reporting_analysis.ipynb
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── reports/
│
├── tests/
│   ├── test_cleaning.py
│   ├── test_normalization.py
│   ├── test_deduplication.py
│   ├── test_reporting.py
│   └── fixtures/
│
└── logs/
```

---

# 23. Suggested Dependencies

Use `uv` to manage dependencies.

Core:

```text
pandas
numpy
requests
httpx
beautifulsoup4
lxml
openpyxl
python-dotenv
pydantic
```

Browser fallback:

```text
selenium
```

Analysis:

```text
jupyter
matplotlib
plotly
```

Testing:

```text
pytest
```

The agent should add dependencies only when actually required.

Do not install Selenium-dependent packages if HTTP extraction is sufficient.

---

# 24. CLI

The project should eventually expose simple commands.

Examples:

```bash
uv run python -m slicing_dashboard.cli discover
```

Discover the dashboard structure.

```bash
uv run python -m slicing_dashboard.cli scrape
```

Perform an extraction.

```bash
uv run python -m slicing_dashboard.cli process
```

Process raw data.

```bash
uv run python -m slicing_dashboard.cli report --daily
```

Generate daily report.

```bash
uv run python -m slicing_dashboard.cli report --weekly
```

Generate weekly report.

```bash
uv run python -m slicing_dashboard.cli report --monthly
```

Generate monthly report.

Eventually:

```bash
uv run python -m slicing_dashboard.cli run
```

should perform:

```text
Scrape
  ↓
Validate
  ↓
Process
  ↓
Update master dataset
  ↓
Generate reports
```

---

# 25. Validation System

Validation is critical.

The system must detect:

### Extraction problems

* Login failed
* Dashboard unavailable
* Table missing
* Empty table
* Unexpected columns
* Pagination failure

### Data problems

* Missing user
* Unknown user
* Invalid duration
* Invalid date
* Duplicate record
* Negative duration
* Impossible duration
* Missing task identifier

### Reporting problems

* Daily total does not equal user totals
* Weekly total does not equal daily totals
* Monthly total does not equal weekly totals

Warnings must be visible in:

```text
logs/
```

and in the Excel `Validation` sheet.

---

# 26. Dashboard-vs-Pandas Verification

During Phase 1, manually compare the extracted results against the dashboard.

For example:

```text
Dashboard:
Total completed = 10:00:00

Pandas:
Total completed = 10:00:00

Difference:
00:00:00
```

Do this for:

* total tasks
* total duration
* individual users
* dates
* reporting periods

Automation should **not be considered production-ready until the extracted data reconciles with the dashboard**.

---

# 27. Initial Manual Workflow

The first implementation should intentionally be simple.

### Step 1

Open dashboard manually and understand:

```text
Login
→ Admin
→ Slicing Dashboard
→ Target Table
```

### Step 2

Determine whether the table is available through normal HTTP requests.

### Step 3

If possible, implement HTTP extraction.

### Step 4

If not, implement Selenium.

### Step 5

Extract one complete dataset.

### Step 6

Save the raw dataset.

### Step 7

Load it into Jupyter.

### Step 8

Understand every column.

### Step 9

Build the Pandas cleaning pipeline.

### Step 10

Normalize users and durations.

### Step 11

Calculate totals.

### Step 12

Compare results against the dashboard manually.

### Step 13

Generate the first Excel report.

Only after this is correct should automation begin.

---

# 28. Automation Phase

Once the extraction is stable, automate:

```text
Daily
    ↓
Authenticate
    ↓
Extract
    ↓
Store raw snapshot
    ↓
Process
    ↓
Validate
    ↓
Update master dataset
    ↓
Generate daily report
```

Weekly and monthly reports should be generated from the master processed dataset rather than requiring separate scraping logic.

This avoids maintaining three different scrapers.

---

# 29. Incremental Scraping

The production scraper should eventually avoid unnecessarily downloading the entire historical dataset.

Preferred strategy:

```text
First run:
    Extract all available records

Later runs:
    Extract recent/new records
    ↓
    Merge with historical dataset
    ↓
    Deduplicate
```

However, do not implement incremental extraction until the dashboard's filtering/pagination behavior is understood.

Correctness is more important than optimization.

---

# 30. Error Handling

The scraper must fail safely.

Example:

```text
Login failure
    ↓
Do NOT overwrite existing data
    ↓
Log failure
    ↓
Exit
```

Similarly:

```text
Dashboard table unexpectedly empty
    ↓
Treat as extraction failure
    ↓
Do NOT replace yesterday's dataset with empty data
```

This prevents a dashboard outage from destroying historical reporting data.

---

# 31. Logging

Use Python's `logging` module.

Logs should include:

```text
timestamp
level
component
message
```

Example:

```text
2026-08-26 09:00:01 INFO  Starting dashboard extraction
2026-08-26 09:00:03 INFO  Login successful
2026-08-26 09:00:05 INFO  Slicing dashboard loaded
2026-08-26 09:00:07 INFO  143 records extracted
2026-08-26 09:00:08 INFO  2 duplicate records removed
2026-08-26 09:00:09 INFO  Validation successful
2026-08-26 09:00:10 INFO  Daily report generated
```

---

# 32. Security

Never commit:

```text
admin username
admin password
session cookies
authentication tokens
```

Use:

```text
.env
```

and:

```text
.env.example
```

The repository should contain only:

```text
DASHBOARD_URL=
ADMIN_USERNAME=
ADMIN_PASSWORD=
```

---

# 33. Testing

Tests should focus heavily on the processing layer because scraping can change when the dashboard UI changes.

Unit tests must cover:

### Duration parsing

```text
10:33
00:10:33
1:45:00
```

### User mapping

```text
SSHD-Adi → Aditya
SSHD-Aditya → Aditya
```

### Duplicate detection

### Missing values

### Date conversion

### Daily aggregation

### Weekly aggregation

### Monthly aggregation

### Cycle aggregation

### Excel generation

Use saved raw dashboard responses as test fixtures so processing tests do not require the live dashboard.

---

# 34. Development Phases

## Phase 1 — Dashboard Discovery

Goal:

Understand exactly how the website works.

Deliverables:

* Login flow documented
* Dashboard URL identified
* Target table identified
* Table schema documented
* Pagination behavior documented
* HTTP/API extraction feasibility determined
* Selenium requirement determined

---

## Phase 2 — First Extraction

Goal:

Successfully extract the complete admin slicing table.

Deliverables:

* HTTP scraper OR Selenium scraper
* Raw extraction file
* Reproducible extraction script
* Extraction logs

Success criteria:

```text
Dashboard data
=
Extracted data
```

---

## Phase 3 — Data Cleaning

Goal:

Create reliable normalized data.

Deliverables:

* Pandas pipeline
* User mapping
* Duration normalization
* Date normalization
* Deduplication
* Validation
* Canonical dataset

---

## Phase 4 — Exploratory Analysis

Goal:

Understand the business/reporting metrics.

Deliverables:

* Jupyter notebook
* User totals
* Daily totals
* Weekly totals
* Monthly totals
* Cycle totals
* Dashboard reconciliation

---

## Phase 5 — Excel Reporting

Goal:

Produce usable reports.

Deliverables:

* Daily Excel report
* Weekly Excel report
* Monthly Excel report
* Summary sheets
* User breakdown
* Validation sheet

---

## Phase 6 — Historical Dataset

Goal:

Create a persistent master dataset.

Deliverables:

```text
raw snapshots
+
processed master dataset
+
historical reporting
```

No historical records should be silently overwritten.

---

## Phase 7 — Automation

Goal:

Run the pipeline without manual intervention.

Deliverables:

```text
Automated extraction
Automated processing
Automated validation
Automated Excel generation
```

Scheduling mechanism can be added after the pipeline itself is reliable.

---

# 35. Definition of Done

The initial project is considered successful when the system can:

1. Authenticate to the admin dashboard.
2. Navigate to the slicing dashboard.
3. Extract the complete split-video completion table.
4. Handle all pages of the table.
5. Preserve raw extracted data.
6. Load the data into Pandas.
7. Normalize user identities.
8. Normalize durations.
9. Normalize timestamps.
10. Remove duplicates deterministically.
11. Detect malformed records.
12. Calculate user-level totals.
13. Calculate daily totals.
14. Calculate weekly totals.
15. Calculate monthly totals.
16. Support project/billing cycles.
17. Generate Excel output.
18. Produce a validation summary.
19. Reconcile calculated totals against the dashboard.
20. Run repeatedly without destroying historical data.

---

# 36. Agent Execution Instructions

The agent should **not attempt to build the entire production system immediately**.

Work incrementally.

### First task

Perform dashboard reconnaissance.

Determine:

```text
1. How authentication works
2. Where the slicing dashboard is
3. What the target table contains
4. Whether data is server-rendered
5. Whether an API/XHR endpoint exists
6. Whether pagination exists
7. Whether filters exist
8. Whether HTTP extraction is possible
9. Whether Selenium is actually necessary
```

Then report the findings.

### Second task

Build the smallest possible extractor that retrieves the complete table.

Do not build reporting before extraction has been validated.

### Third task

Save a representative raw dataset and use Jupyter/Pandas to understand the data.

### Fourth task

Implement cleaning and normalization.

### Fifth task

Reconcile the calculated values with the dashboard.

### Sixth task

Build Excel reports.

### Seventh task

Only after all of the above works reliably, implement scheduled/automated execution.

---

# 37. Architectural Principle

Keep these concerns strictly separated:

```text
SCRAPING
    ↓
RAW DATA
    ↓
PROCESSING
    ↓
VALIDATION
    ↓
BUSINESS LOGIC
    ↓
REPORTING
```

Do not put Pandas aggregation logic inside Selenium.

Do not put Selenium selectors inside reporting code.

Do not put settlement logic inside the scraper.

Do not make Excel the source of truth.

The **processed master dataset** should be the source for reporting, while raw extraction snapshots provide reproducibility and auditability.

---

# 38. Future Extensions

The architecture should leave room for:

* automated email/report delivery
* dashboard monitoring
* Slack/WhatsApp notifications
* anomaly detection
* user productivity dashboards
* incentive calculations
* settlement tracking
* cycle management
* database storage
* scheduled execution
* extraction failure alerts
* historical productivity analysis

These should **not** be implemented in the first phase unless required for validating the core pipeline.

---

# Final Initial Milestone

The first milestone should be:

> **Successfully log into the admin dashboard, extract the complete video-slicing completion table, save an untouched raw snapshot, load it into Pandas, identify and document the actual schema, and produce a first validated Excel report containing user-wise completed work.**

Once that milestone is achieved, the rest of the project becomes a controlled data-engineering/reporting problem rather than an uncertain scraping problem.
