"""
Pydantic schemas for structured data throughout the pipeline.

These models define the canonical data shapes for:
- Raw extracted records (as they come from the dashboard)
- Processed/normalized records (cleaned data)
- Report summaries
- Extraction metadata

The exact raw schema will be refined during Phase 1 dashboard discovery.
The processed schema below represents the target canonical format.
"""
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    """Possible task completion statuses."""
    SLICING = 'slicing'
    ASSIGNED = 'slice_assigned'
    SUBMITTED = 'slice_submitted'
    PENDING_AUDITOR = 'slice_pending_auditor_review'
    PENDING_ADMIN = 'slice_pending_admin_review'
    REWORK = 'slice_rework'
    COMPLETED = 'slice_completed'
    ERROR_CONFIRMED = 'video_error_confirmed'
    ERROR_REVIEW = 'video_error_review'
    UNKNOWN = 'unknown'


class RawRecord(BaseModel):
    """A single record as extracted from the dashboard, before cleaning.

    Fields here are intentionally loose — the actual schema depends on
    what the dashboard exposes. This model validates structure, not content.
    """
    raw_data: dict[str, Any] = Field(description=
        'Original key-value pairs from the dashboard row')
    extracted_at: datetime = Field(default_factory=datetime.now,
        description='Timestamp when this record was extracted')
    source_page: int = Field(default=1, description=
        'Page number from which this record was extracted')


class ProcessedRecord(BaseModel):
    """A normalized, cleaned record ready for reporting.

    This represents the canonical task-level schema.
    """
    record_id: str = Field(description=
        'Deterministic unique identifier for this record')
    user_id: str = Field(description=
        'Original user identifier from the dashboard')
    user_name: str = Field(description='Normalized/mapped user name')
    video_id: str = Field(default='', description=
        'Video identifier if available')
    video_name: str = Field(default='', description='Video name if available')
    task_id: str = Field(default='', description=
        'Task identifier from dashboard')
    status: TaskStatus = Field(default=TaskStatus.UNKNOWN, description=
        'Task completion status')
    is_completed: bool = Field(default=False, description=
        'True if status indicates completion')
    completion_type: str = Field(default='none', description=
        'normal, error, or none')
    duration_seconds: float = Field(default=0.0, description=
        'Duration in seconds (canonical internal representation)')
    duration_display: str = Field(default='00:00:00', description=
        'Duration formatted as HH:MM:SS for display')
    completed_at: datetime | None = Field(default=None, description=
        'Completion timestamp')
    completed_date: str = Field(default='', description=
        'Completion date as YYYY-MM-DD string')
    completed_week: str = Field(default='', description=
        'ISO week as YYYY-Www string')
    completed_month: str = Field(default='', description=
        'Month as YYYY-MM string')
    source_extracted_at: datetime = Field(default_factory=datetime.now,
        description='When the source data was extracted from the dashboard')
    extra_fields: dict[str, Any] = Field(default_factory=dict, description=
        'Additional source-specific fields preserved from the raw data')


class ExtractionResult(BaseModel):
    """Result of a single extraction run."""
    success: bool = Field(description='Whether extraction succeeded')
    records: list[RawRecord] = Field(default_factory=list, description=
        'Extracted records')
    total_records: int = Field(default=0, description='Total records found')
    pages_extracted: int = Field(default=0, description=
        'Number of pages extracted')
    extracted_at: datetime = Field(default_factory=datetime.now,
        description='Extraction timestamp')
    errors: list[str] = Field(default_factory=list, description=
        'Any errors encountered')
    warnings: list[str] = Field(default_factory=list, description=
        'Any warnings generated')
    metadata: dict[str, Any] = Field(default_factory=dict, description=
        'Additional metadata about the extraction')


class ValidationResult(BaseModel):
    """Result of a data validation check."""
    is_valid: bool = Field(description='Whether validation passed')
    checks_passed: int = Field(default=0, description=
        'Number of checks that passed')
    checks_failed: int = Field(default=0, description=
        'Number of checks that failed')
    errors: list[str] = Field(default_factory=list, description=
        'Validation errors')
    warnings: list[str] = Field(default_factory=list, description=
        'Validation warnings')
    details: dict[str, Any] = Field(default_factory=dict, description=
        'Detailed validation results by check name')


