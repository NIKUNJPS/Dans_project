"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")


class Settings:
    # Mongo
    mongo_url: str = os.environ["MONGO_URL"]
    db_name: str = os.environ["DB_NAME"]

    # CORS
    cors_origins: str = os.environ.get("CORS_ORIGINS", "*")

    # JWT
    jwt_secret_key: str = os.environ.get("JWT_SECRET_KEY", "change-me")
    jwt_algorithm: str = os.environ.get("JWT_ALGORITHM", "HS256")
    access_token_expire_minutes: int = int(
        os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
    )
    refresh_token_expire_days: int = int(
        os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "30")
    )

    # OTP
    otp_expiry_seconds: int = int(os.environ.get("OTP_EXPIRY_SECONDS", "300"))
    otp_length: int = int(os.environ.get("OTP_LENGTH", "6"))

    # LLM
    emergent_llm_key: str = os.environ.get("EMERGENT_LLM_KEY", "")
    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")

    # Email — Resend (preferred) then SMTP fallback
    resend_api_key: str = os.environ.get("RESEND_API_KEY", "")
    smtp_host: str = os.environ.get("SMTP_HOST", "")
    smtp_port: int = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user: str = os.environ.get("SMTP_USER", "")
    smtp_pass: str = os.environ.get("SMTP_PASS", "")
    smtp_from: str = os.environ.get("SMTP_FROM", "noreply@4xstruct.com")
    smtp_from_name: str = os.environ.get("SMTP_FROM_NAME", "STRUCTMIND")

    # URLs
    frontend_url: str = os.environ.get("FRONTEND_URL", "http://localhost:3000")

    # Admin seed
    admin_email: str = os.environ.get("ADMIN_EMAIL", "")
    admin_password: str = os.environ.get("ADMIN_PASSWORD", "")
    admin_first_name: str = os.environ.get("ADMIN_FIRST_NAME", "Admin")
    admin_last_name: str = os.environ.get("ADMIN_LAST_NAME", "User")

    # Uploads
    upload_dir: str = os.environ.get("UPLOAD_DIR", "/app/backend/uploads")
    max_upload_mb: int = int(os.environ.get("MAX_UPLOAD_MB", "500"))

    @property
    def llm_key(self) -> str:
        return self.gemini_api_key or self.emergent_llm_key


settings = Settings()
Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
