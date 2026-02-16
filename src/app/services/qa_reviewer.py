"""Prompt Improver - Analyzes quality issues and improves agent prompts.

On-demand service triggered via internal API endpoint (curl / Railway CLI) that:
1. Fetches unresolved quality issues (soft + hard errors)
2. Sends them to Claude for analysis (using prompt-improver prompt)
3. For each agent with improvement proposals, generates improved prompts
4. Applies improvements via GitHub API
5. Stores revision history for rollback
"""

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog
from anthropic import AsyncAnthropic

from ..config.database import get_pool
from ..config.settings import get_settings
from .github import GitHubService, GitHubServiceError

logger = structlog.get_logger()


# System prompt for Step 2: Generating improved prompts
# Uses XML output format instead of JSON to avoid malformed JSON issues
# when the improved_prompt contains thousands of chars with quotes/newlines.
PROMPT_IMPROVER_SYSTEM = """Sos un experto en prompt engineering para agentes de IA conversacional.

Tu tarea es mejorar un prompt de agente basándote en propuestas de mejora específicas derivadas de un análisis de calidad.

Reglas estrictas:
1. Hacé SOLO los cambios mínimos necesarios para abordar los problemas identificados
2. NO reescribas el prompt completo - ajustá secciones específicas
3. Mantené el tono, estilo y estructura general del prompt original
4. Mantené el idioma del prompt original (si está en español, respondé en español)
5. Si una propuesta de mejora no se puede implementar solo con cambios al prompt (ej: requiere cambios de código), ignorala y mencionalo
6. Preservá todas las instrucciones existentes que no estén relacionadas con los problemas

Respondé SOLO con XML (sin markdown, sin texto fuera de los tags):

<result>
<should_modify>true</should_modify>
<improved_prompt>
...el prompt completo con los cambios aplicados...
</improved_prompt>
<changes>
<change section="nombre de la sección" change="descripción del cambio" reason="qué problema resuelve"/>
</changes>
<skipped_proposals>descripción de propuestas no implementadas (o vacío)</skipped_proposals>
<confidence>0.8</confidence>
</result>

Si no hay cambios necesarios en el prompt (ej: todos los problemas son técnicos):
<result>
<should_modify>false</should_modify>
<improved_prompt></improved_prompt>
<changes></changes>
<skipped_proposals>razón</skipped_proposals>
<confidence>1.0</confidence>
</result>"""


