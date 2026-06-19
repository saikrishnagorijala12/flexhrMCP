import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    ms_client_id: str
    ms_tenant_id: str
    flexhr_url: str
    flexhr_username: str
    flexhr_password: str
    flexhr_employee: str
    flexhr_company: str
    flexhr_api_username: str
    flexhr_api_password: str
    openrouter_api_key: str
    flexhr_work_status: str = "Working From Office"
    openrouter_model: str = "anthropic/claude-sonnet-4-6"
    ms_token_cache: str = ".token_cache.json"
    timezone: str = "UTC"
    onedrive_folder: str = ""
    notes_folder: str = ""
    flexhr_activity_types: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        raw_types = os.environ.get("FLEXHR_ACTIVITY_TYPES", "")
        activity_types = [t.strip() for t in raw_types.split(",") if t.strip()] if raw_types else []
        return cls(
            ms_client_id=os.environ["MS_CLIENT_ID"],
            ms_tenant_id=os.environ.get("MS_TENANT_ID", "common"),
            flexhr_url=os.environ["FLEXHR_URL"].rstrip("/"),
            flexhr_username=os.environ["FLEXHR_USERNAME"],
            flexhr_password=os.environ["FLEXHR_PASSWORD"],
            flexhr_employee=os.environ["FLEXHR_EMPLOYEE"],
            flexhr_company=os.environ["FLEXHR_COMPANY"],
            flexhr_api_username=os.environ.get("FLEXHR_API_USERNAME", os.environ["FLEXHR_USERNAME"]),
            flexhr_api_password=os.environ.get("FLEXHR_API_PASSWORD", os.environ["FLEXHR_PASSWORD"]),
            flexhr_work_status=os.environ.get("FLEXHR_WORK_STATUS", "Working From Office"),
            openrouter_api_key=os.environ["OPENROUTER_API_KEY"],
            openrouter_model=os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-6"),
            ms_token_cache=os.environ.get("MS_TOKEN_CACHE", ".token_cache.json"),
            timezone=os.environ.get("TIMEZONE", "UTC"),
            onedrive_folder=os.environ.get("ONEDRIVE_FOLDER", ""),
            notes_folder=os.environ.get("NOTES_FOLDER", ""),
            flexhr_activity_types=activity_types,
        )
