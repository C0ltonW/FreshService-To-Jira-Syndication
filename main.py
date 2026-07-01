from __future__ import annotations

import logging
from typing import List, Dict

from clients import JiraClient, FreshserviceClient
from core.sync_engine import SyncEngine
from core.department_registry import DepartmentRegistry
from models.department import DepartmentConfig
from utils.config import settings
from utils.logger import setup_logging
from utils.debugging import run_test_mode


# --- Agent preview / filtering (console aids) --- #
def _preview_agents(all_agents: List[dict], agent_filter: List[int]) -> List[int]:
    """Console preview of agents to sync."""
    agents_to_sync: List[int] = []
    for agent in all_agents:
        try:
            agent_id = int(agent.get("id"))
        except Exception:
            continue
        name = agent.get("name") or agent.get("first_name") or ""

        # Only include agents whose ID is in the configured filter list
        if agent_id in agent_filter:
            agents_to_sync.append(agent_id)
            print(f"Agent id={agent_id} name={name} added to sync list.")
    return agents_to_sync


# --- Board preview (first 10 issues) --- #
def _preview_issues(jira_client: JiraClient, board_id):
    """Console preview of issues to sync."""
    issues = jira_client.get_board_issues(board_id)
    logging.getLogger(__name__).info(f"Fetched {len(issues)} issue(s) from Jira board {board_id}.")
    for i in issues[:10]:
        status_name = None
        try:
            status_name = i.fields.status.name if getattr(i, "fields", None) and getattr(i.fields, "status", None) else None
        except Exception:
            status_name = None
        summary = getattr(i, "fields", None)
        summary = getattr(summary, "summary", "") if summary else ""
        print(f"Issue id={getattr(i, 'id', '?')} key={getattr(i, 'key', '?')} summary={summary} status={status_name}")
    return issues


# --- Department registry loading --- #
def _load_department_registry() -> DepartmentRegistry:
    """
    Load department configurations and create registry.

    Returns:
        DepartmentRegistry instance
    """
    logger = logging.getLogger(__name__)

    # Load department configurations from settings
    dept_configs_raw = settings.load_departments()

    if not dept_configs_raw:
        logger.warning("No department configurations loaded. Using legacy single-department mode.")
        # Create a default department from legacy settings
        default_dept = DepartmentConfig(
            dept_id="default",
            name="Default Department",
            agent_ids=settings.agents_to_sync,
            jira_project_key=settings.jira_project_key,
            jira_issue_type_id=settings.jira_issue_type_id,
            jira_board_id=settings.jira_board_id,
            jira_default_status=settings.jira_status,
            assignment_map={},
            status_sync_map={},
        )
        return DepartmentRegistry([default_dept])

    # Parse department configurations
    departments = []
    for dept_data in dept_configs_raw:
        try:
            dept = DepartmentConfig.model_validate(dept_data)
            departments.append(dept)
        except Exception as ex:
            logger.error("Failed to parse department config %s: %s", dept_data.get("dept_id", "?"), ex)

    if not departments:
        raise ValueError("No valid department configurations found")

    return DepartmentRegistry(departments)


