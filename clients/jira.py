from __future__ import annotations

import re
from typing import Dict, Optional, Any, List, Union
import logging
import requests
from requests import Response
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models.jira_issue_model import JiraIssue
from utils.exceptions import JiraAPIError

logger = logging.getLogger(__name__)


def _build_session(email: str, api_token: str) -> requests.Session:
    """
    Create requests session for Jira Cloud REST API

    Args:
        email (str): Jira email address
        api_token (str): Jira API token
    Returns:
        requests.Session
    """
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods={"GET", "POST", "PUT", "PATCH", "DELETE"},
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update({"Accept": "application/json"})
    s.auth = (email, api_token)
    return s


def _looks_like_account_id(s: str) -> bool:
    """Regex for Jira account IDs"""
    return bool(s) and ('@' not in s) and (' ' not in s) and bool(re.fullmatch(r'[0-9A-Za-z:-]{8,}', s))



class JiraClient:
    """
    Jira Cloud REST API client with retry and pagination helpers

    Exposes a narrow range of endpoints for use in sync_engine

    """
    # --- Lifecycle --- #
    def __init__(self, domain, email, api_token):
        self.base_url = f"https://{domain}/rest/api/3"
        self.agile_base_url = f"https://{domain}/rest/agile/1.0"
        self.email = email
        self.api_token = api_token
        self._field_name_to_id: Dict[str, str] = None
        self.s = _build_session(email, api_token)

    # --- HTTP helpers --- #

    def create_request(
            self,
            endpoint: str,
            method: str,
            data: Optional[Any] = None,
            params: Optional[Dict[str, Any]] = None,
    ) -> Response:
        """
        Performs JSON API request
        """
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.s.request(method=method, url=url, json=data, params=params)
        except Exception as e:
            logger.error("Jira request failed: %s %s -> %s", method, url, e)
            raise
        if resp.status_code // 100 != 2:
            logger.error("Jira API error %s for %s %s: %s", resp.status_code, method, url, resp.text)
            raise JiraAPIError(resp.status_code, resp.text)
        return resp

    def create_agile_request(
        self,
        endpoint: str,
        method: str,
        data: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Response:
        """
        Call Jira Agile REST API (Board/Sprint/Issue listing)

        Args:
            endpoint (str): API endpoint
            method (str): HTTP method
            data (Optional[Any]): JSON payload
            params: (Optional[Dict[str, Any]]): URL query params
        Returns:
            Response
        Raises:
            JiraAPIError
        """
        url = f"{self.agile_base_url}{endpoint}"
        try:
            resp = self.s.request(method=method, url=url, json=data, params=params)
        except Exception as e:
            logger.error("Jira Agile request failed: %s %s -> %s", method, url, e)
            raise
        if resp.status_code // 100 != 2:
            logger.error("Jira Agile API error %s for %s %s: %s", resp.status_code, method, url, resp.text)
            raise JiraAPIError(resp.status_code, resp.text)
        return resp

    # --- ADF helper --- #
    def _to_adf(self, text: str) -> Dict:
        """
        Convert plain text to Jira ADF format
        """
        return {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        }

    # --- API wrappers --- #
    def get_account_id(self, query: str) -> Optional[str]:
        """Resolve a Jira accountId from an email, name, or accountId string.

        If the query already looks like a Jira accountId, it is returned as-is.
        Otherwise, searches users and returns the first matching accountId.
        """
        if not query:
            return None
        if _looks_like_account_id(query):
            return query
        try:
            # Use the plural endpoint which supports pagination/filters in v3
            resp = self.create_request("/users/search", "GET", params={"query": query, "maxResults": 50})
            users = resp.json() or []
            if not isinstance(users, list):
                return None
            # Prefer exact email match if the query is an email
            if '@' in query:
                ql = query.lower()
                for u in users:
                    email = (u.get("emailAddress") or "").lower()
                    if email == ql:
                        return u.get("accountId")
            # Otherwise, return the first candidate's accountId
            return users[0].get("accountId") if users else None
        except JiraAPIError:
            return None

    def list_all_users(self, max_results: int = 100) -> List[Dict[str, Any]]:
        """Return all users visible to the API, paginated via /users/search."""
        all_users: List[Dict[str, Any]] = []
        start_at = 0
        while True:
            try:
                resp = self.create_request(
                    "/users/search", "GET", params={"startAt": start_at, "maxResults": max_results}
                )
            except JiraAPIError:
                break
            page = resp.json() or []
            if not isinstance(page, list) or not page:
                break
            all_users.extend(page)
            if len(page) < max_results:
                break
            start_at += len(page)
        return all_users

    def create_issue(
        self,
        project_key: str,
        issue_type_id: str,
        summary: str,
        description: str,
        priority_id: Optional[str] = None,
        labels: Optional[list] = None,
        assignee_id: Optional[str] = None,
    ) -> JiraIssue:
        """
        Create a Jira issue

        Args:
            project_key (str): Jira project key
            issue_type_id (str): Jira issue type ID
            summary (str): Issue summary
            description (str): Issue description
            priority_id (Optional[str]): Priority ID
            labels (Optional[list]): List of labels
            assignee_id (Optional[str]): Assignee ID
        Returns:
            JiraIssue
        """
        endpoint = "/issue"
        fields: Dict[str, Any] = {
            "project": {"key": project_key},
            "issuetype": {"id": issue_type_id},
            "summary": summary,
            "description": self._to_adf(description) if description else None,
        }
        if priority_id:
            fields["priority"] = {"id": priority_id}
        if labels:
            fields["labels"] = labels
        if assignee_id:
            fields["assignee"] = {"accountId": assignee_id}
        payload = {"fields": fields}
        response = self.create_request(endpoint, "POST", data=payload)
        return JiraIssue.model_validate(response.json())

    def get_board_issues(
        self,
        board_id: Union[int, str],
        jql: Optional[str] = None,
        max_results: int = 50,
        start_at: int = 0,
    ) -> List[JiraIssue]:
        """
        List issues for a given board

        Args:
            board_id (Union[int, str]): Jira board ID
            jql (Optional[str]): JQL query
            max_results (int): Maximum number of results to return
            start_at (int): Index of first result to return
        Returns:
            List[JiraIssue]
        """
        params: Dict[str, Any] = {"maxResults": max_results, "startAt": start_at}
        if jql:
            params["jql"] = jql
        response = self.create_agile_request(f"/board/{board_id}/issue", "GET", params=params)
        payload = response.json()
        items = payload.get("issues", []) if isinstance(payload, dict) else []
        coerced: List[dict] = []
        for issue in items:
            fields = issue.get("fields", {})
            desc = fields.get("description")
            if isinstance(desc, str):
                fields["description"] = self._to_adf(desc)
            coerced.append(issue)
        return [JiraIssue.model_validate(i) for i in coerced]

    def get_boards(self) -> Dict:
        """Returns a list of boards in the Jira instance"""
        response = self.create_agile_request("/board", "GET")
        return response.json()

    def get_issue_types_for_project(self, project_key: str) -> List[Dict[str, str]]:
        """Returns a list of issue types for a given project"""
        endpoint = f"/project/{project_key}"
        response = self.create_request(endpoint, "GET")
        project_data = response.json()
        issue_types = project_data.get("issueTypes", [])
        result = [{"id": it.get("id"), "name": it.get("name")} for it in issue_types if "id" in it and "name" in it]
        logger.info("Found %d issue types for project %s", len(result), project_key)
        for it in result:
            print(f"Issue Type: {it['name']} (ID: {it['id']})")
        return result

    def get_board_configuration(self, board_id: Union[int, str]) -> Dict:
        """Returns a board's configuration"""
        endpoint = f"/board/{board_id}/configuration"
        response = self.create_agile_request(endpoint, "GET")
        config = response.json()
        columns = config.get("columnConfig", {}).get("columns", [])
        logger.info("Board %s has %d columns configured.", board_id, len(columns))
        for col in columns:
            name = col.get("name")
            statuses = [s.get("name") for s in col.get("statuses", [])]
            print(f"Column: {name} → Statuses: {statuses}")
        return config

    def get_issue(self, issue_key: str) -> JiraIssue:
        """Get a single issue by key"""
        endpoint = f"/issue/{issue_key}"
        response = self.create_request(endpoint, "GET")
        return JiraIssue.model_validate(response.json())

    def update_issue(self, issue_key: str, fields: Dict[str, Any]) -> JiraIssue:
        """Update fields on a single issue by key"""
        endpoint = f"/issue/{issue_key}"
        payload = {"fields": fields}
        self.create_request(endpoint, "PUT", data=payload)
        return self.get_issue(issue_key)

    def add_comment_to_issue(self, issue_key: str, comment_text: str) -> Dict:
        """
        Add plain text comment to issue
        """
        endpoint = f"/issue/{issue_key}/comment"
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment_text}]}],
            }
        }
        response = self.create_request(endpoint, "POST", data=payload)
        return response.json()

    # --- Field resolution cache --- #
    def _ensure_fields_cache(self) -> None:
        """Lazy-load field name to ID mapping"""
        if self._field_name_to_id is not None:
            return
        response = self.create_request("/field", "GET")
        fields = response.json()
        self._field_name_to_id = {f.get("name"): f.get("id") for f in fields if f.get("name") and f.get("id")}

    def resolve_field(self, name_or_id: str) -> str:
        """Resolve a field name to its ID"""
        if name_or_id.startswith("customfield_"):
            return name_or_id
        self._ensure_fields_cache()
        return self._field_name_to_id.get(name_or_id)

    # --- Search / JQL --- #
    def search_issues(self, jql: str, max_results: int = 1) -> List[JiraIssue]:
        """Search for issues using JQL"""
        payload = {"jql": jql, "maxResults": max_results}
        response = self.create_request("/search/jql", "POST", data=payload)
        data = response.json()
        return [JiraIssue.model_validate(i) for i in data.get("issues", [])]


    # --- Transitions --- #
    def get_transitions(self, issue_key: str) -> List[Dict]:
        """List available transitions for an issue by key"""
        response = self.create_request(f"/issue/{issue_key}/transitions", "GET")
        return response.json().get("transitions", [])

    def transition_issue(self, issue_key: str, transition_id: str) -> None:
        """Transition an issue to a new status"""
        self.create_request(f"/issue/{issue_key}/transitions", "POST", data={"transition": {"id": transition_id}})


    # --- Idempotent lookup --- #
    def find_by_fs_ticket(self, project_key: str, fs_ticket_num: str, fs_field_name_or_id: str) -> Optional[str]:
        """Find Jira issue key for a given FreshService ticket number and field name"""
        field_id = self.resolve_field(fs_field_name_or_id)
        if field_id:
            jql = (
                f'project = "{project_key}" AND "{fs_field_name_or_id}" ~ "{fs_ticket_num}" '
                f"ORDER BY created DESC"
            )
            issues = self.search_issues(jql, max_results=1)
            if issues:
                return issues[0].key
        jql = f'project = "{project_key}" AND summary ~ "[FS-{fs_ticket_num}]" ORDER BY created DESC'
        issues = self.search_issues(jql, max_results=1)
        return issues[0].key if issues else None


    # --- Attachments --- #
    def add_attachment(self, issue_key: str, filename: str, data: bytes, content_type: str = None) -> Dict:
        """Upload an attachment to an issue by key"""
        url = f"{self.base_url}/issue/{issue_key}/attachments"
        headers = {
            "X-Atlassian-Token": "no-check",
            "Accept": "application/json",
        }
        files = {
            "file": (filename, data, content_type or "application/octet-stream")
        }
        resp = self.s.post(url, headers=headers, files=files, auth=(self.email, self.api_token))
        if resp.status_code // 100 != 2:
            logger.error("Jira attachment upload failed %s: %s", resp.status_code, resp.text)
            raise JiraAPIError(resp.status_code, resp.text)
        body = resp.json()
        return body[0] if isinstance(body, list) and body else {}

    def get_attachment_settings(self) -> Dict:
        """Get attachment compatibility settings"""
        resp = self.create_request("/attachment/meta", "GET")
        return resp.json()

    # --- EditMeta --- #
    def get_editmeta_fields(self, issue_key: str) -> Dict[str, Any]:
        """Get edit metadata for issue fields"""
        resp = self.create_request(f"/issue/{issue_key}/editmeta", "GET")
        data = resp.json()
        return data.get("fields", {}) or {}