class UserSummary(BaseModel):
    """Aggregated summary for a single user, segmenting normal and error completed tasks."""
    user_name: str
    completed_normal_count: int = 0
    completed_normal_duration_seconds: float = 0.0
    completed_normal_duration_display: str = '00:00:00'
    completed_error_count: int = 0
    completed_error_duration_seconds: float = 0.0
    completed_error_duration_display: str = '00:00:00'
    total_completed_count: int = 0
    total_completed_duration_seconds: float = 0.0
    total_completed_duration_display: str = '00:00:00'
    submitted_count: int = 0
    submitted_duration_seconds: float = 0.0
    submitted_duration_display: str = '00:00:00'
    leader_passed_count: int = 0
    leader_passed_duration_seconds: float = 0.0
    leader_passed_duration_display: str = '00:00:00'
    leader_returned_count: int = 0
    leader_returned_duration_seconds: float = 0.0
    leader_returned_duration_display: str = '00:00:00'
    auditor_passed_count: int = 0
    auditor_passed_duration_seconds: float = 0.0
    auditor_passed_duration_display: str = '00:00:00'
    auditor_returned_count: int = 0
    auditor_returned_duration_seconds: float = 0.0
    auditor_returned_duration_display: str = '00:00:00'
    percentage_contribution: float = 0.0

    @field_validator('completed_normal_duration_display',
        'completed_error_duration_display',
        'total_completed_duration_display', 'submitted_duration_display',
        'leader_passed_duration_display',
        'leader_returned_duration_display',
        'auditor_passed_duration_display',
        'auditor_returned_duration_display', mode='before')
    @classmethod
    def format_duration(cls, v: str, info: Any) ->str:
        """Auto-format duration display from seconds if not provided."""
        if v and v != '00:00:00':
            return v
        return v


class ReportSummary(BaseModel):
    """Summary section of a report (daily/weekly/monthly)."""
    report_type: str = Field(description='daily, weekly, or monthly')
    period_label: str = Field(description='Human-readable period label')
    period_start: str = Field(description='Period start date (YYYY-MM-DD)')
    period_end: str = Field(description='Period end date (YYYY-MM-DD)')
    total_completed_normal_count: int = 0
    total_completed_normal_duration_seconds: float = 0.0
    total_completed_normal_duration_display: str = '00:00:00'
    total_completed_error_count: int = 0
    total_completed_error_duration_seconds: float = 0.0
    total_completed_error_duration_display: str = '00:00:00'
    total_completed_count: int = 0
    total_completed_duration_seconds: float = 0.0
    total_completed_duration_display: str = '00:00:00'
    total_submitted_count: int = 0
    total_submitted_duration_seconds: float = 0.0
    total_submitted_duration_display: str = '00:00:00'
    total_leader_passed_count: int = 0
    total_leader_passed_duration_seconds: float = 0.0
    total_leader_passed_duration_display: str = '00:00:00'
    total_leader_returned_count: int = 0
    total_leader_returned_duration_seconds: float = 0.0
    total_leader_returned_duration_display: str = '00:00:00'
    total_auditor_passed_count: int = 0
    total_auditor_passed_duration_seconds: float = 0.0
    total_auditor_passed_duration_display: str = '00:00:00'
    total_auditor_returned_count: int = 0
    total_auditor_returned_duration_seconds: float = 0.0
    total_auditor_returned_duration_display: str = '00:00:00'
    user_summaries: list[UserSummary] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)


class BillingCycle(BaseModel):
    """A billing/project cycle for settlement tracking."""
    cycle_id: str = Field(description='Unique cycle identifier')
    cycle_start: str = Field(description='Cycle start date (YYYY-MM-DD)')
    cycle_end: str = Field(description='Cycle end date (YYYY-MM-DD)')
    is_finalized: bool = Field(default=False, description=
        'Whether this cycle is locked')
