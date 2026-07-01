from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DepartmentConfig(BaseModel):
    """
    Configuration for a single department's Freshservice → Jira syndication workflow.

    Each department defines:
    - Which Freshservice agents it owns
    - Which Jira project/board to sync to
    - How to route assignments based on category/subcategory
    - How to map status changes (Jira → Freshservice)
    """

    dept_id: str = Field(..., description="Unique department identifier (e.g., 'wms', 'fpna')")
    name: str = Field(..., description="Human-readable department name")

    # Freshservice ownership
    agent_ids: List[int] = Field(
        default_factory=list,
        description="List of Freshservice agent IDs owned by this department"
    )

    # Jira destination
    jira_project_key: str = Field(..., description="Jira project key (e.g., 'FRE', 'FPA')")
    jira_issue_type_id: str = Field(..., description="Jira issue type ID for created issues")
    jira_board_id: int = Field(..., description="Jira board ID for issue queries")
    jira_default_status: str = Field(
        default="Backlog",
        description="Default Jira status for newly created issues"
    )

    # Assignment routing
    assignment_map: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="""
        Hierarchical category → subcategory → assignee mapping.
        Structure: {
            "Category Name": {
                "Subcategory Name": "jira_assignee_email_or_accountid",
                "default": "fallback_assignee"
            }
        }
        """
    )

    # Status synchronization (Jira → Freshservice)
    status_sync_map: Dict[str, str] = Field(
        default_factory=dict,
        description="""
        Map Jira status names to Freshservice status codes.
        Example: {"Done": "4"} means Jira "Done" → Freshservice "Resolved" (code 4)
        """
    )

    # Optional: department-specific behavior overrides
    enabled: bool = Field(default=True, description="Whether this department is active")
    sync_comments: bool = Field(default=True, description="Enable comment synchronization")
    sync_attachments: bool = Field(default=True, description="Enable attachment synchronization")

    def get_assignee(self, category: Optional[str], subcategory: Optional[str]) -> Optional[str]:
        """
        Resolve assignee for a given category/subcategory combination.

        Precedence:
        1. Exact category + subcategory match
        2. Category default (if "default" key exists)
        3. None (caller should handle fallback)

        Args:
            category: Freshservice ticket category
            subcategory: Freshservice ticket subcategory

        Returns:
            Jira assignee identifier (email or accountId), or None if no match
        """
        if not category:
            return None

        def _norm(s: Optional[str]) -> str:
            return (s or "").strip().lower()

        cat_norm = _norm(category)
        sub_norm = _norm(subcategory)

        # Find matching category key (case-insensitive)
        matched_cat_key = None
        for cat_key in self.assignment_map.keys():
            if _norm(cat_key) == cat_norm:
                matched_cat_key = cat_key
                break

        if matched_cat_key is None:
            return None

        sub_map = self.assignment_map.get(matched_cat_key, {})

        # Try exact subcategory match (case-insensitive)
        for sub_key, assignee in sub_map.items():
            if sub_key != "default" and _norm(sub_key) == sub_norm:
                return assignee

        # Fallback to category default
        return sub_map.get("default")

    def get_fs_status_for_jira_status(self, jira_status: str) -> Optional[str]:
        """
        Get Freshservice status code for a given Jira status name.

        Args:
            jira_status: Jira status name (e.g., "Done")

        Returns:
            Freshservice status code as string (e.g., "4" for Resolved), or None
        """
        return self.status_sync_map.get(jira_status)
