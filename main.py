import os
import sys
import json
import logging
import re
from typing import Optional, List
import requests
from pydantic import BaseModel, Field, ValidationError

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Environment Variables
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
PR_NUMBER = os.getenv("PR_NUMBER")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-4")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def get_github_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_REPOSITORY}/pulls/{PR_NUMBER}"


def get_github_comments_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues/{PR_NUMBER}/comments"


class ReviewIssue(BaseModel):
    file_path: str = Field(description="The exact file path where the issue was found")
    line: str = Field(description="The line number or range (e.g., '42' or '42-45')")
    severity: str = Field(description="Must be one of: Critical, Major, Minor, Nitpick")
    description: str = Field(description="Detailed, brutal explanation of the issue and why it violates best practices")
    suggestion: str = Field(description="Actionable code suggestion or code snippet to fix the issue")


class CodeReviewResult(BaseModel):
    has_issues: bool = Field(description="Set to true ONLY if there are actionable, significant issues. False if the code is perfectly fine.")
    security: List[ReviewIssue] = Field(default_factory=list, description="Vulnerabilities, OWASP Top 10, injections, exposed secrets.")
    maintainability: List[ReviewIssue] = Field(default_factory=list, description="Architecture, SOLID, Design Patterns, tight coupling.")
    readability: List[ReviewIssue] = Field(default_factory=list, description="Clean Code, KISS, DRY, bad naming, confusing logic.")
    performance: List[ReviewIssue] = Field(default_factory=list, description="Algorithmic complexity, N+1 queries, memory leaks, useless loops.")


def validate_env_vars() -> None:
    required = {
        "OPENROUTER_API_KEY": OPENROUTER_API_KEY,
        "GITHUB_TOKEN": GITHUB_TOKEN,
        "GITHUB_REPOSITORY": GITHUB_REPOSITORY,
        "PR_NUMBER": PR_NUMBER
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)
        
    if not str(PR_NUMBER).isdigit():
        logger.error("PR_NUMBER must be digits.")
        sys.exit(1)
        
    if not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", str(GITHUB_REPOSITORY)):
        logger.error("GITHUB_REPOSITORY must match 'owner/repo' format.")
        sys.exit(1)


def get_pr_diff() -> Optional[str]:
    """Fetches the Pull Request diff from GitHub."""
    logger.info("Fetching PR diff...")
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3.diff"
    }
    try:
        response = requests.get(url=get_github_api_url(), headers=headers, timeout=15)
        response.raise_for_status()
        diff = response.text.strip()
        
        if not diff:
            logger.info("No diff found in PR. Skipping review.")
            return None
            
        max_size = 50000
        if len(diff) > max_size:
            logger.warning(f"Diff is too large ({len(diff)} chars). Truncating to {max_size} chars.")
            diff = diff[:max_size]
            
        return diff
    except requests.RequestException as e:
        logger.error(f"Failed to fetch PR diff: {e}")
        sys.exit(1)


def extract_json_from_llm_response(content: str) -> str:
    """Cleans up markdown JSON wrapping from LLM responses."""
    content = content.strip()
    match = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return content


