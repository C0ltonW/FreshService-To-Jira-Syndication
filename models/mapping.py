from typing import Optional, Dict, Literal, Any
from pydantic import BaseModel, Field, ConfigDict

from models.jira_fields import JiraFields, JiraFieldRef
from utils.config import settings


class Mapping(BaseModel):
    """Main mapping model"""
    model_config = ConfigDict(extra="ignore")

    # toggles (keep here if you later want to surface JSON-based toggles)
    sync_comments: bool = True
    comment_sync_strategy: Literal["since_last_id", "since_timestamp"] = "since_last_id"

    # value maps (JSON-supplied)
    priority_map: Dict[str, str] = Field(default_factory=dict)
    status_target: Dict[str, str] = Field(default_factory=dict)

    # Jira field refs
    jira: JiraFields

    # FS -> friendly name maps (JSON-supplied)
    fs_status_name_map: Dict[str, str] = Field(default_factory=dict)
    fs_urgency_name_map: Dict[str, str] = Field(default_factory=dict)
    fs_impact_name_map: Dict[str, str] = Field(default_factory=dict)
    fs_department_name_map: Dict[str, str] = Field(default_factory=dict)
    fs_source_name_map: Dict[str, str] = Field(default_factory=dict)

    # optional group mapping (reserved for future)
    group_map: Dict[str, str] = Field(default_factory=dict)
    category_subcategory_agent_map: Dict[str, Dict[str, str]] = Field(default_factory=dict)


def mapping_from_settings(cfg=settings) -> Mapping:
    """
    Build mapping instance from loaded settings.

    Pulls Jira field references and mapping dictionaries from settings.
    """
    jf = cfg.JIRA_FIELDS or {}

    def get_ref(key: str, default_name: str) -> JiraFieldRef:
        # Use default name if key is missing in JSON config
        return JiraFieldRef(ref=jf.get(key, default_name))

    return Mapping(
        jira=JiraFields(
            fs_ticket_number=get_ref("fs_ticket_number", "Freshservice Ticket #"),
            fs_status=get_ref("fs_status", "Freshservice Status"),
            source=get_ref("source", "Source"),
            fs_ticket_type=get_ref("fs_ticket_type", "Freshservice Ticket Type"),
            urgency=get_ref("urgency", "Urgency"),
            impact=get_ref("impact", "Impact"),
            group=get_ref("group", "Group"),
            department=get_ref("department", "Department"),
            ticket_category=get_ref("ticket_category", "Ticket Category"),
            ticket_subcategory=get_ref("ticket_subcategory", "Ticket Subcategory"),
            business_requestor=get_ref("business_requestor", "Business Requestor"),
            requestor_phone=get_ref("requestor_phone", "Requestor's Phone Number"),
            location_ticket=get_ref("location_ticket", "Location - Ticket"),
            user_assigned_category=get_ref("user_assigned_category", "User assigned category"),
            user_assigned_subcategory=get_ref("user_assigned_subcategory", "User assigned subcategory"),
        ),
        priority_map=cfg.PRIORITY_MAP or {},
        status_target=cfg.STATUS_TARGET or {},
        fs_status_name_map=cfg.FS_STATUS_NAME_MAP or {},
        fs_urgency_name_map=cfg.FS_URGENCY_NAME_MAP or {},
        fs_impact_name_map=cfg.FS_IMPACT_NAME_MAP or {},
        fs_department_name_map={str(k): v for k, v in (cfg.FS_DEPARTMENT_NAME_MAP or {}).items()},
        fs_source_name_map=cfg.FS_SOURCE_NAME_MAP or {},
        group_map={},
        category_subcategory_agent_map=cfg.CATEGORY_SUBCATEGORY_AGENT_MAP or {},
    )


def resolve_field_ids(jira_client, jira_fields: JiraFields) -> Dict[str, str]:
    """
    Resolve Jira field names to their internal field IDs.

    If reference starts with "customfield_", it is used as-is.
    Otherwise, it is looked up via Jira's /field API.
    """
    resp = jira_client.create_request("/field", "GET")
    raw_fields = resp.json()
    name_to_id = {f.get("name"): f.get("id") for f in raw_fields if f.get("name") and f.get("id")}
    resolved: Dict[str, str] = {}

    # Safely extract 'ref' from each field, defaulting to empty string
    data = jira_fields.model_dump()
    for logical_key, ref_obj in data.items():
        name_or_id: str = (ref_obj or {}).get("ref", "")
        if not name_or_id:
            continue
        if name_or_id.startswith("customfield_"):
            resolved[logical_key] = name_or_id
        else:
            fid = name_to_id.get(name_or_id)
            if fid:
                resolved[logical_key] = fid
    return resolved


def desired_jira_status(mapping: Mapping, fs_status_code: Optional[int]) -> Optional[str]:
    """Map FreshService status code to Jira status name."""
    if fs_status_code is None:
        return None
    return mapping.status_target.get(str(fs_status_code))