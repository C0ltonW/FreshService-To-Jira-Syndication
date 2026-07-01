from __future__ import annotations

from typing import Dict, List, Optional
import logging

from models.department import DepartmentConfig

logger = logging.getLogger(__name__)


class DepartmentRegistry:
    """
    Central registry for department configurations.

    Responsibilities:
    - Load and validate department configurations
    - Resolve which department owns a given Freshservice agent
    - Provide department-specific settings for syndication workflows
    - Enable multi-department ticket processing
    """

    def __init__(self, departments: List[DepartmentConfig]):
        """
        Initialize the registry with a list of department configurations.

        Args:
            departments: List of DepartmentConfig instances

        Raises:
            ValueError: If agent IDs are duplicated across departments
        """
        self._departments: List[DepartmentConfig] = []
        self._agent_to_dept: Dict[int, DepartmentConfig] = {}
        self._dept_by_id: Dict[str, DepartmentConfig] = {}

        # Register each department and build lookup indices
        for dept in departments:
            self._register_department(dept)

        logger.info(
            "Initialized DepartmentRegistry with %d department(s) covering %d agent(s)",
            len(self._departments),
            len(self._agent_to_dept)
        )

    def _register_department(self, dept: DepartmentConfig) -> None:
        """
        Register a department and validate no agent ID conflicts.

        Args:
            dept: Department configuration to register

        Raises:
            ValueError: If any agent ID is already registered to another department
        """
        # Check for duplicate agent IDs
        for agent_id in dept.agent_ids:
            if agent_id in self._agent_to_dept:
                existing = self._agent_to_dept[agent_id]
                raise ValueError(
                    f"Agent ID {agent_id} is already registered to department "
                    f"'{existing.dept_id}' and cannot be assigned to '{dept.dept_id}'"
                )

        # Check for duplicate department IDs
        if dept.dept_id in self._dept_by_id:
            raise ValueError(f"Department ID '{dept.dept_id}' is already registered")

        # Register the department
        self._departments.append(dept)
        self._dept_by_id[dept.dept_id] = dept

        # Build agent → department reverse lookup
        for agent_id in dept.agent_ids:
            self._agent_to_dept[agent_id] = dept

        logger.info(
            "Registered department '%s' (%s) with %d agent(s): %s",
            dept.dept_id,
            dept.name,
            len(dept.agent_ids),
            dept.agent_ids
        )

    def get_department_by_agent(self, agent_id: int) -> Optional[DepartmentConfig]:
        """
        Look up which department owns a given Freshservice agent.

        Args:
            agent_id: Freshservice agent ID

        Returns:
            DepartmentConfig if agent is registered, None otherwise
        """
        return self._agent_to_dept.get(agent_id)

    def get_department_by_id(self, dept_id: str) -> Optional[DepartmentConfig]:
        """
        Get a department by its unique identifier.

        Args:
            dept_id: Department ID (e.g., "wms", "fpna")

        Returns:
            DepartmentConfig if found, None otherwise
        """
        return self._dept_by_id.get(dept_id)

    def get_all_departments(self) -> List[DepartmentConfig]:
        """
        Get all registered departments.

        Returns:
            List of all DepartmentConfig instances
        """
        return list(self._departments)

    def get_enabled_departments(self) -> List[DepartmentConfig]:
        """
        Get all enabled departments.

        Returns:
            List of enabled DepartmentConfig instances
        """
        return [dept for dept in self._departments if dept.enabled]

    def get_all_agent_ids(self) -> List[int]:
        """
        Get all Freshservice agent IDs across all departments.

        Returns:
            List of all agent IDs
        """
        return list(self._agent_to_dept.keys())

    def get_agent_ids_for_department(self, dept_id: str) -> List[int]:
        """
        Get agent IDs for a specific department.

        Args:
            dept_id: Department ID

        Returns:
            List of agent IDs, or empty list if department not found
        """
        dept = self.get_department_by_id(dept_id)
        return dept.agent_ids if dept else []

    @property
    def department_count(self) -> int:
        """Total number of registered departments."""
        return len(self._departments)

    @property
    def agent_count(self) -> int:
        """Total number of registered agents across all departments."""
        return len(self._agent_to_dept)

    def __repr__(self) -> str:
        return (
            f"DepartmentRegistry(departments={self.department_count}, "
            f"agents={self.agent_count})"
        )
