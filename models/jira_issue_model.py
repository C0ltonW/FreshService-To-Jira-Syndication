from pydantic import BaseModel, Field
from typing import Optional, List, Dict

class JiraIssueType(BaseModel):
    id: Optional[str]
    name: str

class JiraProject(BaseModel):
    key: str

class JiraPriority(BaseModel):
    id: Optional[str]
    name: str

class JiraStatus(BaseModel):
    name: Optional[str]

class JiraIssueFields(BaseModel):
    summary: str
    description: Optional[Dict]
    issuetype: JiraIssueType
    project: JiraProject
    priority: Optional[JiraPriority]
    status: Optional[JiraStatus] = None
    created: Optional[str] = None
    custom_fields: Dict[str, Optional[str]] = Field(default_factory=dict)
    freshservice_ticket_number: Optional[str] = Field(None, description="Ticket Number")
    freshservice_status: Optional[str] = Field(None, description="Status")
    source: Optional[str] = Field(None, description="Source")
    freshservice_ticket_type: Optional[str] = Field(None, description="Type")
    urgency: Optional[str] = Field(None, description="Urgency")
    impact: Optional[str] = Field(None, description="Impact")
    group: Optional[str] = Field(None, description="Group")
    department: Optional[str] = Field(None, description="Department")
    ticket_category: Optional[str] = Field(None, description="Category")
    ticket_subcategory: Optional[str] = Field(None, description="Sub-Category")
    business_requestor: Optional[str] = Field(None, description="Requestor")
    requestors_phone_number: Optional[str] = Field(None, description="Requestor's Contact Number")
    location_ticket: Optional[str] = Field(None, description="Location")
    user_assigned_category: Optional[str] = Field(None, description="How the User categorized the issue")
    user_assigned_subcategory: Optional[str] = Field(None, description="The subcategory the User selected")

class JiraIssue(BaseModel):
    id: Optional[str]
    key: Optional[str]
    fields: Optional[JiraIssueFields] = None
