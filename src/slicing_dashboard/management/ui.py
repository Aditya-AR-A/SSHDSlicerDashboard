import os
import json
from datetime import datetime
from dash import dcc, html, Input, Output, State, dash_table
import dash_bootstrap_components as dbc
import pandas as pd
from slicing_dashboard.config import PROJECT_ROOT, get_settings


def _get_admin_password() -> str:
    pwd = os.environ.get("SLICER_ADMIN_PASSWORD")
    if not pwd:
        try:
            pwd = get_settings().slicer_admin_password
        except Exception:
            pass
    return (pwd or "").strip()


def _render_auth_card(title: str, subtitle: str, input_id: str, btn_id: str, error_id: str):
    """Render an auth card with glassmorphism and icons."""
    return html.Div(
        [
            html.Div(
                [
                    html.I(className="bi bi-shield-lock-fill"),
                ],
                className="mgmt-lock-icon",
            ),
            html.H4(title, className="fw-bold mb-2 text-primary"),
            html.P(subtitle, className="text-secondary small mb-4"),
            dbc.InputGroup(
                [
                    dbc.InputGroupText(
                        html.I(className="bi bi-key-fill text-muted"),
                        style={"background": "transparent", "borderColor": "var(--glass-border)"},
                    ),
                    dbc.Input(
                        id=input_id,
                        type="password",
                        placeholder="Enter Master Admin Password...",
                        style={
                            "background": "var(--glass-card-bg)",
                            "color": "var(--text-primary)",
                            "borderColor": "var(--glass-border)",
                            "borderRadius": "0 8px 8px 0",
                        },
                    ),
                ],
                className="mb-3",
            ),
            dbc.Button(
                [
                    html.I(className="bi bi-unlock-fill me-2"),
                    "Unlock Controls",
                ],
                id=btn_id,
                color="primary",
                className="w-100 py-2 fw-semibold shadow-sm",
                style={"background": "var(--accent-gradient)", "border": "none"},
            ),
            html.Div(id=error_id, className="mt-3"),
        ],
        className="mgmt-auth-card",
    )


def layout_settlement_management(is_dark: bool = True):
    return html.Div(
        [
            html.Div(
                id="settlement-container",
                children=[
                    _render_auth_card(
                        "Settlement Period Management",
                        "Enter the master password to view, create, or update settlement billing periods.",
                        "settlement-auth-input",
                        "btn-unlock-settlement",
                        "settlement-auth-error",
                    ),
                    html.Div(id="settlement-content"),
                ],
            )
        ],
        className="mgmt-container p-2",
    )


def layout_user_mapping(is_dark: bool = True):
    return html.Div(
        [
            html.Div(
                id="mapping-container",
                children=[
                    _render_auth_card(
                        "User Mapping Management",
                        "Enter the master password to map SSHD slicer user IDs and identify unassigned accounts.",
                        "mapping-auth-input",
                        "btn-unlock-mapping",
                        "mapping-auth-error",
                    ),
                    html.Div(id="mapping-content"),
                ],
            )
        ],
        className="mgmt-container p-2",
    )


