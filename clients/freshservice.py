from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Union
from urllib.parse import urlparse, quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models.fresh_ticket_model import FreshTicket
from utils.exceptions import FreshserviceAPIError

logger = logging.getLogger(__name__)

DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = 100


def _build_session(api_key: str) -> requests.Session:
    """
    Create a requests session with the Freshservice API key

    Includes:
        Basic auth with FreshService API key
        Retry policy (5 times)
        JSON headers
    Args: api_key (str): FreshService API key
    Returns: requests.Session
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
    s.headers.update({
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    s.auth = (api_key, "X")
    return s


class FreshserviceClient:
    """
    FreshService API client with retry and pagination helpers

    Exposes a narrow range of endpoints for use in sync_engine
    Tickets: get_ticket, get_all_tickets_by_agent, filter_tickets_by_agent
    Agents: get_all_agents, get_agents
    Conversations: get_ticket_conversations
    Requester: get_requester_by_id
    Attachments: download_attachment

    """
    # --- Lifecycle --- #
    def __init__(self, domain: str, api_key: str):
        """
        Initialize client
        Args:
             domain (str): FreshService domain
             api_key (str): FreshService API key
        """
        self.base_url = f"https://{domain}".rstrip("/")
        self.api_key = api_key
        self.s = _build_session(api_key)
        try:
            self._base_host = urlparse(self.base_url).netloc.lower()
        except Exception:
            self._base_host = ""


    # --- Core HTTP helpers --- #
    def create_request(self, endpoint: str, method: str, data: Optional[Any] = None) -> requests.Response:
        """
        Performs JSON API request

        Args:
             endpoint (str): API endpoint
             method (str): HTTP method
             data (Optional[Any]): JSON payload
         Returns:
             requests.Response
         Raises:
             FreshserviceAPIError
        """
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.s.request(method=method, url=url, json=data)
        except Exception as e:
            logger.error("Freshservice request failed: %s %s -> %s", method, url, e)
            raise
        if resp.status_code // 100 != 2:
            logger.error(
                "Freshservice API error %s for %s %s: %s",
                resp.status_code, method, url, resp.text
            )
            raise FreshserviceAPIError(resp.status_code, resp.text)
        return resp


    def _get_json(self, endpoint: str, *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get JSON helper

        Args:
             endpoint (str): API endpoint
             params: (Optional[Dict[str, Any]]): URL query params
         Returns:
             JSON payload or {} if empty
         Raises:
             FreshserviceAPIError
        """
        url = f"{self.base_url}{endpoint}"
        resp = self.s.get(url, params=params or None)
        if resp.status_code // 100 != 2:
            logger.error("Freshservice API error %s for GET %s: %s", resp.status_code, url, resp.text)
            raise FreshserviceAPIError(resp.status_code, resp.text)
        return resp.json() if resp.content else {}


    def _paginate(
        self,
        path: str,
        *,
        item_key: str,
        params: Optional[Dict[str, Any]] = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> Iterable[Dict[str, Any]]:
        """
        Yield paginated results from FreshService endpoints

        Args:
             path (str): API endpoint
             item_key (str): JSON key for items
             params: (Optional[Dict[str, Any]]): URL query params
             per_page (int): Number of items per page
             max_pages (int): Maximum number of pages to fetch
        Returns:
            Raw item dicts as return by API
        """
        page = 1
        params = dict(params or {})
        while page <= max_pages:
            params.update({"page": page, "per_page": per_page})
            data = self._get_json(path, params=params)
            items = data.get(item_key, [])
            if not items:
                break
            for it in items:
                yield it
            if len(items) < per_page:
                break
            page += 1


    # --- Tickets --- #
    def get_ticket(self, ticket_id: int, include_requester: bool = False) -> Union[FreshTicket, None]:
        """
        Fetch singe ticket

        When include_requester is True, the requester's name and phone number are added to the ticket.

        Args:
            ticket_id (int): FreshService ticket ID
            include_requester (bool): Include requester details in ticket
        Returns:
            FreshTicket or None if not found
        """
        endpoint = f"/api/v2/tickets/{ticket_id}"
        if include_requester:
            endpoint += "?include=requester"
        resp = self.create_request(endpoint, "GET")
        try:
            payload = resp.json()
            data = payload.get("ticket", payload)
            if include_requester and isinstance(data, dict) and isinstance(data.get("requester"), dict):
                req = data["requester"]

                # Fill missing fields from requester
                data["name"] = data.get("name") or req.get("name")
                data["phone"] = (
                        data.get("phone")
                        or req.get("phone")
                        or req.get("mobile")
                        or req.get("mobile_number")
                        or req.get("work_phone_number")
                        or req.get("work_phone")
                        or req.get("primary_phone")
                )

                # If ticket has no department, use requester's department
                if include_requester and not data.get("department_id"):
                    requester_id = data.get("requester_id")
                    if requester_id:
                        try:
                            full_req = self.get_requester_by_id(int(requester_id)) or {}
                            dept_ids = full_req.get("department_ids") or []
                            data["department_id"] = (
                                    full_req.get("department_id") or (dept_ids[0] if dept_ids else None)
                            )
                        except Exception:
                            pass

                # Bring requester location into ticket if ticket is missing
                if not data.get("location"):
                    data["location"] = req.get("location") or req.get("location_id")

            return FreshTicket.model_validate(data)
        except Exception as e:
            logger.exception("Error parsing Freshservice ticket %s: %s", ticket_id, e)
            if "Internal Error" in resp.text:
                raise FreshserviceAPIError(resp.status_code, resp.text)
            return None

    def get_all_tickets_by_agent(
        self,
        agent_id: int,
        extra: Optional[str] = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> List[FreshTicket]:
        """
        Get all tickets for an agent until max_pages is reached

        Args:
             agent_id (int): FreshService agent ID
             extra (Optional[str]): Additional query params
             per_page (int): Number of items per page
             max_pages (int): Maximum number of pages to fetch
         Returns:
             List[FreshTicket]
        """
        all_tickets: List[FreshTicket] = []
        page = 1
        while page <= max_pages:
            batch = self.filter_tickets_by_agent(
                agent_id=agent_id,
                extra=extra,
                per_page=per_page,
                page=page,
            )
            if not batch:
                break
            all_tickets.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return all_tickets

    def filter_tickets_by_agent(
            self,
            agent_id: int,
            extra: Optional[str] = None,
            per_page: int = DEFAULT_PER_PAGE,
            page: int = 1,
    ) -> List[FreshTicket]:
        """
        Get one page of tickets for a specific agent. Helper for get_all_tickets_by_agent

        Args:
            agent_id (int): FreshService agent ID
            extra (Optional[str]): Additional query params
            per_page (int): Number of items per page
            page (int): Page number
        Returns:
            List[FreshTicket] for that page
        """
        from utils.config import settings  # local import to avoid circulars at module import time

        base_query = f"agent_id:{agent_id}"

        # Build a status clause unless we are explicitly told to allow closed tickets
        status_clause = ""
        if not settings.allow_closed_tickets:
            include_statuses = settings.fs_status_include or [2, 3, 4]  # Open, Pending, Resolved
            status_parts = [f"status:{int(s)}" for s in include_statuses]
            status_clause = "(" + " OR ".join(status_parts) + ")"

        # Merge caller-supplied 'extra' with base and status clause.
        if extra:
            if "status:" in extra.lower():
                full_query = f"{base_query} AND {extra}"
            else:
                full_query = f"{base_query} AND {extra}" + (f" AND {status_clause}" if status_clause else "")
        else:
            full_query = f"{base_query}" + (f" AND {status_clause}" if status_clause else "")

        # Encode and call the filter endpoint
        encoded_query = quote(f'"{full_query}"')
        endpoint = f"/api/v2/tickets/filter?query={encoded_query}&per_page={per_page}&page={page}"
        resp = self.create_request(endpoint, "GET")

        try:
            items = resp.json().get("tickets", [])
            return [FreshTicket.model_validate(t) for t in items]
        except Exception as e:
            logger.exception("Error parsing Freshservice tickets (agent_id=%s): %s", agent_id, e)
            if "Internal Error" in resp.text:
                raise FreshserviceAPIError(resp.status_code, resp.text)
            return []

    def get_ticket_fields(self) -> list[dict]:
        """
        Return all ticket fields from Freshservice.

        Tries v2 `/ticket_fields` first (legacy in some accounts), then
        falls back to `/ticket_form_fields` (workspace-aware).
        If `settings.fs_workspace_id` is set, it appends ?workspace_id=...
        """
        from utils.config import settings  # local import to avoid circulars

        endpoints = ["/api/v2/ticket_fields", "/api/v2/ticket_form_fields"]

        last_err = None
        for ep in endpoints:
            try:
                params = {}
                # If caller configured a workspace, include it for ticket_form_fields
                if ep.endswith("/ticket_form_fields") and getattr(settings, "fs_workspace_id", None):
                    params["workspace_id"] = settings.fs_workspace_id

                data = self._get_json(ep, params=params or None) or {}
                # Normalize return shape: both endpoints tend to return a top-level list or a dict key
                fields = (
                        data.get("ticket_fields") or
                        data.get("fields") or
                        data.get("ticket_form_fields") or
                        data  # sometimes an array is returned directly
                )
                return fields if isinstance(fields, list) else []
            except Exception as e:
                last_err = e
                continue

        # If both attempts failed, re-raise the last error so callers can see the cause
        raise last_err if last_err else RuntimeError("Unable to fetch Freshservice ticket fields")


    # --- Agents --- #
    def get_all_agents(self,
        per_page: int = DEFAULT_PER_PAGE,
        include_inactive: bool = True,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> List[dict]:
        """
        Get all agents from FreshService. Can optionally include inactive agents.

        Args:
             per_page (int): Number of items per page
             include_inactive (bool): Include inactive agents
             max_pages (int): Maximum number of pages to fetch
         Returns:
             List[dict] of agents
        """
        agents: List[dict] = []
        for a in self._paginate(
            "/api/v2/agents",
            item_key="agents",
            params={},
            per_page=per_page,
            max_pages=max_pages,
        ):
            if include_inactive or a.get("active", True):
                agents.append(a)
        return agents


    # --- Conversations --- #
    def get_ticket_conversations(self, ticket_id: int, per_page: int = DEFAULT_PER_PAGE, page: int = 1) -> List[dict]:
        """
        Get converstations, including replies, for a ticket

        Args:
             ticket_id (int): FreshService ticket ID
             per_page (int): Number of items per page
             page (int): Page number
         Returns:
             List[dict] of conversations for that ticket
        """
        endpoint = f"/api/v2/tickets/{ticket_id}/conversations?per_page={per_page}&page={page}"
        resp = self.create_request(endpoint, "GET")
        payload = resp.json()
        return payload.get("conversations", payload if isinstance(payload, list) else [])


    # --- User/Agent lookup --- #
    def get_agent_by_id(self, agent_id: int) -> Optional[dict]:
        """Lookup agent by ID"""
        try:
            data = self._get_json(f"/api/v2/agents/{agent_id}")
            return data.get("agent", data)
        except FreshserviceAPIError:
            return None


    def get_requester_by_id(self, requester_id: int) -> Optional[dict]:
        """Lookup requester by ID"""
        try:
            data = self._get_json(f"/api/v2/requesters/{requester_id}")
            return data.get("requester", data)
        except FreshserviceAPIError:
            return None


    # --- Departments and Groups --- #
    def get_departments(self, per_page: int = DEFAULT_PER_PAGE, max_page: int = DEFAULT_MAX_PAGES) -> List[dict]:
        """Fetch departments from FreshService"""
        return list(self._paginate(
            "/api/v2/departments",
            item_key="departments",
            params={},
            per_page=per_page,
            max_pages=max_page,
        ))


    def get_groups(self, per_page: int = DEFAULT_PER_PAGE, max_page: int = DEFAULT_MAX_PAGES) -> List[dict]:
        """Fetch groups from FreshService"""
        return list(self._paginate(
            "/api/v2/groups",
            item_key="groups",
            params={},
            per_page=per_page,
            max_pages=max_page,
        ))


    # --- Update ticket --- #
    def update_ticket(self, ticket_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Update a Freshservice ticket.

        Args:
            ticket_id: Freshservice ticket ID
            data: Dictionary of fields to update (e.g., {"status": 4})

        Returns:
            Updated ticket data, or None if update failed
        """
        endpoint = f"/api/v2/tickets/{ticket_id}"
        try:
            resp = self.create_request(endpoint, "PUT", data=data)
            payload = resp.json()
            return payload.get("ticket", payload)
        except Exception as e:
            logger.exception("Failed to update Freshservice ticket %s: %s", ticket_id, e)
            return None

    # --- Attachments (binary) --- #
    def download_attachment(self, url: str) -> bytes:
        """
        Download an attachment

        Args:
            url (str): URL to attachment
        Returns:
            Raw attachment bytes
        Raises:
            FreshserviceAPIError
        """
        if not url:
            return b""
        full_url = url if url.startswith("http") else f"{self.base_url}{url}"
        try:
            host = urlparse(full_url).netloc.lower()
        except Exception:
            host = ""
        prefer_auth = bool(self._base_host and host.endswith(self._base_host))

        def _get(use_auth: bool) -> requests.Response:
            return self.s.get(full_url, stream=True, auth=self.s.auth if use_auth else None)

        resp = _get(prefer_auth)
        if resp.status_code // 100 != 2:
            resp = _get(not prefer_auth)
        if resp.status_code // 100 != 2:
            logger.error("Freshservice download failed %s for %s", resp.status_code, full_url)
            raise FreshserviceAPIError(resp.status_code, resp.text)
        return resp.content
