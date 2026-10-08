"""Accessible management panels for settlement periods and account mappings."""
import json
import os

from dash import dcc, html, Input, Output, State, dash_table, no_update, ctx
import dash_bootstrap_components as dbc

from slicing_dashboard.config import PROJECT_ROOT, get_settings
from slicing_dashboard.management.periods import today_iso, validate_period_rows


def _get_admin_password():
    return (os.environ.get("SLICER_ADMIN_PASSWORD") or get_settings().slicer_admin_password or "").strip()


def _authorized(password):
    configured = _get_admin_password()
    return bool(configured) and password == configured


def _message(text, color="danger"):
    return dbc.Alert(text, color=color, className="mgmt-feedback")


def _layout(kind, title, description):
    return html.Section([
        html.Div([
            html.Div(html.I(className="bi bi-shield-lock", **{"aria-hidden": "true"}), className="mgmt-lock-icon"),
            html.Div("ADMIN CONTROLS", className="mgmt-eyebrow"),
            html.H3(title), html.P(description, className="mgmt-description"),
            html.Label("Admin password", htmlFor=f"{kind}-auth-input", className="mgmt-label"),
            dbc.Input(id=f"{kind}-auth-input", type="password", autoComplete="current-password",
                      placeholder="Enter admin password", className="mgmt-input"),
            dbc.Button("Unlock controls", id=f"btn-unlock-{kind}", color="primary", className="mgmt-unlock"),
            html.Div(id=f"{kind}-auth-error", **{"aria-live": "polite"}),
        ], id=f"{kind}-auth-card", className="mgmt-auth-card"),
        dcc.Loading(html.Div(id=f"{kind}-content"), type="circle", color="#2563eb"),
    ], className="mgmt-container", **{"aria-label": title})


def layout_settlement_management(is_dark=True):
    return _layout("settlement", "Settlement periods", "Manage billing dates and close completed periods. The current period follows today automatically.")


def layout_user_mapping(is_dark=True):
    return _layout("mapping", "User mappings", "Connect slicer accounts to people so their work appears together in reports.")


def _table(kind, data, columns, dropdown):
    return dash_table.DataTable(
        id=f"{kind}-table", data=data, columns=columns, dropdown=dropdown,
        editable=True, row_deletable=True, sort_action="native", filter_action="native", page_action="native", page_size=15,
        style_table={"overflowX": "auto", "minHeight": "180px"},
        style_header={"backgroundColor": "var(--table-header-bg)", "color": "var(--text-secondary)",
                      "fontWeight": "600", "padding": "14px 16px", "border": "none"},
        style_cell={"backgroundColor": "var(--glass-card-bg)", "color": "var(--text-primary)",
                    "border": "none", "borderBottom": "1px solid var(--glass-border)", "padding": "14px 16px",
                    "fontSize": "14px", "fontFamily": "Inter, sans-serif", "textAlign": "left",
                    "minWidth": "150px", "whiteSpace": "normal", "height": "auto"},
        style_data_conditional=[
            {"if": {"state": "active"}, "border": "2px solid var(--accent-blue)", "backgroundColor": "var(--dropdown-option-hover)"},
            {"if": {"filter_query": '{mapping_type} = "New"'}, "fontWeight": "600"},
            {"if": {"filter_query": '{status} = "Current"'}, "fontWeight": "600"},
        ],
        css=[{"selector": ".Select-menu-outer", "rule": "display: block !important; z-index: 1000;"}]
            + ([{"selector": ".dash-filter", "rule": "display: none;"}] if kind == "mapping" else []),
    )


def _build_settlement_table(periods, is_dark=True):
    rows = [{"start_date": p["start_date"], "end_date": p["end_date"],
             "status": "Current" if p.get("is_current") else "Settled" if p.get("settled") else "Unsettled"} for p in periods]
    return _table("settlement", rows, [
        {"name": "Start date", "id": "start_date"}, {"name": "End date", "id": "end_date"},
        {"name": "Status", "id": "status", "presentation": "dropdown"},
    ], {"status": {"options": [{"label": v, "value": v} for v in ("Current", "Settled", "Unsettled")]}})


def _build_mapping_table(mappings, is_dark=True):
    rows = [{k: m.get(k, "") for k in ("id", "mapped_user", "mapping_type")} for m in mappings]
    return _table("mapping", rows, [
        {"name": "Account", "id": "id"}, {"name": "Person", "id": "mapped_user", "presentation": "dropdown"},
        {"name": "Status", "id": "mapping_type", "presentation": "dropdown"},
    ], {"mapped_user": {"options": person_options(mappings)}, "mapping_type": {"options": [
        {"label": "Mapped", "value": "Existing"}, {"label": "Needs mapping", "value": "New"},
        {"label": "Excluded from totals", "value": "Exempt"},
    ]}})