# --- Entry point --- #
def main():
    """
    Main entry point for the application.

    - Initializes logging and API clients.
    - Loads department registry for multi-department support.
    - Fetches agents and their tickets from Freshservice.
    - Processes tickets department-by-department.
    - if IS_TEST is True, runs in test mode.
    """
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Freshservice to Jira sync...")

    fresh_client = FreshserviceClient(settings.freshservice_domain, settings.freshservice_api_key)
    jira_client = JiraClient(settings.jira_domain, settings.jira_email, settings.jira_api_token)

    # Load department registry
    try:
        registry = _load_department_registry()
        logger.info("Loaded %d department(s): %s",
                   registry.department_count,
                   [d.dept_id for d in registry.get_enabled_departments()])
    except Exception as ex:
        logger.exception("Failed to load department registry: %s", ex)
        return

    # Fetch all agents from Freshservice for preview
    try:
        all_agents = fresh_client.get_all_agents(per_page=100, include_inactive=False)
        logger.info(f"Fetched {len(all_agents)} agent(s) from Freshservice.")
    except Exception as e:
        logger.exception(f"Failed to fetch agents from Freshservice: {e}")
        all_agents = []

    # Get all agent IDs we care about from the registry
    agent_ids_to_sync = registry.get_all_agent_ids()
    agents_to_sync = _preview_agents(all_agents, agent_ids_to_sync)

    if not agents_to_sync:
        logger.warning("No matching agents found across all departments. Nothing to fetch.")
        return

    # Fetch all tickets grouped by department
    tickets_by_department: Dict[str, List] = {}
    for agent_id in agents_to_sync:
        dept = registry.get_department_by_agent(agent_id)
        if not dept or not dept.enabled:
            logger.info(f"Skipping agent {agent_id}: department not found or disabled")
            continue

        logger.info(f"Fetching tickets for agent id={agent_id} (department: {dept.name})")
        try:
            agent_tickets = fresh_client.get_all_tickets_by_agent(agent_id=agent_id, per_page=100)
            logger.info(f"Fetched {len(agent_tickets)} ticket(s) for agent id={agent_id}.")

            # Group tickets by department
            if dept.dept_id not in tickets_by_department:
                tickets_by_department[dept.dept_id] = []
            tickets_by_department[dept.dept_id].extend(agent_tickets)

            # Preview first few tickets
            for t in agent_tickets[:10]:
                print(f"[{dept.dept_id}] Ticket id={t.id} subject={t.subject} "
                     f"requester_id={t.requester_id} status={t.status}")
        except Exception as ex:
            logger.exception(f"Failed to fetch tickets for agent id={agent_id}: {ex}")

    # TEST MODE: Use first department with tickets for backward compatibility
    if settings.is_test:
        if tickets_by_department:
            first_dept_id = list(tickets_by_department.keys())[0]
            first_dept = registry.get_department_by_id(first_dept_id)
            test_tickets = tickets_by_department[first_dept_id]

            logger.info(f"TEST MODE: Using department '{first_dept.name}' with {len(test_tickets)} ticket(s)")

            try:
                test_issues = _preview_issues(jira_client, first_dept.jira_board_id)
            except Exception as e:
                test_issues = []
                logger.exception(f"Failed to fetch issues from Jira: {e}")

            sync_engine = SyncEngine(fresh_client, jira_client, settings, department=first_dept)
            run_test_mode(
                fresh_client=fresh_client,
                jira_client=jira_client,
                sync_engine=sync_engine,
                aggregated_tickets=test_tickets,
                aggregated_issues=test_issues,
            )
        else:
            logger.warning("No tickets available for test mode")
    else:
        # PRODUCTION MODE: Process each department
        logger.info("Running in PRODUCTION MODE")
        for dept in registry.get_enabled_departments():
            dept_tickets = tickets_by_department.get(dept.dept_id, [])
            if not dept_tickets:
                logger.info(f"No tickets for department '{dept.name}' ({dept.dept_id})")
                continue

            logger.info(f"Processing {len(dept_tickets)} ticket(s) for department '{dept.name}'")

            try:
                # Fetch issues from department-specific board
                dept_issues = jira_client.get_board_issues(dept.jira_board_id, max_results=100)
                logger.info(f"Fetched {len(dept_issues)} issue(s) from Jira board {dept.jira_board_id}")
            except Exception as e:
                logger.exception(f"Failed to fetch issues for department '{dept.name}': {e}")
                dept_issues = []

            # Create department-specific sync engine
            dept_sync_engine = SyncEngine(fresh_client, jira_client, settings, department=dept)

            # Run sync for this department
            try:
                dept_sync_engine.run(dept_tickets, dept_issues)
                logger.info(f"Completed sync for department '{dept.name}'")
            except Exception as ex:
                logger.exception(f"Failed to sync department '{dept.name}': {ex}")

        logger.info("PRODUCTION MODE sync completed for all departments.")

    logger.info("Sync completed.")


if __name__ == "__main__":
    main()