def analyze_diff(diff: str) -> Optional[CodeReviewResult]:
    """Sends the diff to the LLM for a strict, architect-level code review."""
    logger.info(f"Analyzing diff with LLM using model: {OPENROUTER_MODEL}...")
    
    schema_json = json.dumps(CodeReviewResult.model_json_schema(), indent=2)
    system_prompt = f"""
You are an EXTREMELY STRICT, highly critical senior software architect performing a code review.
You possess deep knowledge of Clean Architecture, SOLID principles, OWASP Top 10, and advanced performance optimizations.
Your goal is to relentlessly find architectural flaws, security vulnerabilities, and messy code in the provided git diff.

Do NOT praise the code. Do NOT nitpick tiny subjective formatting. DO ruthlessly attack bad design, security flaws, poor maintainability, duplication, and inefficiencies.
If you find issues, classify them rigidly. If the code is genuinely flawless, set `has_issues` to false.

You must output ONLY valid JSON matching the following schema. Do NOT include Markdown formatting, greetings, or explanations outside the JSON.

Expected JSON Schema:
{schema_json}
"""

    messages = [
        {"role": "system", "content": system_prompt.strip()},
        {"role": "user", "content": f"Review this git diff and output JSON strictly adhering to the schema:\n\n{diff}"}
    ]

    try:
        response = requests.post(
            url=OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": messages,
                "response_format": {"type": "json_object"}
            },
            timeout=120
        )
        response.raise_for_status()
        
        response_data = response.json()
        raw_content = response_data["choices"][0]["message"]["content"]
        
        json_content = extract_json_from_llm_response(raw_content)
        parsed_json = json.loads(json_content)
        return CodeReviewResult(**parsed_json)

    except requests.RequestException as e:
        logger.error(f"OpenRouter API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"API Response Body: {e.response.text}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON. Error: {e}")
        return None
    except ValidationError as e:
        logger.error(f"LLM response did not match expected Pydantic schema: {e}")
        return None


def sanitize_markdown(text: str) -> str:
    """Sanitizes text to prevent markdown breakout and malicious links."""
    # Escape triple backticks to avoid breaking the markdown block
    text = text.replace("```", r"\`\`\`")
    # Strip unexpected markdown links
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    return text


def format_review_comment(review: CodeReviewResult) -> str:
    """Formats the structured code review result into a beautiful Markdown comment."""
    if not review.has_issues:
        return ""
        
    lines = [
        "## 🔍 Strict Architectural Code Review\n",
        "> ⚠️ **Note:** This review focuses strictly on Security, Maintainability, Readability, and Performance. "
        "Minor subjective style choices have been omitted. Actionable issues follow below.\n"
    ]
    
    sections = [
        ("🚨 Security", review.security),
        ("🛠️ Maintainability", review.maintainability),
        ("📖 Readability", review.readability),
        ("🚀 Performance", review.performance)
    ]
    
    total_issues = 0
    for title, issues in sections:
        if issues:
            lines.append(f"### {title}")
            for issue in issues:
                total_issues += 1
                desc_safe = sanitize_markdown(issue.description)
                sugg_safe = sanitize_markdown(issue.suggestion)
                lines.append(f"**File:** `{issue.file_path}` (Line `{issue.line}`) | **Severity:** {issue.severity}")
                lines.append(f"**Issue:** {desc_safe}\n")
                lines.append("**Suggestion:**")
                lines.append(f"```\n{sugg_safe}\n```\n")
            lines.append("---\n")
            
    if total_issues == 0:
        return ""
        
    return "\n".join(lines)


def post_comment_to_pr(comment_body: str) -> None:
    """Posts the formatted markdown review to the GitHub Pull Request."""
    if not comment_body:
        logger.info("No significant issues to report. Skipping PR comment.")
        return
        
    logger.info("Posting review comment to PR...")
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(
            url=get_github_comments_url(),
            headers=headers,
            json={"body": comment_body},
            timeout=15
        )
        response.raise_for_status()
        logger.info("Successfully posted review comment to PR.")
    except requests.RequestException as e:
        logger.error(f"Failed to post comment to PR: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"API Response Body: {e.response.text}")
        sys.exit(1)


def main() -> None:
    validate_env_vars()
    
    diff = get_pr_diff()
    if not diff:
        return
        
    review_result = analyze_diff(diff)
    if not review_result:
        logger.error("Failed to generate a valid code review.")
        sys.exit(1)
        
    if not review_result.has_issues:
        logger.info("LLM determined there are no actionable issues. Exiting peacefully.")
        return
        
    comment_body = format_review_comment(review_result)
    
    if comment_body:
        post_comment_to_pr(comment_body)
    else:
        logger.info("Comment body was empty after formatting. Exiting.")


if __name__ == '__main__':
    main()
