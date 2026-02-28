# AI Code Review on Pull Requests

Automated, LLM-powered code review that runs on every Pull Request via GitHub Actions. It acts as an extremely strict senior software architect, checking your code for security vulnerabilities, architectural flaws, readability issues, and performance problems -- then posts a formatted review comment directly on the PR.

Uses [OpenRouter](https://openrouter.ai/) to route requests to any LLM (default: GPT-4). Bring your own API key, pick your model, and get actionable code review on every PR in under a minute.

---

## How It Works

1. A Pull Request is opened (or updated) in your repository
2. GitHub Actions triggers the workflow
3. The script fetches the PR diff and metadata via the GitHub API
4. The diff is sent to an LLM through OpenRouter with a strict code review prompt
5. The LLM returns structured JSON with categorized issues
6. A formatted Markdown comment is posted directly on the PR

---

## Setup (< 5 minutes)

### 1. Copy the script files

Copy these two files into your repository root:

- **`main.py`** -- the code review script
- **`requirements.txt`** -- production Python dependencies (`requests`, `pydantic>=2.0.0`)

### 2. Create the workflow file

Create `.github/workflows/code-review-on-pr.yml` in your repository:

```yaml
name: AI Code Review on PR

on:
  pull_request:
    types: [opened, reopened, synchronize]

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    name: Run AI Code Review
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

      - name: Run Code Review
        run: python main.py
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
          OPENROUTER_MODEL: ${{ vars.OPENROUTER_MODEL }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITHUB_REPOSITORY: ${{ github.repository }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
```

### 3. Configure secrets and variables

In your GitHub repository, go to **Settings > Secrets and variables > Actions** and add:

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `OPENROUTER_API_KEY` | Secret | Yes | Your OpenRouter API key. Get one at [openrouter.ai](https://openrouter.ai/) |
| `OPENROUTER_MODEL` | Variable | No | LLM model to use (see [Model Selection](#model-selection) below) |

`GITHUB_TOKEN` is automatically provided by GitHub Actions -- no setup needed.

### 4. Open a Pull Request

That's it. The next time a PR is opened, reopened, or updated, the workflow will run and post a review comment if it finds issues.

---

## Review Categories

Every issue the reviewer finds is classified into one of four categories:

| Category | What It Checks |
|----------|---------------|
| **Security** | OWASP Top 10, SQL/XSS injections, exposed secrets, authentication flaws |
| **Maintainability** | SOLID principles, design patterns, tight coupling, god objects |
| **Readability** | Clean Code, DRY, KISS, unclear naming, confusing logic |
| **Performance** | Algorithmic complexity, N+1 queries, memory leaks, unnecessary allocations |

Each issue includes:

- **File path** and **line number** pointing to the exact location
- **Severity** -- Critical, Major, Minor, or Nitpick
- **Description** -- what's wrong and why it matters
- **Suggestion** -- actionable code snippet to fix it

If the code is clean, the reviewer stays silent. No noise.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | -- | OpenRouter API key |
| `GITHUB_TOKEN` | Yes | -- | GitHub token (auto-provided by Actions) |
| `GITHUB_REPOSITORY` | Yes | -- | `owner/repo` format (auto-provided by Actions) |
| `PR_NUMBER` | Yes | -- | Pull Request number (auto-provided by Actions) |
| `OPENROUTER_MODEL` | No | `openai/gpt-4` | LLM model identifier on OpenRouter |
| `OPENROUTER_USE_JSON_FORMAT` | No | `true` | Enable structured JSON response format |

---

## Model Selection

Set the `OPENROUTER_MODEL` variable to any model available on [OpenRouter](https://openrouter.ai/models). Examples:

| Model | Identifier |
|-------|-----------|
| GPT-4 (default) | `openai/gpt-4` |
| Claude 3.5 Sonnet | `anthropic/claude-3.5-sonnet` |
| Gemini 2.0 Flash | `google/gemini-2.0-flash` |
| DeepSeek Chat | `deepseek/deepseek-chat` |

Different models vary in cost, speed, and review quality. GPT-4 provides the most thorough reviews; lighter models like Gemini Flash are faster and cheaper for high-volume repos.

---

## Context Files

The reviewer automatically reads these files from your repository root (if they exist) to better understand your project's conventions:

- `README.md` -- project description and purpose
- `CONTRIBUTING.md` -- contribution guidelines and coding standards
- `AGENTS.md` -- AI agent instructions

This context is included in the prompt so the LLM can tailor its review to your project's specific patterns and rules.

---

## Security

The tool is designed with defense-in-depth against prompt injection and output abuse:

- **Diff size limit** -- truncated at 50,000 characters to prevent token abuse
- **PR metadata truncation** -- title capped at 200 characters, body at 2,000
- **HTML entity escaping** -- all output is sanitized before posting
- **Markdown injection prevention** -- `@mention` protection, link stripping, code fence breakout prevention
- **Prompt injection defenses** -- XML tag isolation of untrusted input, explicit LLM instructions to ignore embedded commands in diffs and PR metadata
- **Input validation** -- repository format verified by regex, PR number checked for digits only

---

## Running Tests

The test suite is fully self-contained with no network calls:

```bash
pip install -r requirements-dev.txt
pip install pytest-cov
pytest test_main.py --cov=main -v
```

57 tests, 99% coverage.

---

## Requirements

- Python 3.9+ (the CI workflow uses 3.11)
- `requests` -- HTTP client for GitHub and OpenRouter APIs
- `pydantic>=2.0.0` -- structured response parsing and validation
- An [OpenRouter](https://openrouter.ai/) API key (free tier available)

Dependencies are split across two files: `requirements.txt` (production deps for running `main.py`) and `requirements-dev.txt` (adds test deps: `pytest`, `pytest-mock`).
