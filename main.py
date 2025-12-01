from __future__ import annotations

import logging
from typing import List

from clients import JiraClient, FreshserviceClient
from core.sync_engine import SyncEngine
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


# --- Entry point --- #
def main():
    """
    Main entry point for the application.

    - Initializes logging and API clients.
    - Fetches agents and their tickets from Freshservice.
    - if IS_TEST is True, runs in test mode.
    """
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Freshservice to Jira sync...")

    fresh_client = FreshserviceClient(settings.freshservice_domain, settings.freshservice_api_key)
    jira_client = JiraClient(settings.jira_domain, settings.jira_email, settings.jira_api_token)
    sync_engine = SyncEngine(fresh_client, jira_client, settings)

    tickets: list = []
    try:
        all_agents = fresh_client.get_all_agents(per_page=100, include_inactive=False)
        logger.info(f"Fetched {len(all_agents)} agent(s) from Freshservice.")
        agent_filter: List[int] = settings.agents_to_sync
        agents_to_sync = _preview_agents(all_agents, agent_filter)
        if not agents_to_sync:
            logger.warning("No matching agents found in agents_to_sync. Nothing to fetch.")

        # Fetch all tickets for each agent and accumulate them into a single list
        for agent_id in agents_to_sync:
            logger.info(f"Fetching tickets for agent id={agent_id}")
            try:
                agent_tickets = fresh_client.get_all_tickets_by_agent(agent_id=agent_id, per_page=100)
                logger.info(f"Fetched {len(agent_tickets)} ticket(s) for agent id={agent_id}.")
                tickets.extend(agent_tickets)
                for t in agent_tickets[:10]:
                    print(f"Ticket id={t.id} subject={t.subject} requester_id={t.requester_id} status={t.status}")
            except Exception as ex:
                logger.exception(f"Failed to fetch tickets for agent id={agent_id}: {ex}")
    except Exception as e:
        logger.exception(f"Failed to fetch agents from Freshservice: {e}")

    try:
        issues = _preview_issues(jira_client, settings.jira_board_id)
    except Exception as e:
        issues = []
        logger.exception(f"Failed to fetch issues from Jira: {e}")

    # Switch for test mode if IS_TEST is True
    if settings.is_test:
        run_test_mode(
            fresh_client=fresh_client,
            jira_client=jira_client,
            sync_engine=sync_engine,
            aggregated_tickets=tickets,
            aggregated_issues=issues,
        )
    else:
        logger.info("Running in PRODUCTION MODE")
        sync_engine.run(tickets, issues)
        logger.info("PRODUCTION MODE sync completed.")

    logger.info("Sync completed.")


if __name__ == "__main__":
    main()
