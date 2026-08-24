from __future__ import annotations
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import json
from typing import Optional, Dict, List, Set
import logging

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    App settings: credentials/env + mappings.json content.
    All mappings are loaded from JSON only.
    """
    # --- Freshservice ---
    freshservice_domain: str
    freshservice_api_key: str
    fresh_test_ticket: int
    fs_status_include: List[int]

    # --- Jira ---
    jira_domain: str
    jira_email: str
    jira_api_token: str
    jira_project_key: str
    jira_issue_type_id: str
    jira_board_id: int

    # --- Behavior ---
    is_test: bool
    sync_test_ticket: bool = False  # If False, runs in dry-run mode (no actual API writes)
    test_ticket_id: Optional[int] = None  # Specific ticket ID to test (overrides fresh_test_ticket)
    sync_always_create: bool
    agents_to_sync: List[int]
    cutoff_period: int
    jira_status: str
    allow_closed_tickets: bool

    # --- Where to read mappings from ---
    mappings_path: str = "./settings/mappings.json"
    departments_path: str = "./settings/departments.json"

    # --- Loaded from mappings.json ---
    FRIENDLY_LABELS: Dict[str, str] = {}
    DEFAULT_SUPPRESS_KEYS: Set[str] = set()
    FS_DEPARTMENT_NAME_MAP: Dict[int, str] = {}
    CATEGORY_AGENT_MAP: Dict[str, str] = {}
    CATEGORY_SUBCATEGORY_AGENT_MAP: Dict[str, Dict[str, str]] = {}

    FS_STATUS_NAME_MAP: Dict[str, str] = {}
    FS_URGENCY_NAME_MAP: Dict[str, str] = {}
    FS_IMPACT_NAME_MAP: Dict[str, str] = {}
    FS_SOURCE_NAME_MAP: Dict[str, str] = {}

    STATUS_TARGET: Dict[str, str] = {}
    PRIORITY_MAP: Dict[str, str] = {}

    JIRA_FIELDS: Dict[str, str] = {}

    model_config = SettingsConfigDict(
        env_file="./settings/settings.env",
        env_file_encoding="utf-8"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.load_mappings()

    def load_mappings(self):
        path = Path(self.mappings_path)
        if not path.exists():
            raise FileNotFoundError(f"Mapping file not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # Friendly mapping
        self.FRIENDLY_LABELS = data.get("FRIENDLY_LABELS", {})
        self.DEFAULT_SUPPRESS_KEYS = set(data.get("DEFAULT_SUPPRESS_KEYS", []))

        # FreshService -> friendly name maps
        self.FS_STATUS_NAME_MAP   = data.get("FS_STATUS_NAME_MAP", {})
        self.FS_URGENCY_NAME_MAP  = data.get("FS_URGENCY_NAME_MAP", {})
        self.FS_IMPACT_NAME_MAP   = data.get("FS_IMPACT_NAME_MAP", {})
        self.FS_SOURCE_NAME_MAP   = data.get("FS_SOURCE_NAME_MAP", {})

        # Departments and category to assignee
        self.FS_DEPARTMENT_NAME_MAP = {int(k): v for k, v in data.get("FS_DEPARTMENT_NAME_MAP", {}).items()}
        self.CATEGORY_AGENT_MAP     = data.get("CATEGORY_AGENT_MAP", {})
        self.CATEGORY_SUBCATEGORY_AGENT_MAP = data.get("CATEGORY_SUBCATEGORY_AGENT_MAP", {}) or {}

        # Workflow and priority
        self.STATUS_TARGET = data.get("STATUS_TARGET", {})
        self.PRIORITY_MAP  = data.get("PRIORITY_MAP", {})

        # Jira field references
        self.JIRA_FIELDS = data.get("JIRA_FIELDS", {})

    def load_departments(self) -> List[Dict]:
        """
        Load department configurations from departments.json.

        Returns:
            List of department configuration dictionaries
        """
        path = Path(self.departments_path)
        if not path.exists():
            logger.warning(
                "Department configuration file not found at %s. "
                "Multi-department mode will not be available.",
                path
            )
            return []

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            departments = data.get("departments", [])
            logger.info("Loaded %d department configuration(s) from %s", len(departments), path)
            return departments
        except Exception as e:
            logger.error("Failed to load department configurations from %s: %s", path, e)
            return []


settings = Settings()