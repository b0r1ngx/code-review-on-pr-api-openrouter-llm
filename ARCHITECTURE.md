# Architecture

Internal architecture reference for the AI Code Review GitHub Action.

---

## Table of Contents

- [Overview](#overview)
- [Data Flow](#data-flow)
- [Pydantic Models](#pydantic-models)
- [Architecture Decisions](#architecture-decisions)
- [Security Measures](#security-measures)
- [LLM Integration](#llm-integration)
- [Environment Variables](#environment-variables)
- [Dependencies](#dependencies)
- [Test Suite](#test-suite)
- [CI/CD](#cicd)

---

## Overview

Single-file Python tool (`main.py`) that runs as a GitHub Action. It fetches PR diffs from the GitHub API, sends them to an LLM via the OpenRouter API, parses the structured JSON response into Pydantic models, and posts a formatted Markdown review comment back to the PR.

The LLM is prompted to act as an "extremely strict senior software architect" and returns issues categorized by severity (`Critical`, `Major`, `Minor`, `Nitpick`) across four review categories: security, maintainability, readability, and performance.

---

## Data Flow

```
validate_env_vars() -> config dict
    |
    v
get_pr_diff(config) -> diff string (max 50,000 chars)
    |
    v
get_pr_metadata(config) -> {title, body} from GitHub API
    |
    v
get_repo_context() -> README.md + CONTRIBUTING.md + AGENTS.md content
    |
    v
analyze_diff(config, diff, pr_metadata, repo_context) -> CodeReviewResult (Pydantic)
    |
    v
format_review_comment(review) -> Markdown string
    |
    v
post_comment_to_pr(config, comment) -> GitHub API
```

**`main()`** orchestrates this pipeline. Every function receives its dependencies as arguments; nothing reads global state or environment variables directly after the initial `validate_env_vars()` call. Errors propagate as exceptions and are caught centrally in `main()`.

---

## Pydantic Models

### `ReviewIssue`

Represents a single issue found during review.

| Field | Type | Description |
|-------|------|-------------|
| `file_path` | `str` | Exact file path where the issue was found |
| `line` | `str` | Line number or range (e.g., `"42"` or `"42-45"`) |
| `severity` | `Literal["Critical", "Major", "Minor", "Nitpick"]` | Issue severity level |
| `description` | `str` | Explanation of the issue and why it violates best practices |
| `suggestion` | `str` | Actionable code suggestion or snippet to fix the issue |

### `CodeReviewResult`

Top-level review output. Issues are grouped by category.

| Field | Type | Description |
|-------|------|-------------|
| `has_issues` | `bool` | `True` only if actionable issues exist |
| `security` | `List[ReviewIssue]` | Vulnerabilities, OWASP Top 10, injections, exposed secrets |
| `maintainability` | `List[ReviewIssue]` | Architecture, SOLID, design patterns, tight coupling |
| `readability` | `List[ReviewIssue]` | Clean code, KISS, DRY, naming, confusing logic |
| `performance` | `List[ReviewIssue]` | Algorithmic complexity, N+1 queries, memory leaks |

The full Pydantic JSON schema is injected into the LLM system prompt so the model knows the exact output structure.

---

## Architecture Decisions

### 1. No global state

Environment variables are read once in `validate_env_vars()` and returned as a config dict. Every function receives what it needs through arguments. This makes functions independently testable and eliminates hidden coupling.

### 2. Exceptions, not sys.exit

Utility functions raise `ValueError` or `RuntimeError` on failure. The top-level `main()` function catches these and calls `sys.exit(1)`. This keeps control flow explicit and test-friendly -- tests can assert on exceptions without intercepting process exits.

### 3. Pydantic for structured output

`CodeReviewResult` and `ReviewIssue` enforce strict typing on LLM responses. Invalid or missing fields are caught at parse time rather than downstream. The JSON schema is also injected into the prompt, giving the LLM a formal contract to follow.

### 4. Deep context injection

The LLM receives more than just the diff:

- PR title and body (in `<pr_title>` / `<pr_body>` XML tags)
- Repository guideline files: `README.md`, `CONTRIBUTING.md`, `AGENTS.md` (in `<repo_context>` XML tags)
- The diff itself (in `<diff>` XML tags)

This gives the model enough context to evaluate changes against project-specific conventions.

### 5. requests, not httpx

This is a short-lived CI script, not a long-running server. Synchronous HTTP via `requests` is simpler and sufficient. There is no benefit to async here.

### 6. Single file

The entire tool lives in `main.py`. This is deliberate -- it simplifies adoption as a GitHub Action (one file to copy, no package structure to manage) and keeps the dependency surface minimal.

---

## Security Measures

### Input truncation

| Input | Max Length | Purpose |
|-------|-----------|---------|
| PR diff | 50,000 chars | Prevents DoS from massive diffs |
| PR title | 200 chars | Limits metadata injection surface |
| PR body | 2,000 chars | Limits metadata injection surface |
| Repo context files | 10,000 chars each | Prevents oversized context |

### Output sanitization

Three sanitization functions handle different contexts:

- **`sanitize_markdown()`** -- For Markdown prose sections. Escapes HTML entities, collapses triple backticks (prevents fence breakout), strips links, and injects zero-width spaces into `@mentions` to prevent GitHub notification spam.
- **`sanitize_code_snippet()`** -- For content inside code blocks. Escapes HTML entities but preserves backticks and `@` symbols, which are syntactically valid in code.
- **`get_safe_code_fence()`** -- Generates variable-length backtick fencing. If content contains triple backticks, the fence length increases to prevent code fence breakout attacks.

### Prompt injection defense

The system prompt explicitly instructs the LLM to treat content within `<pr_title>`, `<pr_body>`, `<repo_context>`, and `<diff>` tags as untrusted data and ignore any embedded instructions. The user prompt repeats this warning.

### Input validation

- **`GITHUB_REPOSITORY`** -- Validated against a strict `owner/repo` regex pattern.
- **`PR_NUMBER`** -- Must consist entirely of digits. Prevents path traversal in API URL construction.

---

## LLM Integration

| Setting | Value |
|---------|-------|
| Provider | OpenRouter API |
| Endpoint | `https://openrouter.ai/api/v1/chat/completions` |
| Default model | `openai/gpt-4` (configurable via `OPENROUTER_MODEL`) |
| JSON mode | `response_format: {type: json_object}` (configurable via `OPENROUTER_USE_JSON_FORMAT`, default `true`) |

### Request structure

Two-message conversation:

1. **System prompt** -- Reviewer persona, full Pydantic JSON schema, list of severity levels, instructions to ignore embedded commands in data blocks.
2. **User prompt** -- PR metadata in XML tags, repo context in XML tags, diff in XML tags, and a reminder to return only valid JSON.

### Response parsing

`extract_json_from_llm_response()` handles messy LLM output:

1. Strips markdown code fences (`` ```json ... ``` ``) if present.
2. Parses the result with `json.loads()`.
3. Validates against `CodeReviewResult` via Pydantic.

If parsing or validation fails, the function returns `None`, and `main()` detects this and exits with a non-zero status (`sys.exit(1)`).

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | -- | OpenRouter API key for LLM access |
| `GITHUB_TOKEN` | Yes | -- | GitHub token with PR read/write permissions |
| `GITHUB_REPOSITORY` | Yes | -- | Repository in `owner/repo` format |
| `PR_NUMBER` | Yes | -- | Pull request number (digits only) |
| `OPENROUTER_MODEL` | No | `openai/gpt-4` | LLM model identifier |
| `OPENROUTER_USE_JSON_FORMAT` | No | `true` | Enable JSON response format mode |

All required variables are validated at startup. Missing or malformed values cause an immediate `ValueError`.

---

## Dependencies

Production dependencies (`requirements.txt`):

| Package | Version | Purpose |
|---------|---------|---------|
| `requests` | `~=2.32.5` | HTTP client for GitHub and OpenRouter APIs |
| `pydantic` | `>=2.0.0` | Structured output validation for LLM responses |

Development dependencies (`requirements-dev.txt`, includes production deps via `-r requirements.txt`):

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | `>=8.0.0` | Test framework |
| `pytest-mock` | `>=3.12.0` | Mocking utilities for tests |

No transitive dependencies beyond what these packages bring. Standard library modules used: `os`, `sys`, `json`, `logging`, `re`, `typing`.

---

## Test Suite

57 tests in `test_main.py` with 99% code coverage. All tests are fully mocked -- no network calls are made.

### Coverage areas

- Environment variable validation (missing vars, malformed values)
- PR diff fetching (success, HTTP errors, truncation)
- PR metadata retrieval (success, missing fields, HTTP errors)
- Repository context loading (file present, file missing, truncation)
- JSON extraction from LLM responses (clean JSON, fenced JSON, invalid JSON)
- Diff analysis (success, API errors, parse failures)
- Comment formatting (issues present, no issues, severity ordering)
- PR comment posting (success, HTTP errors)
- Sanitization functions (HTML escaping, fence breakout, mention protection)
- `main()` flow (happy path, all error paths)

---

## CI/CD

### `.github/workflows/unit-test-on-pr.yml`

Runs `pytest` with coverage reporting on every pull request. Ensures no PR merges with failing tests.

### `.github/workflows/code-review-on-pr.yml`

Dogfooding workflow. Runs `main.py` against its own PRs, so the tool reviews changes to itself. Provides a continuous integration feedback loop on review quality.
