"""Resource naming helpers for the universal SRE agent."""

from __future__ import annotations


def format_resource_name(
    project_id: str,
    project_name: str,
    system_name_number: str,
    explicit_name: str | None = None,
) -> str:
    """Format a resource name using the project and system identifiers.

    If explicit_name is provided, it is used as the final name.
    Otherwise, the default format is: <project_id>_<project_name>_<system_name_number>
    Example: 1234_Project_1_DC6 -> '1234_Project_1_DC6'
    """
    if explicit_name:
        return explicit_name
    return f"{project_id}_{project_name}_{system_name_number}"
