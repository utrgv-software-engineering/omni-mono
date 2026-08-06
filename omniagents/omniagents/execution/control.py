"""AgentHost control-plane registries for Workspace and profile definitions."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import WorkspaceNotFoundError
from .models import WorkspaceResource
from .registry import WorkspaceRegistry


class AgentHostProvisioningCatalog:
    """Definitions required to provision environments inside one AgentHost."""

    def __init__(self, workspaces: WorkspaceRegistry) -> None:
        self._workspaces = workspaces
        self._workspace_directories: dict[str, Path] = {}
        self._profiles: dict[tuple[str | None, str], dict[str, Any]] = {}

    def register_workspace(
        self,
        *,
        workspace_id: str,
        owner_user_id: str | None,
        materialization_path: str,
        snapshot_ref: str | None = None,
        sources: Sequence[Mapping[str, Any]] = (),
    ) -> WorkspaceResource:
        path = self._path(materialization_path)
        resource = WorkspaceResource(
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            snapshot_ref=snapshot_ref,
            sources=tuple(deepcopy(dict(source)) for source in sources),
        )
        try:
            existing = self._workspaces.get(workspace_id)
        except WorkspaceNotFoundError:
            self._workspaces.register(resource)
        else:
            if existing != resource:
                raise ValueError(
                    f"workspace {workspace_id!r} is already registered differently"
                )
            existing_path = self._workspace_directories.get(workspace_id)
            if existing_path is not None and existing_path != path:
                raise ValueError(
                    f"workspace {workspace_id!r} has a different materialization path"
                )
            resource = existing
        self._workspace_directories[workspace_id] = path
        return resource

    def workspace_directory(self, workspace: WorkspaceResource) -> Path:
        try:
            return self._workspace_directories[workspace.workspace_id]
        except KeyError:
            raise KeyError(
                f"workspace {workspace.workspace_id!r} has no materialization path"
            ) from None

    def register_profile(
        self,
        profile_id: str,
        definition: Mapping[str, Any],
        *,
        owner_user_id: str | None,
    ) -> dict[str, Any]:
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        if owner_user_id is not None and (
            not isinstance(owner_user_id, str) or not owner_user_id.strip()
        ):
            raise ValueError("owner_user_id must be a non-empty string")
        if not isinstance(definition, Mapping):
            raise TypeError("profile definition must be an object")
        copied = deepcopy(dict(definition))
        kind = copied.get("kind")
        if kind == "path":
            if set(copied) != {"kind", "path"}:
                raise ValueError("path profile accepts only 'kind' and 'path'")
            copied["path"] = str(self._path(copied.get("path")))
        elif kind == "default":
            if not set(copied).issubset({"kind", "project_id"}):
                raise ValueError("default profile accepts only 'kind' and 'project_id'")
            project_id = copied.get("project_id")
            if project_id is not None and (
                not isinstance(project_id, str) or not project_id.strip()
            ):
                raise ValueError("project_id must be a non-empty string")
        else:
            raise ValueError("profile kind must be 'default' or 'path'")
        key = (owner_user_id, profile_id)
        existing = self._profiles.get(key)
        if existing is not None and existing != copied:
            raise ValueError(
                f"profile {profile_id!r} is already registered differently"
            )
        self._profiles[key] = copied
        return deepcopy(copied)

    def profile(self, profile_id: str, *, owner_user_id: str | None) -> dict[str, Any]:
        try:
            return deepcopy(self._profiles[(owner_user_id, profile_id)])
        except KeyError:
            raise KeyError(f"profile {profile_id!r} is not registered") from None

    def list_profiles(self, *, owner_user_id: str | None) -> dict[str, dict[str, Any]]:
        return {
            profile_id: deepcopy(definition)
            for (owner, profile_id), definition in self._profiles.items()
            if owner == owner_user_id
        }

    def profiles_by_owner(self) -> list[tuple[str | None, str, dict[str, Any]]]:
        """Every registered profile with its owner attribution.

        The caller applies authorization; the catalog itself has no
        principal. Ordered by owner then profile id so inventory payloads
        are stable across calls.
        """
        return [
            (owner, profile_id, deepcopy(definition))
            for (owner, profile_id), definition in sorted(
                self._profiles.items(),
                key=lambda item: (item[0][0] or "", item[0][1]),
            )
        ]

    @staticmethod
    def _path(value: Any) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("materialization/profile path must be a non-empty string")
        return Path(value).expanduser().resolve()