def person_options(rows):
    names = {}
    for row in rows or []:
        name = str(row.get("mapped_user") or "").strip()
        if name and name.casefold() not in ("unassigned", "exempt"):
            names.setdefault(name.casefold(), name)
    return [{"label": name, "value": name} for name in sorted(names.values(), key=str.casefold)]


def assign_person(rows, account, selected_person, new_person):
    person = str(new_person or "").strip() if selected_person == "__new__" else str(selected_person or "").strip()
    if not account:
        raise ValueError("Choose an account to map.")
    if not person or person.casefold() in ("unassigned", "exempt"):
        raise ValueError("Choose an existing person or enter a new person’s name.")
    existing = {o["value"].casefold(): o["value"] for o in person_options(rows)}
    person = existing.get(person.casefold(), person)
    if not any(r.get("id") == account for r in rows or []):
        raise ValueError("This account is no longer in the table. Choose another account.")
    return [dict(r, mapped_user=person, mapping_type="Existing") if r.get("id") == account else dict(r) for r in rows], person


def _stat(label, value):
    return html.Div([html.Span(label), html.Strong(str(value))], className="mgmt-stat")


def _editor(kind, title, description, stats, table, extra=None):
    return html.Div([
        html.Header([
            html.Div([html.Div("ADMIN CONTROLS", className="mgmt-eyebrow"), html.H3(title),
                      html.P(description, className="mgmt-description")]),
            dbc.Button([html.I(className="bi bi-plus-lg me-2", **{"aria-hidden": "true"}),
                        "Add period" if kind == "settlement" else "Add account"],
                       id=f"btn-add-{kind}", color="primary"),
        ], className="mgmt-header-banner"),
        html.Div(stats, className="mgmt-stats"), extra,
        html.Div([
            html.P("Select a cell to edit. Use Tab to move between cells. Dates use YYYY-MM-DD." if kind == "settlement"
                   else "Select a cell to edit. Map each account to a person, or exclude it from totals.", className="mgmt-help"),
            table,
        ], className="mgmt-table-card"),
        html.Div([
            html.Div([html.Label("Confirm admin password", htmlFor=f"{kind}-save-auth", className="mgmt-label"),
                      dbc.Input(id=f"{kind}-save-auth", type="password", autoComplete="current-password",
                                placeholder="Admin password", className="mgmt-input")], className="mgmt-save-field"),
            dbc.Button("Save changes", id=f"btn-save-{kind}", color="primary", className="mgmt-save-button"),
            html.Span("Changes apply only after saving.", className="mgmt-help"),
        ], className="mgmt-save-toolbar"),
        html.Div(id=f"{kind}-save-output", **{"aria-live": "polite"}),
    ], className="mgmt-editor")


def validate_mappings(rows):
    if not rows:
        raise ValueError("Add at least one account before saving.")
    valid, seen = [], set()
    for index, row in enumerate(rows, 1):
        uid = str(row.get("id") or "").strip()
        person = str(row.get("mapped_user") or "").strip()
        status = row.get("mapping_type")
        if not uid:
            raise ValueError(f"Row {index}: enter an account name.")
        if uid.casefold() in seen:
            raise ValueError(f"Account '{uid}' appears more than once.")
        seen.add(uid.casefold())
        if status not in ("Existing", "New", "Exempt"):
            raise ValueError(f"Row {index}: choose a valid status.")
        if status == "Existing" and (not person or person.casefold() == "unassigned"):
            raise ValueError(f"Row {index}: enter a person’s name, or choose Needs mapping.")
        valid.append({"id": uid, "mapped_user": person or ("Exempt" if status == "Exempt" else "Unassigned"), "mapping_type": status})
    return valid