def _build_settlement_table(periods: list[dict], is_dark: bool):
    data = []
    for p in periods:
        data.append({
            "start_date": p.get("start_date", ""),
            "end_date": p.get("end_date", ""),
            "settled": "Yes" if p.get("settled", False) else "No",
        })

    bg_cell = "rgba(15, 23, 42, 0.75)" if is_dark else "rgba(255, 255, 255, 0.85)"
    bg_header = "#1e293b" if is_dark else "#f1f5f9"
    fg_text = "#f8fafc" if is_dark else "#0f172a"
    fg_header = "#f8fafc" if is_dark else "#1e293b"
    border_col = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"

    return dash_table.DataTable(
        id="settlement-table",
        columns=[
            {"name": "Start Date (YYYY-MM-DD)", "id": "start_date"},
            {"name": "End Date (YYYY-MM-DD)", "id": "end_date"},
            {"name": "Settled (Yes / No)", "id": "settled", "presentation": "dropdown"},
        ],
        data=data,
        editable=True,
        row_deletable=True,
        sort_action="native",
        filter_action="native",
        dropdown={
            "settled": {
                "options": [
                    {"label": "Yes (Settled)", "value": "Yes"},
                    {"label": "No (Active/Unsettled)", "value": "No"},
                ]
            }
        },
        style_table={"overflowX": "auto", "maxHeight": "440px", "borderRadius": "10px"},
        style_header={
            "backgroundColor": bg_header,
            "color": fg_header,
            "fontWeight": "700",
            "fontSize": "12px",
            "textTransform": "uppercase",
            "letterSpacing": "0.5px",
            "border": "none",
            "padding": "10px 14px",
            "fontFamily": "Inter, sans-serif",
        },
        style_cell={
            "backgroundColor": bg_cell,
            "color": fg_text,
            "border": f"1px solid {border_col}",
            "padding": "8px 14px",
            "fontSize": "13px",
            "fontFamily": "Inter, sans-serif",
            "textAlign": "left",
        },
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "rgba(30, 41, 59, 0.45)" if is_dark else "rgba(241, 245, 249, 0.6)"},
            {
                "if": {"filter_query": '{settled} = "Yes"'},
                "backgroundColor": "rgba(34, 197, 94, 0.14)",
                "color": "#4ade80" if is_dark else "#15803d",
                "fontWeight": "600",
            },
            {
                "if": {"filter_query": '{settled} = "No"'},
                "backgroundColor": "rgba(59, 130, 246, 0.12)",
                "color": "#60a5fa" if is_dark else "#1d4ed8",
                "fontWeight": "600",
            },
        ],
    )


def _build_mapping_table(mappings: list[dict], is_dark: bool):
    bg_cell = "rgba(15, 23, 42, 0.75)" if is_dark else "rgba(255, 255, 255, 0.85)"
    bg_header = "#1e293b" if is_dark else "#f1f5f9"
    fg_text = "#f8fafc" if is_dark else "#0f172a"
    fg_header = "#f8fafc" if is_dark else "#1e293b"
    border_col = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"

    return dash_table.DataTable(
        id="mapping-table",
        columns=[
            {"name": "SSHD User Account / ID", "id": "id"},
            {"name": "Mapped Canonical Slicer Name", "id": "mapped_user"},
            {"name": "Mapping Type / Status", "id": "mapping_type", "presentation": "dropdown"},
        ],
        data=mappings,
        editable=True,
        row_deletable=True,
        filter_action="native",
        sort_action="native",
        page_size=30,
        dropdown={
            "mapping_type": {
                "options": [
                    {"label": "Existing (Active)", "value": "Existing"},
                    {"label": "New (Unassigned)", "value": "New"},
                    {"label": "Exempt (Exclude from totals)", "value": "Exempt"},
                ]
            }
        },
        style_table={"overflowX": "auto", "maxHeight": "480px", "borderRadius": "10px"},
        style_header={
            "backgroundColor": bg_header,
            "color": fg_header,
            "fontWeight": "700",
            "fontSize": "12px",
            "textTransform": "uppercase",
            "letterSpacing": "0.5px",
            "border": "none",
            "padding": "10px 14px",
            "fontFamily": "Inter, sans-serif",
        },
        style_cell={
            "backgroundColor": bg_cell,
            "color": fg_text,
            "border": f"1px solid {border_col}",
            "padding": "8px 14px",
            "fontSize": "13px",
            "fontFamily": "Inter, sans-serif",
            "textAlign": "left",
        },
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "rgba(30, 41, 59, 0.45)" if is_dark else "rgba(241, 245, 249, 0.6)"},
            {
                "if": {"filter_query": '{mapping_type} = "New"'},
                "backgroundColor": "rgba(245, 158, 11, 0.2)",
                "color": "#fbbf24" if is_dark else "#b45309",
                "fontWeight": "700",
            },
            {
                "if": {"filter_query": '{mapped_user} = "Unassigned"'},
                "backgroundColor": "rgba(245, 158, 11, 0.2)",
                "color": "#fbbf24" if is_dark else "#b45309",
                "fontWeight": "700",
            },
            {
                "if": {"filter_query": '{mapping_type} = "Exempt"'},
                "backgroundColor": "rgba(168, 85, 247, 0.16)",
                "color": "#c084fc" if is_dark else "#7e22ce",
                "fontWeight": "600",
            },
        ],
    )