class QABatchReviewer:
    """Service for batch reviewing quality issues and improving agent prompts."""

    def __init__(self):
        self.settings = get_settings()
        self.github = GitHubService()
        self._client: Optional[AsyncAnthropic] = None

    @property
    def client(self) -> AsyncAnthropic:
        """Lazy-init Anthropic client."""
        if self._client is None:
            if not self.settings.anthropic_api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not configured. "
                    "Set the environment variable to enable QA reviews."
                )
            self._client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        return self._client

    async def run_review(
        self,
        tenant_id: str,
        triggered_by: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Execute a full QA review cycle.

        Args:
            tenant_id: The tenant to review.
            triggered_by: Email of the admin who triggered this.
            days: How many days back to look for issues.

        Returns:
            Complete review result with analysis and applied revisions.
        """
        pool = await get_pool()
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=days)

        # Create review cycle record
        cycle_id = await pool.fetchval(
            """
            INSERT INTO qa_review_cycles (tenant_id, triggered_by, period_start, period_end, status)
            VALUES ($1, $2, $3, $4, 'running')
            RETURNING id
            """,
            tenant_id,
            triggered_by,
            period_start,
            now,
        )

        try:
            # Step 1: Fetch unresolved issues
            issues = await self._fetch_unresolved_issues(tenant_id, period_start)

            if not issues:
                await self._complete_cycle(cycle_id, 0, 0, {"message": "No issues found"})
                return {
                    "cycle_id": str(cycle_id),
                    "status": "completed",
                    "issues_analyzed": 0,
                    "improvements_applied": 0,
                    "analysis": None,
                    "revisions": [],
                    "message": "No hay issues sin resolver en el período seleccionado.",
                }

            # Step 2: Build data for Claude
            soft_errors = [i for i in issues if i["issue_type"] == "soft_error"]
            hard_errors = [i for i in issues if i["issue_type"] == "hard_error"]

            conversation_log = self._build_conversation_log(soft_errors)
            api_logs = self._build_api_logs(hard_errors)
            metrics = self._build_metrics(issues)

            # Step 3: Load QA Reviewer prompt and run analysis
            qa_reviewer_prompt = await self._load_reviewer_prompt()
            filled_prompt = qa_reviewer_prompt.replace(
                "{{CONVERSATION_LOG}}", conversation_log
            ).replace(
                "{{API_LOGS}}", api_logs
            ).replace(
                "{{CURRENT_METRICS}}", metrics
            )

            analysis = await self._run_analysis(filled_prompt)
            parsed_analysis = self._parse_xml_response(analysis)

            # Step 4: Generate and apply prompt improvements
            revisions = []
            proposals = parsed_analysis.get("automated_fixes", "")

            if proposals:
                revisions = await self._process_improvements(
                    tenant_id=tenant_id,
                    cycle_id=str(cycle_id),
                    proposals=proposals,
                    issues=issues,
                    triggered_by=triggered_by,
                )

            # Step 5: Complete cycle
            await self._complete_cycle(
                cycle_id=cycle_id,
                issues_count=len(issues),
                improvements_count=len(revisions),
                analysis_result=parsed_analysis,
            )

            logger.info(
                "QA review completed",
                cycle_id=str(cycle_id),
                issues_analyzed=len(issues),
                improvements_applied=len(revisions),
            )

            return {
                "cycle_id": str(cycle_id),
                "status": "completed",
                "issues_analyzed": len(issues),
                "improvements_applied": len(revisions),
                "analysis": parsed_analysis,
                "revisions": revisions,
            }

        except Exception as e:
            # Mark cycle as failed
            await pool.execute(
                """
                UPDATE qa_review_cycles
                SET status = 'failed', error_message = $1, completed_at = NOW()
                WHERE id = $2
                """,
                str(e),
                cycle_id,
            )
            logger.error("QA review failed", cycle_id=str(cycle_id), error=str(e))
            raise

    # =====================================================
    # PRIVATE METHODS
    # =====================================================

    async def _fetch_unresolved_issues(
        self,
        tenant_id: str,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch unresolved quality issues from the database."""
        pool = await get_pool()

        rows = await pool.fetch(
            """
            SELECT id, issue_type, issue_category, severity, agent_name, tool_name,
                   user_phone, message_in, message_out, error_code, error_message,
                   qa_analysis, qa_suggestion, qa_confidence,
                   admin_insight, stack_trace, correlation_id, created_at
            FROM quality_issues
            WHERE tenant_id = $1
              AND is_resolved = false
              AND created_at >= $2
            ORDER BY created_at DESC
            """,
            tenant_id,
            since,
        )

        return [dict(row) for row in rows]

    def _build_conversation_log(self, soft_errors: list[dict[str, Any]]) -> str:
        """Format soft errors as a conversation log for Claude."""
        if not soft_errors:
            return "No conversation quality issues found in this period."

        entries = []
        for i, issue in enumerate(soft_errors, 1):
            entry = (
                f"--- Interaction #{i} ---\n"
                f"Agent: {issue.get('agent_name', 'unknown')}\n"
                f"Category: {issue['issue_category']}\n"
                f"Severity: {issue['severity']}\n"
                f"User said: {issue.get('message_in', 'N/A')}\n"
                f"Bot responded: {issue.get('message_out', 'N/A')}\n"
                f"QA Analysis: {issue.get('qa_analysis', 'N/A')}\n"
                f"QA Suggestion: {issue.get('qa_suggestion', 'N/A')}\n"
                f"Confidence: {issue.get('qa_confidence', 'N/A')}\n"
            )
            if issue.get("admin_insight"):
                entry += f"Admin Insight: {issue['admin_insight']}\n"
            entry += f"Time: {issue['created_at']}\n"
            entries.append(entry)

        return "\n".join(entries)

    def _build_api_logs(self, hard_errors: list[dict[str, Any]]) -> str:
        """Format hard errors as API logs for Claude."""
        if not hard_errors:
            return "No technical errors found in this period."

        entries = []
        for i, issue in enumerate(hard_errors, 1):
            entry = (
                f"--- Error #{i} ---\n"
                f"Type: {issue['issue_category']}\n"
                f"Severity: {issue['severity']}\n"
                f"Agent: {issue.get('agent_name', 'unknown')}\n"
                f"Tool: {issue.get('tool_name', 'N/A')}\n"
                f"Error: {issue['error_message']}\n"
                f"Error Code: {issue.get('error_code', 'N/A')}\n"
                f"User Message: {issue.get('message_in', 'N/A')}\n"
                f"Correlation ID: {issue.get('correlation_id', 'N/A')}\n"
                f"Time: {issue['created_at']}\n"
            )
            if issue.get("stack_trace"):
                entry += f"Stack Trace:\n{issue['stack_trace'][:500]}\n"
            entries.append(entry)

        return "\n".join(entries)

    def _build_metrics(self, issues: list[dict[str, Any]]) -> str:
        """Build aggregated metrics from issues."""
        total = len(issues)
        soft = sum(1 for i in issues if i["issue_type"] == "soft_error")
        hard = sum(1 for i in issues if i["issue_type"] == "hard_error")

        # Count by agent
        by_agent: dict[str, int] = {}
        for issue in issues:
            agent = issue.get("agent_name") or "unknown"
            by_agent[agent] = by_agent.get(agent, 0) + 1

        # Count by category
        by_category: dict[str, int] = {}
        for issue in issues:
            cat = issue["issue_category"]
            by_category[cat] = by_category.get(cat, 0) + 1

        # Count by severity
        by_severity: dict[str, int] = {}
        for issue in issues:
            sev = issue["severity"]
            by_severity[sev] = by_severity.get(sev, 0) + 1

        lines = [
            f"Total issues: {total}",
            f"Soft errors (quality): {soft}",
            f"Hard errors (technical): {hard}",
            "",
            "By agent:",
        ]
        for agent, count in sorted(by_agent.items(), key=lambda x: -x[1]):
            lines.append(f"  - {agent}: {count}")

        lines.append("\nBy category:")
        for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
            lines.append(f"  - {cat}: {count}")

        lines.append("\nBy severity:")
        for sev, count in sorted(by_severity.items(), key=lambda x: -x[1]):
            lines.append(f"  - {sev}: {count}")

        return "\n".join(lines)

    async def _load_reviewer_prompt(self) -> str:
        """Load the QA Reviewer prompt from GitHub or local fallback."""
        try:
            if self.github.is_configured:
                return await self.github.get_prompt("prompt-improver")
        except GitHubServiceError:
            logger.warning("Failed to load prompt-improver prompt from GitHub, using local fallback")

        # Local fallback
        from pathlib import Path

        local_path = Path(__file__).parent.parent.parent.parent / "docs" / "prompts" / "prompt-improver-agent.md"
        if local_path.exists():
            return local_path.read_text(encoding="utf-8")

        raise ValueError("Prompt Improver prompt not found. Check docs/prompts/prompt-improver-agent.md")

    async def _run_analysis(self, filled_prompt: str) -> str:
        """Run Step 1: Claude analyzes issues using the QA Reviewer prompt."""
        async with self.client.messages.stream(
            model=self.settings.qa_review_model,
            max_tokens=8000,
            messages=[
                {"role": "user", "content": filled_prompt},
            ],
        ) as stream:
            response = await stream.get_final_message()
        return response.content[0].text

    def _parse_xml_response(self, response: str) -> dict[str, Any]:
        """Parse Claude's XML-tagged response into structured data.

        Supports both the original 4-section format and the extended format
        with automated_fixes, code_patches, strategic_improvements, etc.
        """
        sections: dict[str, Any] = {}

        all_tags = [
            # Original sections
            "understanding_errors",
            "hard_errors",
            "improvement_proposals",
            "summary",
            # Extended sections (v2 prompt)
            "automated_fixes",
            "code_patches",
            "strategic_improvements",
            "process_improvements",
            "implementation_roadmap",
            "executive_summary",
        ]

        for tag in all_tags:
            pattern = rf"<{tag}>(.*?)</{tag}>"
            match = re.search(pattern, response, re.DOTALL)
            if match:
                sections[tag] = match.group(1).strip()

        return sections

    def _parse_improvement_xml(self, content: str) -> Optional[dict[str, Any]]:
        """Parse Claude's XML improvement response into structured data.

        More robust than JSON for long prompt content that contains
        quotes, newlines, and special characters.
        """
        try:
            # Extract should_modify
            should_modify_match = re.search(
                r"<should_modify>(.*?)</should_modify>", content, re.DOTALL
            )
            if not should_modify_match:
                return None

            should_modify = should_modify_match.group(1).strip().lower() == "true"

            # Extract improved_prompt (can be very long)
            prompt_match = re.search(
                r"<improved_prompt>\s*(.*?)\s*</improved_prompt>", content, re.DOTALL
            )
            improved_prompt = prompt_match.group(1).strip() if prompt_match else ""

            # Extract changes
            changes_summary = []
            change_matches = re.finditer(
                r'<change\s+section="([^"]*?)"\s+change="([^"]*?)"\s+reason="([^"]*?)"\s*/?>',
                content,
                re.DOTALL,
            )
            for m in change_matches:
                changes_summary.append({
                    "section": m.group(1),
                    "change": m.group(2),
                    "reason": m.group(3),
                })

            # Extract confidence
            confidence_match = re.search(
                r"<confidence>(.*?)</confidence>", content, re.DOTALL
            )
            confidence = float(confidence_match.group(1).strip()) if confidence_match else 0.5

            return {
                "should_modify": should_modify,
                "improved_prompt": improved_prompt if should_modify else None,
                "changes_summary": changes_summary,
                "confidence": confidence,
            }
        except Exception as e:
            logger.error("XML parsing error", error=str(e))
            return None

    async def _process_improvements(
        self,
        tenant_id: str,
        cycle_id: str,
        proposals: str,
        issues: list[dict[str, Any]],
        triggered_by: str,
    ) -> list[dict[str, Any]]:
        """Process improvement proposals and apply prompt changes.

        For each agent mentioned in proposals, generate and apply an improved prompt.
        """
        # Identify which agents are mentioned in proposals
        agents_with_issues = self._extract_agents_from_proposals(proposals, issues)

        if not agents_with_issues:
            return []

        revisions = []
        improvements_applied = 0

        for agent_name, agent_issues in agents_with_issues.items():
            if improvements_applied >= self.settings.qa_review_max_improvements:
                logger.info(
                    "Max improvements reached",
                    max=self.settings.qa_review_max_improvements,
                )
                break

            # Check cooldown
            if await self._is_on_cooldown(tenant_id, agent_name):
                logger.info(
                    "Agent prompt on cooldown, skipping",
                    agent_name=agent_name,
                )
                continue

            # Check minimum issues threshold (except for hallucinations/critical)
            has_critical = any(
                i["issue_category"] == "hallucination" or i["severity"] == "critical"
                for i in agent_issues
            )
            if len(agent_issues) < self.settings.qa_review_min_issues and not has_critical:
                logger.info(
                    "Not enough issues for agent, skipping",
                    agent_name=agent_name,
                    issue_count=len(agent_issues),
                    min_required=self.settings.qa_review_min_issues,
                )
                continue

            try:
                revision = await self._improve_agent_prompt(
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    agent_name=agent_name,
                    agent_issues=agent_issues,
                    proposals=proposals,
                    triggered_by=triggered_by,
                )
                if revision:
                    revisions.append(revision)
                    improvements_applied += 1
            except Exception as e:
                logger.error(
                    "Failed to improve prompt for agent",
                    agent_name=agent_name,
                    error=str(e),
                )

        return revisions

    def _extract_agents_from_proposals(
        self,
        proposals: str,
        issues: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Group issues by agent name for agents that have actionable proposals."""
        # Known agent names
        known_agents = {"router", "finance", "calendar", "reminder", "shopping", "vehicle", "qa"}

        # Group issues by agent
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            agent = issue.get("agent_name")
            if agent and agent in known_agents:
                if agent not in by_agent:
                    by_agent[agent] = []
                by_agent[agent].append(issue)

        # Filter to only agents mentioned in proposals
        result = {}
        proposals_lower = proposals.lower()
        for agent_name, agent_issues in by_agent.items():
            if agent_name.lower() in proposals_lower:
                result[agent_name] = agent_issues

        # If no specific agent matched in proposals text, return all agents with issues
        if not result and by_agent:
            return by_agent

        return result

    async def _is_on_cooldown(self, tenant_id: str, agent_name: str) -> bool:
        """Check if an agent's prompt was modified recently."""
        pool = await get_pool()
        cooldown_since = datetime.now(timezone.utc) - timedelta(
            hours=self.settings.qa_review_cooldown_hours
        )

        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM prompt_revisions
            WHERE tenant_id = $1 AND agent_name = $2
              AND created_at >= $3 AND is_rolled_back = false
            """,
            tenant_id,
            agent_name,
            cooldown_since,
        )
        return count > 0

    async def _improve_agent_prompt(
        self,
        tenant_id: str,
        cycle_id: str,
        agent_name: str,
        agent_issues: list[dict[str, Any]],
        proposals: str,
        triggered_by: str,
    ) -> Optional[dict[str, Any]]:
        """Generate and apply an improved prompt for a specific agent."""
        # Load current prompt
        try:
            current_prompt = await self.github.get_prompt(agent_name)
        except GitHubServiceError as e:
            logger.error("Cannot read prompt for agent", agent_name=agent_name, error=e.message)
            return None

        # Build context for the improvement
        issues_summary = "\n".join(
            f"- [{i['issue_category']}/{i['severity']}] "
            f"User: \"{(i.get('message_in') or 'N/A')[:100]}\" → "
            f"Bot: \"{(i.get('message_out') or 'N/A')[:100]}\" "
            f"(QA: {(i.get('qa_analysis') or 'N/A')[:150]})"
            for i in agent_issues
        )

        user_message = (
            f"## Prompt actual del agente '{agent_name}':\n\n"
            f"```\n{current_prompt}\n```\n\n"
            f"## Issues detectados para este agente ({len(agent_issues)} issues):\n\n"
            f"{issues_summary}\n\n"
            f"## Propuestas de mejora del análisis de calidad:\n\n"
            f"{proposals}\n\n"
            f"Generá el prompt mejorado aplicando SOLO los cambios necesarios "
            f"para resolver los issues de este agente específico."
        )

        # Call Claude for improvement
        # max_tokens=16000 to avoid truncation: the improved_prompt field contains
        # the full agent prompt which can be thousands of tokens when JSON-encoded.
        async with self.client.messages.stream(
            model=self.settings.qa_review_model,
            max_tokens=16000,
            system=PROMPT_IMPROVER_SYSTEM,
            messages=[
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
        ) as stream:
            response = await stream.get_final_message()

        content = response.content[0].text

        # Check if response was truncated (stop_reason == "max_tokens")
        if response.stop_reason == "max_tokens":
            logger.error(
                "Claude improvement response truncated (max_tokens reached)",
                agent_name=agent_name,
                content_length=len(content),
            )
            return None

        # Parse XML response
        result = self._parse_improvement_xml(content)
        if result is None:
            logger.error(
                "Failed to parse improvement XML response",
                content=content[:500],
                content_length=len(content),
            )
            return None

        if not result.get("should_modify"):
            logger.info("Claude decided no prompt change needed", agent_name=agent_name)
            return None

        improved_prompt = result.get("improved_prompt")
        if not improved_prompt:
            return None

        changes_summary = result.get("changes_summary", [])
        confidence = result.get("confidence", 0.0)

        # Apply via GitHub
        try:
            commit_result = await self.github.update_prompt(
                agent_name=agent_name,
                content=improved_prompt,
                updated_by=f"QA Reviewer (triggered by {triggered_by})",
            )
        except GitHubServiceError as e:
            logger.error("Failed to apply improvement via GitHub", error=e.message)
            return None

        # Store revision
        pool = await get_pool()
        issue_ids = [str(i["id"]) for i in agent_issues]

        revision_id = await pool.fetchval(
            """
            INSERT INTO prompt_revisions (
                review_cycle_id, tenant_id, agent_name,
                original_prompt, improved_prompt,
                improvement_reason, changes_summary, issues_addressed,
                github_commit_sha, github_commit_url
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id
            """,
            cycle_id,
            tenant_id,
            agent_name,
            current_prompt,
            improved_prompt,
            "\n".join(
                f"- {c.get('section', '?')}: {c.get('change', '?')} ({c.get('reason', '?')})"
                for c in changes_summary
            ),
            json.dumps(changes_summary),
            json.dumps(issue_ids),
            commit_result["commit_sha"],
            commit_result["commit_url"],
        )

        logger.info(
            "Prompt improved",
            agent_name=agent_name,
            revision_id=str(revision_id),
            commit_sha=commit_result["commit_sha"][:7],
            changes=len(changes_summary),
            confidence=confidence,
        )

        return {
            "revision_id": str(revision_id),
            "agent_name": agent_name,
            "improvement_reason": "\n".join(
                f"- {c.get('change', '?')}" for c in changes_summary
            ),
            "changes_summary": changes_summary,
            "confidence": confidence,
            "github_commit_sha": commit_result["commit_sha"],
            "github_commit_url": commit_result["commit_url"],
            "is_rolled_back": False,
        }

    async def _complete_cycle(
        self,
        cycle_id: Any,
        issues_count: int,
        improvements_count: int,
        analysis_result: dict[str, Any],
    ) -> None:
        """Mark a review cycle as completed."""
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE qa_review_cycles
            SET status = 'completed',
                issues_analyzed_count = $1,
                improvements_applied_count = $2,
                analysis_result = $3,
                completed_at = NOW()
            WHERE id = $4
            """,
            issues_count,
            improvements_count,
            json.dumps(analysis_result, default=str),
            cycle_id,
        )

    # =====================================================
    # SINGLE ISSUE FIX (triggered from admin panel)
    # =====================================================

    async def fix_single_issue(self, issue_id: str) -> None:
        """Generate and apply a prompt fix for a single quality issue (background).

        Reads the issue (including admin_insight), generates an improved
        prompt via Claude, and commits it to GitHub.
        Updates fix_status in quality_issues as it progresses.

        Args:
            issue_id: UUID of the quality issue to fix.
        """
        pool = await get_pool()

        try:
            # Mark as in_progress
            await pool.execute(
                "UPDATE quality_issues SET fix_status = 'in_progress', fix_error = NULL WHERE id = $1",
                issue_id,
            )

            # Fetch the issue
            row = await pool.fetchrow(
                """
                SELECT id, tenant_id, issue_type, issue_category, severity,
                       agent_name, tool_name, message_in, message_out,
                       error_message, qa_analysis, qa_suggestion, qa_confidence,
                       admin_insight, created_at
                FROM quality_issues
                WHERE id = $1
                """,
                issue_id,
            )

            if not row:
                await self._fail_fix(pool, issue_id, f"Quality issue {issue_id} not found")
                return

            issue = dict(row)
            agent_name = issue.get("agent_name")
            tenant_id = str(issue["tenant_id"])

            if not agent_name:
                await self._fail_fix(pool, issue_id, "Issue has no associated agent name")
                return

            # Build a proposal text from the QA analysis + admin insight
            proposal_parts = []
            if issue.get("qa_analysis"):
                proposal_parts.append(f"Análisis QA: {issue['qa_analysis']}")
            if issue.get("qa_suggestion"):
                proposal_parts.append(f"Sugerencia QA: {issue['qa_suggestion']}")
            if issue.get("admin_insight"):
                proposal_parts.append(f"Insight del admin: {issue['admin_insight']}")
            if issue.get("error_message"):
                proposal_parts.append(f"Error: {issue['error_message']}")

            proposals = "\n".join(proposal_parts) if proposal_parts else issue.get("error_message", "")

            # Create a review cycle to track this fix
            now = datetime.now(timezone.utc)
            cycle_id = await pool.fetchval(
                """
                INSERT INTO qa_review_cycles (
                    tenant_id, triggered_by, period_start, period_end, status
                )
                VALUES ($1, 'admin-fix', $2, $3, 'running')
                RETURNING id
                """,
                tenant_id,
                now,
                now,
            )

            revision = await self._improve_agent_prompt(
                tenant_id=tenant_id,
                cycle_id=str(cycle_id),
                agent_name=agent_name,
                agent_issues=[issue],
                proposals=proposals,
                triggered_by="admin-fix",
            )

            if not revision:
                await self._complete_cycle(
                    cycle_id, 1, 0,
                    {"message": "Claude determined no prompt change needed"},
                )
                await self._fail_fix(
                    pool, issue_id,
                    "Claude determinó que no hay cambios necesarios en el prompt.",
                )
                return

            await self._complete_cycle(cycle_id, 1, 1, {"fix_applied": True})

            # Mark issue as completed with result
            await pool.execute(
                """
                UPDATE quality_issues
                SET fix_status = 'completed',
                    fix_error = NULL,
                    fix_result = $2,
                    is_resolved = true,
                    resolved_at = NOW(),
                    resolved_by = 'admin-fix',
                    resolution_notes = $3
                WHERE id = $1
                """,
                issue_id,
                json.dumps(revision),
                f"Fix aplicado. Revision: {revision['revision_id']}",
            )

            logger.info(
                "Single issue fix completed",
                issue_id=issue_id,
                revision_id=revision.get("revision_id"),
            )

        except Exception as e:
            logger.error("Single issue fix failed", issue_id=issue_id, error=str(e))
            await self._fail_fix(pool, issue_id, str(e))

    async def _fail_fix(
        self, pool: Any, issue_id: str, error_message: str
    ) -> None:
        """Mark a fix as failed in the database."""
        await pool.execute(
            """
            UPDATE quality_issues
            SET fix_status = 'failed', fix_error = $2
            WHERE id = $1
            """,
            issue_id,
            error_message,
        )