def register_management_callbacks(app, dm):
    @app.callback(
        Output("settlement-content", "children"), Output("settlement-auth-error", "children"), Output("settlement-auth-card", "style"),
        Input("btn-unlock-settlement", "n_clicks"), Input("settlement-auth-input", "n_submit"),
        State("settlement-auth-input", "value"), prevent_initial_call=True)
    def unlock_settlement(clicks, submits, password):
        if not _authorized(password):
            return no_update, _message("Incorrect admin password. Try again."), no_update
        periods = dm.get_available_periods()
        current = next((p for p in periods if p.get("is_current")), None)
        extra = html.Div([
            html.I(className="bi bi-calendar-check", **{"aria-hidden": "true"}),
            html.Div([html.Strong("Current period ends today"),
                      html.P(f"{current['start_date']} → today · Updates automatically in India time. To close this period, choose Settled and set its final end date.")]),
        ], className="mgmt-current-note") if current else None
        content = _editor("settlement", "Settlement periods", "Keep closed periods fixed and the current period up to date.",
                          [_stat("Periods", len(periods)), _stat("Settled", sum(bool(p.get("settled")) for p in periods)),
                           _stat("Current end date", "Through today" if current else "No current period")],
                          _build_settlement_table(periods), extra)
        content.children.append(dcc.Interval(id="settlement-date-interval", interval=60_000, n_intervals=0))
        return content, "", {"display": "none"}

    @app.callback(Output("settlement-table", "data"), Input("btn-add-settlement", "n_clicks"),
                  Input("settlement-date-interval", "n_intervals"),
                  State("settlement-table", "data"), prevent_initial_call=True)
    def add_settlement_row(clicks, intervals, rows):
        if ctx.triggered_id == "settlement-date-interval":
            if not any(r.get("status") == "Current" and r.get("end_date") != today_iso() for r in (rows or [])):
                return no_update
            return [dict(r, end_date=today_iso()) if r.get("status") == "Current" else r for r in (rows or [])]
        return [{"start_date": "", "end_date": "", "status": "Unsettled"}] + list(rows or [])

    @app.callback(Output("settlement-save-output", "children"), Input("btn-save-settlement", "n_clicks"),
                  Input("settlement-save-auth", "n_submit"), State("settlement-save-auth", "value"),
                  State("settlement-table", "data"), prevent_initial_call=True)
    def save_settlement(clicks, submits, password, rows):
        if not _authorized(password):
            return _message("Incorrect admin password. Your changes have not been saved.")
        try:
            periods = validate_period_rows(rows)
        except ValueError as error:
            return _message(str(error))
        if not dm.db.save_settlement_periods(periods):
            return _message("Unable to save periods. Your edits are still here; try again.")
        dm.settlement_periods = periods
        return _message(f"Saved {len(periods)} periods. The current period will continue through today automatically.", "success")

    @app.callback(
        Output("mapping-content", "children"), Output("mapping-auth-error", "children"), Output("mapping-auth-card", "style"),
        Input("btn-unlock-mapping", "n_clicks"), Input("mapping-auth-input", "n_submit"),
        State("mapping-auth-input", "value"), prevent_initial_call=True)
    def unlock_mapping(clicks, submits, password):
        if not _authorized(password):
            return no_update, _message("Incorrect admin password. Try again."), no_update
        missing = dm.get_unassigned_users(force_refresh=True)
        data = [{"id": uid, "mapped_user": "Unassigned", "mapping_type": "New"} for uid in missing]
        data += list(dm.user_mapping_full.values())
        extra = html.Div([
            html.Div([html.Label("Search accounts or people", htmlFor="mapping-search", className="mgmt-label"),
                      dbc.Input(id="mapping-search", placeholder="Search by account or person…", debounce=True, className="mgmt-input")]),
            html.Div([html.Label("Show accounts", htmlFor="mapping-status-filter", className="mgmt-label"),
                      dbc.Select(id="mapping-status-filter", value="all", options=[
                          {"label": "All accounts", "value": "all"}, {"label": "Needs mapping", "value": "New"},
                          {"label": "Mapped", "value": "Existing"}, {"label": "Excluded", "value": "Exempt"},
                      ], className="mgmt-input")]),
        ], className="mgmt-filters")
        assignment = html.Div([
            html.H4("Map an account", className="fs-6 fw-semibold mb-1"),
            html.P("Choose an existing person or add someone new, then apply the mapping. Save changes when you’re done.", className="mgmt-help"),
            html.Div([
                html.Div([html.Label("Account", htmlFor="mapping-assign-account", className="mgmt-label"),
                          dcc.Dropdown(id="mapping-assign-account", options=[{"label": r["id"], "value": r["id"]} for r in data],
                                       value=missing[0] if missing else None, placeholder="Choose an account", className="dropdown-glass")]),
                html.Div([html.Label("Person", htmlFor="mapping-assign-person", className="mgmt-label"),
                          dcc.Dropdown(id="mapping-assign-person", options=person_options(data) + [{"label": "+ Add a new person", "value": "__new__"}],
                                       placeholder="Choose a person", className="dropdown-glass")]),
                html.Div([html.Label("New person’s name", htmlFor="mapping-new-person", className="mgmt-label"),
                          dbc.Input(id="mapping-new-person", placeholder="Enter full name", className="mgmt-input")],
                         id="mapping-new-person-field", style={"display": "none"}),
                dbc.Button("Apply mapping", id="btn-apply-mapping", color="primary", className="mgmt-apply-button"),
            ], className="mgmt-assignment-fields"),
            html.Div(id="mapping-assign-feedback", **{"aria-live": "polite"}),
        ], className="mgmt-assignment-card")
        return _editor("mapping", "User mappings", "Find accounts quickly and combine each person’s work in reports.",
                       [_stat("Accounts", len(data)), _stat("Needs mapping", len(missing) + sum(m.get("mapping_type") == "New" for m in dm.user_mapping_full.values())),
                        _stat("Excluded", len(dm.exempt_ids))], _build_mapping_table(data), html.Div([assignment, extra])), "", {"display": "none"}

    @app.callback(Output("mapping-new-person-field", "style"), Input("mapping-assign-person", "value"))
    def show_new_person(person):
        return {} if person == "__new__" else {"display": "none"}

    @app.callback(Output("mapping-assign-account", "options"), Output("mapping-assign-person", "options"),
                  Output("mapping-table", "dropdown"), Input("mapping-table", "data"))
    def update_person_choices(rows):
        table = _build_mapping_table(rows or [])
        return ([{"label": r["id"], "value": r["id"]} for r in rows or [] if r.get("id")],
                person_options(rows) + [{"label": "+ Add a new person", "value": "__new__"}], table.dropdown)

    @app.callback(Output("mapping-table", "filter_query"), Input("mapping-search", "value"), Input("mapping-status-filter", "value"))
    def filter_mapping(search, status):
        clauses = []
        if search and search.strip():
            query = json.dumps(search.strip())
            clauses.append(f'({{id}} contains {query} || {{mapped_user}} contains {query})')
        if status and status != "all":
            clauses.append(f'{{mapping_type}} = {json.dumps(status)}')
        return " && ".join(clauses)

    @app.callback(Output("mapping-table", "data"), Output("mapping-search", "value"), Output("mapping-status-filter", "value"),
                  Output("mapping-assign-feedback", "children"),
                  Input("btn-add-mapping", "n_clicks"), Input("btn-apply-mapping", "n_clicks"),
                  State("mapping-table", "data"), State("mapping-assign-account", "value"),
                  State("mapping-assign-person", "value"), State("mapping-new-person", "value"), prevent_initial_call=True)
    def add_mapping_row(clicks, apply_clicks, rows, account, person, new_person):
        if ctx.triggered_id == "btn-apply-mapping":
            try:
                updated, name = assign_person(rows, account, person, new_person)
            except ValueError as error:
                return no_update, no_update, no_update, _message(str(error))
            return updated, account, "all", _message(f"{account} mapped to {name}. Save changes to keep this mapping.", "success")
        return [{"id": "", "mapped_user": "", "mapping_type": "Existing"}] + list(rows or []), "", "all", ""

    @app.callback(Output("mapping-save-output", "children"), Input("btn-save-mapping", "n_clicks"),
                  Input("mapping-save-auth", "n_submit"), State("mapping-save-auth", "value"), State("mapping-table", "data"), prevent_initial_call=True)
    def save_mapping(clicks, submits, password, rows):
        if not _authorized(password):
            return _message("Incorrect admin password. Your changes have not been saved.")
        try:
            mappings = validate_mappings(rows)
        except ValueError as error:
            return _message(str(error))
        if not dm.db.save_user_mappings(mappings):
            return _message("Unable to save mappings. Your edits are still here; try again.")
        dm.user_mapping_full = {m["id"]: m for m in mappings}
        dm.user_mapping = {m["id"]: m["mapped_user"] for m in mappings}
        dm.exempt_ids = {m["id"] for m in mappings if m["mapping_type"] == "Exempt"}
        dm.invalidate_reporting_caches()
        try:
            (PROJECT_ROOT / "config" / "user_mapping.json").write_text(json.dumps(dm.user_mapping, indent=2), encoding="utf-8")
        except OSError:
            return _message("Mappings saved. The local backup could not be updated.", "warning")
        return _message(f"Saved {len(mappings)} account mappings.", "success")
