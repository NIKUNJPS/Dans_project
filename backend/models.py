"""Pydantic models for API I/O. MongoDB docs use these same shapes (id is UUID str)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


# 3-role architecture. `super_admin` controls everything via feature_permissions.
Role = Literal["super_admin", "detailer", "fabricator"]
SubscriptionTier = Literal["free", "starter", "pro", "enterprise"]


# ---------- AUTH ----------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(min_length=1, max_length=64)
    company: str = Field(default="", max_length=128)
    # Self-signup is restricted to detailer or fabricator. Super admins are seeded only.
    role: Literal["detailer", "fabricator"]
    country: str = Field(default="", max_length=64)


class VerifyOtpRequest(BaseModel):
    user_id: str
    otp: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class VerifyResetOtpRequest(BaseModel):
    reset_token: str
    otp: str


class ResetPasswordRequest(BaseModel):
    reset_session_token: str
    new_password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)
    otp: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserPublic"


class UserPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    email: EmailStr
    first_name: str
    last_name: str
    role: Role
    company: str = ""
    country: str = ""
    phone: str = ""
    avatar_url: str = ""
    is_verified: bool = False
    is_active: bool = True
    subscription_tier: SubscriptionTier = "free"
    created_by: Optional[str] = None
    usage_this_month: dict = Field(default_factory=dict)
    limits: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)


# ---------- PROJECTS ----------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    tags: List[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[Literal["active", "archived", "completed"]] = None
    tags: Optional[List[str]] = None


class TeamMember(BaseModel):
    user_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    role: str
    added_at: datetime = Field(default_factory=now_utc)


class ProjectOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    description: str = ""
    owner_id: str
    owner_name: Optional[str] = None
    team_members: List[TeamMember] = Field(default_factory=list)
    status: Literal["active", "archived", "completed"] = "active"
    tags: List[str] = Field(default_factory=list)
    file_count: int = 0
    analysis_count: int = 0
    rfi_count: int = 0
    created_at: datetime
    updated_at: datetime


# ---------- FILES ----------
class FileOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: Optional[str] = None
    uploaded_by: str
    original_name: str
    storage_key: str
    mime_type: str
    size_bytes: int
    size_mb: float
    processing_status: Literal["pending", "processing", "ready", "error"] = "ready"
    metadata: dict = Field(default_factory=dict)
    blockchain_hash: str = ""
    created_at: datetime


# ---------- ANALYSES ----------
AnalysisStatus = Literal["queued", "processing", "complete", "failed"]


class AnalysisCreate(BaseModel):
    project_id: Optional[str] = None
    file_ids: List[str] = Field(default_factory=list)
    mode: str
    input_text: str = ""


class AnalysisOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    file_ids: List[str] = Field(default_factory=list)
    requested_by: str
    requested_by_name: Optional[str] = None
    mode: str
    mode_label: Optional[str] = None
    status: AnalysisStatus
    model_used: str = ""
    input_text: str = ""
    output_markdown: str = ""
    processing_time_seconds: float = 0
    issues_found: dict = Field(default_factory=lambda: {"critical": 0, "major": 0, "minor": 0, "total": 0})
    quality_score: float = 0
    blockchain_hash: str = ""
    exports: list = Field(default_factory=list)
    error_message: str = ""
    created_at: datetime
    completed_at: Optional[datetime] = None


# ---------- RFIS ----------
RfiStatus = Literal["draft", "sent", "responded", "closed"]
RfiPriority = Literal["critical", "urgent", "standard"]


class RfiCreate(BaseModel):
    project_id: Optional[str] = None
    analysis_id: Optional[str] = None
    subject: str = Field(min_length=1, max_length=256)
    body: str
    priority: RfiPriority = "standard"
    assigned_to: str = ""
    sheet_reference: str = ""
    blocking: bool = False


class RfiUpdate(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    priority: Optional[RfiPriority] = None
    assigned_to: Optional[str] = None
    status: Optional[RfiStatus] = None
    response: Optional[str] = None
    sheet_reference: Optional[str] = None
    blocking: Optional[bool] = None


class RfiOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    analysis_id: Optional[str] = None
    rfi_number: str
    subject: str
    body: str
    priority: RfiPriority
    status: RfiStatus
    assigned_to: str = ""
    response: str = ""
    sheet_reference: str = ""
    blocking: bool = False
    created_by: str
    created_by_name: Optional[str] = None
    created_at: datetime
    responded_at: Optional[datetime] = None


# ---------- NOTIFICATIONS ----------
class NotificationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str
    type: str
    title: str
    message: str
    is_read: bool = False
    action_url: str = ""
    created_at: datetime


# ---------- USAGE ----------
class UsageOut(BaseModel):
    analyses_used: int
    analyses_limit: int
    files_processed: int
    total_file_size_mb: float
    subscription_tier: SubscriptionTier
    period_start: datetime
    period_end: datetime


# ---------- ADMIN ----------
class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=64)
    last_name: str = Field(min_length=1, max_length=64)
    company: str = Field(default="", max_length=128)
    country: str = Field(default="", max_length=64)
    role: Literal["detailer", "fabricator"]


class AdminUserUpdate(BaseModel):
    role: Optional[Role] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None
    subscription_tier: Optional[SubscriptionTier] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    country: Optional[str] = None


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class FeaturePermissionsUpdate(BaseModel):
    """Partial update — any field omitted is left unchanged."""
    analysesPerMonth: Optional[int] = None
    maxFileSizeMb: Optional[int] = None
    maxFilesPerAnalysis: Optional[int] = None
    allowedModes: Optional[List[str]] = None
    allowedExports: Optional[List[str]] = None
    canUploadFiles: Optional[bool] = None
    canCreateProjects: Optional[bool] = None
    canViewHistory: Optional[bool] = None
    canDownloadReports: Optional[bool] = None
    canSendRfis: Optional[bool] = None
    canViewAuditLog: Optional[bool] = None
    blockchainAnchoring: Optional[bool] = None
    canRunEstimation: Optional[bool] = None
    estimationCountries: Optional[List[str]] = None
    showDashboardStats: Optional[bool] = None
    showActivityChart: Optional[bool] = None


class ImpersonateRequest(BaseModel):
    user_id: str


TokenResponse.model_rebuild()