def register_management_callbacks(app, dm):

    # ── Settlement Management Unlock ──────────────────────────────────────────
    @app.callback(
        [
            Output("settlement-content", "children"),
            Output("settlement-auth-error", "children"),
        ],
        Input("btn-unlock-settlement", "n_clicks"),
        [
            State("settlement-auth-input", "value"),
            State("main-container", "className"),
        ],
        prevent_initial_call=True,
    )
    def unlock_settlement(n_clicks, password, container_class):
        if not n_clicks:
            return None, ""

        correct_pwd = _get_admin_password()
        if not correct_pwd or password != correct_pwd:
            return None, dbc.Alert(
                [
                    html.I(className="bi bi-x-circle-fill me-2"),
                    "Incorrect master password. Access denied.",
                ],
                color="danger",
                className="py-2 small fw-semibold text-start shadow-sm",
            )

        is_dark = "theme-dark" in (container_class or "")
        periods = dm.get_available_periods()
        table = _build_settlement_table(periods, is_dark)

        content = html.Div(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H5(
                                    [
                                        html.I(className="bi bi-calendar2-range me-2 text-primary"),
                                        "Settlement Billing Periods",
                                    ],
                                    className="fw-bold mb-1",
                                ),
                                html.Span(
                                    f"Configured Periods: {len(periods)} | Settled: {sum(1 for p in periods if p.get('settled'))}",
                                    className="text-secondary small fw-medium",
                                ),
                            ]
                        ),
                        dbc.ButtonGroup(
                            [
                                dbc.Button(
                                    [html.I(className="bi bi-plus-lg me-1"), "Add Period"],
                                    id="btn-add-settlement",
                                    color="primary",
                                    size="sm",
                                    className="shadow-sm",
                                ),
                            ]
                        ),
                    ],
                    className="mgmt-header-banner",
                ),
                table,
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span("Save Authorization", className="fw-bold small d-block mb-1 text-primary"),
                                html.Span("Enter master password to commit settlement changes to database:", className="text-secondary small"),
                            ]
                        ),
                        html.Div(
                            [
                                dbc.Input(
                                    id="settlement-save-auth",
                                    type="password",
                                    placeholder="Enter password to save...",
                                    size="sm",
                                    style={
                                        "width": "240px",
                                        "background": "var(--glass-card-bg)",
                                        "color": "var(--text-primary)",
                                        "borderColor": "var(--glass-border)",
                                    },
                                    className="me-2",
                                ),
                                dbc.Button(
                                    [html.I(className="bi bi-check2-circle me-1"), "Save Changes"],
                                    id="btn-save-settlement",
                                    color="success",
                                    size="sm",
                                    className="fw-semibold px-3 shadow-sm",
                                ),
                            ],
                            className="d-flex align-items-center",
                        ),
                    ],
                    className="mgmt-save-toolbar",
                ),
                html.Div(id="settlement-save-output", className="mt-2"),
            ],
            className="mt-3",
        )
        return content, ""

    @app.callback(
        Output("settlement-table", "data"),
        Input("btn-add-settlement", "n_clicks"),
        State("settlement-table", "data"),
        prevent_initial_call=True,
    )
    def add_settlement_row(n_clicks, rows):
        if rows is None:
            rows = []
        if n_clicks and n_clicks > 0:
            rows.insert(0, {"start_date": "", "end_date": "", "settled": "No"})
        return rows

    @app.callback(
        Output("settlement-save-output", "children"),
        Input("btn-save-settlement", "n_clicks"),
        [
            State("settlement-save-auth", "value"),
            State("settlement-table", "data"),
        ],
        prevent_initial_call=True,
    )
    def save_settlement(n_clicks, password, rows):
        if not n_clicks:
            return ""
        correct_pwd = _get_admin_password()
        if not correct_pwd or password != correct_pwd:
            return dbc.Alert("Incorrect password. Changes not saved.", color="danger", className="py-2 small fw-semibold")

        if not rows:
            return dbc.Alert("No rows to save.", color="warning", className="py-2 small")

        valid_periods = []
        for r in rows:
            s_date = str(r.get("start_date", "")).strip()
            e_date = str(r.get("end_date", "")).strip()
            if not s_date or not e_date:
                continue
            try:
                s_dt = datetime.strptime(s_date, "%Y-%m-%d")
                e_dt = datetime.strptime(e_date, "%Y-%m-%d")
                if e_dt < s_dt:
                    return dbc.Alert(f"Invalid date order: {s_date} is after {e_date}", color="danger", className="py-2 small")
            except Exception:
                return dbc.Alert(f"Invalid date format for '{s_date}' or '{e_date}'. Use YYYY-MM-DD.", color="danger", className="py-2 small")

            valid_periods.append({
                "start_date": s_date,
                "end_date": e_date,
                "settled": True if r.get("settled") in ["Yes", True] else False,
            })

        success = dm.db.save_settlement_periods(valid_periods)
        if success:
            dm.settlement_periods = dm.db.load_settlement_periods()
            return dbc.Alert(f"Successfully saved {len(valid_periods)} settlement periods to database!", color="success", className="py-2 small fw-semibold")
        return dbc.Alert("Database error saving settlement periods.", color="danger", className="py-2 small")

    # ── User Mapping Unlock & Management ─────────────────────────────────────
    @app.callback(
        [
            Output("mapping-content", "children"),
            Output("mapping-auth-error", "children"),
        ],
        Input("btn-unlock-mapping", "n_clicks"),
        [
            State("mapping-auth-input", "value"),
            State("main-container", "className"),
        ],
        prevent_initial_call=True,
    )
    def unlock_mapping(n_clicks, password, container_class):
        if not n_clicks:
            return None, ""

        correct_pwd = _get_admin_password()
        if not correct_pwd or password != correct_pwd:
            return None, dbc.Alert(
                [
                    html.I(className="bi bi-x-circle-fill me-2"),
                    "Incorrect master password. Access denied.",
                ],
                color="danger",
                className="py-2 small fw-semibold text-start shadow-sm",
            )

        is_dark = "theme-dark" in (container_class or "")

        # Detect unassigned users from all data sources
        unassigned_ids = dm.get_unassigned_users(force_refresh=True)

        unassigned_rows = []
        for uid in unassigned_ids:
            unassigned_rows.append({
                "id": uid,
                "mapped_user": "Unassigned",
                "mapping_type": "New",
            })

        # Put unassigned accounts at top of mapping table
        data = unassigned_rows + list(dm.user_mapping_full.values())
        table = _build_mapping_table(data, is_dark)

        # Unassigned notification banner
        if unassigned_ids:
            unassigned_banner = html.Div(
                [
                    html.Div(
                        [
                            html.I(className="bi bi-exclamation-triangle-fill text-warning me-2 fs-5"),
                            html.Span(
                                f"{len(unassigned_ids)} Unassigned Account{'s' if len(unassigned_ids) > 1 else ''} Detected: ",
                                className="fw-bold me-2 text-warning",
                            ),
                            html.Span(
                                [
                                    html.Span(uid, className="mgmt-unassigned-badge me-1")
                                    for uid in unassigned_ids
                                ]
                            ),
                        ],
                        className="d-flex align-items-center flex-wrap",
                    ),
                    html.Span(
                        "These IDs are prepended to the table below. Set their Canonical Name and Save.",
                        className="small text-secondary fw-medium",
                    ),
                ],
                className="mgmt-unassigned-banner",
            )
        else:
            unassigned_banner = html.Div(
                [
                    html.Div(
                        [
                            html.I(className="bi bi-check-circle-fill text-success me-2 fs-5"),
                            html.Span("All active SSHD accounts are mapped! No unassigned IDs found.", className="fw-semibold text-success small"),
                        ],
                        className="d-flex align-items-center",
                    )
                ],
                className="p-2 mb-3 rounded glass-card",
                style={"borderLeft": "4px solid #10b981"},
            )

        content = html.Div(
            [
                html.Div(
                    [
                        html.Div(
                            [
                                html.H5(
                                    [
                                        html.I(className="bi bi-people-fill me-2 text-primary"),
                                        "SSHD Slicer User Mappings",
                                    ],
                                    className="fw-bold mb-1",
                                ),
                                html.Span(
                                    f"Total Active Mappings: {len(dm.user_mapping_full)} | Unassigned: {len(unassigned_ids)} | Exempt Accounts: {len(dm.exempt_ids)}",
                                    className="text-secondary small fw-medium",
                                ),
                            ]
                        ),
                        dbc.ButtonGroup(
                            [
                                dbc.Button(
                                    [html.I(className="bi bi-plus-lg me-1"), "Add Row"],
                                    id="btn-add-mapping",
                                    color="primary",
                                    size="sm",
                                    className="shadow-sm",
                                ),
                            ]
                        ),
                    ],
                    className="mgmt-header-banner",
                ),
                unassigned_banner,
                table,
                html.Div(
                    [
                        html.Div(
                            [
                                html.Span("Save Authorization", className="fw-bold small d-block mb-1 text-primary"),
                                html.Span("Enter master password to commit user mapping changes:", className="text-secondary small"),
                            ]
                        ),
                        html.Div(
                            [
                                dbc.Input(
                                    id="mapping-save-auth",
                                    type="password",
                                    placeholder="Enter password to save...",
                                    size="sm",
                                    style={
                                        "width": "240px",
                                        "background": "var(--glass-card-bg)",
                                        "color": "var(--text-primary)",
                                        "borderColor": "var(--glass-border)",
                                    },
                                    className="me-2",
                                ),
                                dbc.Button(
                                    [html.I(className="bi bi-check2-circle me-1"), "Save Changes"],
                                    id="btn-save-mapping",
                                    color="success",
                                    size="sm",
                                    className="fw-semibold px-3 shadow-sm",
                                ),
                            ],
                            className="d-flex align-items-center",
                        ),
                    ],
                    className="mgmt-save-toolbar",
                ),
                html.Div(id="mapping-save-output", className="mt-2"),
            ],
            className="mt-3",
        )
        return content, ""

    @app.callback(
        Output("mapping-table", "data"),
        Input("btn-add-mapping", "n_clicks"),
        State("mapping-table", "data"),
        prevent_initial_call=True,
    )
    def add_mapping_row(n_clicks, rows):
        if rows is None:
            rows = []
        if n_clicks and n_clicks > 0:
            rows.insert(0, {"id": "", "mapped_user": "", "mapping_type": "Existing"})
        return rows

    @app.callback(
        Output("mapping-save-output", "children"),
        Input("btn-save-mapping", "n_clicks"),
        [
            State("mapping-save-auth", "value"),
            State("mapping-table", "data"),
        ],
        prevent_initial_call=True,
    )
    def save_mapping(n_clicks, password, rows):
        if not n_clicks:
            return ""
        correct_pwd = _get_admin_password()
        if not correct_pwd or password != correct_pwd:
            return dbc.Alert("Incorrect password. Changes not saved.", color="danger", className="py-2 small fw-semibold")

        if not rows:
            return dbc.Alert("No rows to save.", color="warning", className="py-2 small")

        valid_mappings = []
        seen_ids = set()
        for r in rows:
            uid = str(r.get("id", "")).strip()
            if not uid:
                continue
            if uid in seen_ids:
                return dbc.Alert(f"Duplicate User ID encountered: '{uid}'. Each account ID must be unique.", color="danger", className="py-2 small")
            seen_ids.add(uid)
            m_user = str(r.get("mapped_user", "")).strip()
            m_type = str(r.get("mapping_type", "Existing")).strip()
            if not m_user:
                m_user = "Unassigned"
            valid_mappings.append({
                "id": uid,
                "mapped_user": m_user,
                "mapping_type": m_type,
            })

        success = dm.db.save_user_mappings(valid_mappings)
        if success:
            db_mappings = dm.db.load_user_mappings()
            dm.user_mapping_full = {m["id"]: m for m in db_mappings}
            dm.user_mapping = {m["id"]: m["mapped_user"] for m in db_mappings}
            dm.exempt_ids = {m["id"] for m in db_mappings if m.get("mapping_type") == "Exempt"}

            # Sync to local config/user_mapping.json for disk persistence
            try:
                mapping_path = PROJECT_ROOT / "config" / "user_mapping.json"
                with open(mapping_path, "w", encoding="utf-8") as f:
                    json.dump(dm.user_mapping, f, indent=2)
            except Exception as e:
                print(f"Warning: Failed to sync user_mapping.json: {e}")

            return dbc.Alert(f"Successfully saved and synchronized {len(valid_mappings)} user mappings to MongoDB and config!", color="success", className="py-2 small fw-semibold")
        return dbc.Alert("Database error saving user mappings.", color="danger", className="py-2 small")
