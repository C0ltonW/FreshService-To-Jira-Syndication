from pydantic import BaseModel, Field


class JiraFieldRef(BaseModel):
    """represents a Jira field reference, either by ID or custom field ID."""
    ref: str = Field(...)


class JiraFields(BaseModel):
    """Jira field references."""
    fs_ticket_number: JiraFieldRef
    fs_status: JiraFieldRef
    source: JiraFieldRef
    fs_ticket_type: JiraFieldRef
    urgency: JiraFieldRef
    impact: JiraFieldRef
    group: JiraFieldRef
    department: JiraFieldRef
    ticket_category: JiraFieldRef
    ticket_subcategory: JiraFieldRef
    business_requestor: JiraFieldRef
    requestor_phone: JiraFieldRef
    location_ticket: JiraFieldRef
    user_assigned_category: JiraFieldRef
    user_assigned_subcategory: JiraFieldRef