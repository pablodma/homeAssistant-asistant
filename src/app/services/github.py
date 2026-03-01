"""GitHub API service for editing prompt files.

This service allows the Prompt Improver to update agent prompts via GitHub API,
which then triggers automatic Railway deployment.

Flow:
1. Prompt Improver identifies prompt improvements
2. Calls GitHub API to update the prompt file
3. GitHub triggers webhook → Railway redeploys (~30 sec)
"""

import base64
from typing import Optional

import httpx
import structlog

from ..config.settings import get_settings

logger = structlog.get_logger()

# Mapping of agent names to their prompt file paths in the repo
PROMPT_FILE_PATHS = {
    "router": "docs/prompts/router-agent.md",
    "finance": "docs/prompts/finance-agent.md",
    "agenda": "docs/prompts/calendar-agent.md",
    "shopping": "docs/prompts/shopping-agent.md",
    "vehicle": "docs/prompts/vehicle-agent.md",
    "qa": "docs/prompts/qa-agent.md",
    "prompt-improver": "docs/prompts/prompt-improver-agent.md",
}


class GitHubServiceError(Exception):
    """Exception raised when GitHub API call fails."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class GitHubService:
    """Service for interacting with GitHub API to edit prompt files."""

    def __init__(self):
        settings = get_settings()
        self.token = settings.github_token
        self.repo = settings.github_repo
        self.branch = settings.github_branch
        self.base_url = "https://api.github.com"

    @property
    def is_configured(self) -> bool:
        """Check if GitHub integration is properly configured."""
        return bool(self.token and self.repo)

    def _get_headers(self) -> dict:
        """Get headers for GitHub API requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_file_content(self, file_path: str) -> tuple[str, str]:
        """Get file content and SHA from GitHub.

        Args:
            file_path: Path to file in repository (e.g., "docs/prompts/finance-agent.md")

        Returns:
            Tuple of (content, sha)

        Raises:
            GitHubServiceError: If API call fails
        """
        url = f"{self.base_url}/repos/{self.repo}/contents/{file_path}"
        params = {"ref": self.branch}

        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=30.0,
            )

            if response.status_code == 404:
                raise GitHubServiceError(f"File not found: {file_path}", 404)

            if response.status_code != 200:
                logger.error(
                    "GitHub API error",
                    status=response.status_code,
                    body=response.text,
                )
                raise GitHubServiceError(
                    f"Failed to get file: {response.text}",
                    response.status_code,
                )

            data = response.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            sha = data["sha"]

            return content, sha

    async def update_file(
        self,
        file_path: str,
        content: str,
        commit_message: str,
        committer_name: str = "HomeAI Prompt Improver",
        committer_email: str = "prompt-improver@homeai.app",
    ) -> dict:
        """Update a file in the repository.

        Args:
            file_path: Path to file in repository
            content: New file content
            commit_message: Commit message
            committer_name: Name for the commit
            committer_email: Email for the commit

        Returns:
            GitHub API response with commit info

        Raises:
            GitHubServiceError: If API call fails
        """
        # First get current SHA (required for update)
        try:
            _, sha = await self.get_file_content(file_path)
        except GitHubServiceError as e:
            if e.status_code == 404:
                sha = None  # File doesn't exist, will create
            else:
                raise

        url = f"{self.base_url}/repos/{self.repo}/contents/{file_path}"

        payload = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": self.branch,
            "committer": {
                "name": committer_name,
                "email": committer_email,
            },
        }

        if sha:
            payload["sha"] = sha

        async with httpx.AsyncClient() as client:
            response = await client.put(
                url,
                headers=self._get_headers(),
                json=payload,
                timeout=30.0,
            )

            if response.status_code not in (200, 201):
                logger.error(
                    "GitHub API error",
                    status=response.status_code,
                    body=response.text,
                )
                raise GitHubServiceError(
                    f"Failed to update file: {response.text}",
                    response.status_code,
                )

            data = response.json()

            logger.info(
                "File updated via GitHub API",
                file_path=file_path,
                commit_sha=data["commit"]["sha"][:7],
                commit_url=data["commit"]["html_url"],
            )

            return {
                "commit_sha": data["commit"]["sha"],
                "commit_url": data["commit"]["html_url"],
                "file_url": data["content"]["html_url"],
            }

    async def update_prompt(
        self,
        agent_name: str,
        content: str,
        updated_by: str = "Prompt Improver",
    ) -> dict:
        """Update an agent's prompt file.

        Args:
            agent_name: Name of the agent (finance, calendar, etc.)
            content: New prompt content
            updated_by: User/system who made the change

        Returns:
            GitHub API response with commit info

        Raises:
            GitHubServiceError: If agent not found or API call fails
        """
        if agent_name not in PROMPT_FILE_PATHS:
            raise GitHubServiceError(f"Unknown agent: {agent_name}", 400)

        if not self.is_configured:
            raise GitHubServiceError(
                "GitHub integration not configured. Set GITHUB_TOKEN environment variable.",
                500,
            )

        file_path = PROMPT_FILE_PATHS[agent_name]
        commit_message = f"update: {agent_name} agent prompt (via {updated_by})"

        return await self.update_file(
            file_path=file_path,
            content=content,
            commit_message=commit_message,
            committer_name=f"HomeAI Prompt Improver ({updated_by})",
            committer_email="prompt-improver@homeai.app",
        )

    async def get_prompt(self, agent_name: str) -> str:
        """Get an agent's prompt content from GitHub.

        Args:
            agent_name: Name of the agent

        Returns:
            Prompt content

        Raises:
            GitHubServiceError: If agent not found or API call fails
        """
        if agent_name not in PROMPT_FILE_PATHS:
            raise GitHubServiceError(f"Unknown agent: {agent_name}", 400)

        if not self.is_configured:
            raise GitHubServiceError(
                "GitHub integration not configured. Set GITHUB_TOKEN environment variable.",
                500,
            )

        file_path = PROMPT_FILE_PATHS[agent_name]
        content, _ = await self.get_file_content(file_path)
        return content
