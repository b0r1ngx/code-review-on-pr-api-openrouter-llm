import os, sys
import requests

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
PR_NUMBER = os.getenv("PR_NUMBER")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/pulls/{PR_NUMBER}"
GITHUB_COMMENTS_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/issues/{PR_NUMBER}/comments"


def get_pr_diff() -> str:
    print("get_pr_diff()")
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3.diff"}
    response = requests.get(url=GITHUB_API_URL, headers=headers)
    response.raise_for_status()
    diff = response.text.strip()

    if not diff:
        print("No diff found. Skipping review.")
        sys.exit(0)

    return diff


def get_code_review(diff: str) -> str:
    print("get_code_review()")
    system_prompt = """
You are an expert software engineer performing a code review.
With DEEP knowledge of refactoring to ideal code maintainability and readability.
Know Clean Architecture and Design Patterns. Your code exhibits MASTERY through SIMPLICITY.

Review the following git DIFF and provide concise, actionable feedback.
Focus on bugs, security issues, performance, clarity, and style.

Only respond with review comments. Do not add introductions or summaries.""".strip()
    prompt = f"DIFF:\n{diff}"

    llm_response = requests.post(
        url=OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            # "temperature": 0.2
        }
    )

    if llm_response.status_code != 200:
        print(f"OpenRouter error: {llm_response.status_code} - {llm_response.text}")
        sys.exit(1)

    code_review = llm_response.json()["choices"][0]["message"]["content"].strip()
    return code_review


def post_comment_to_pr(code_review: str):
    print("post_comment_to_pr()")
    if code_review:
        comment = f"""## 🔍 Code Review\n\n{code_review}"""
        comment_response = requests.post(
            url=GITHUB_COMMENTS_URL,
            headers={"Authorization": f"token {GITHUB_TOKEN}", "Content-Type": "application/json"},
            json={"body": comment}
        )
        if comment_response.status_code == 201:
            print("Comment posted successfully.")
        else:
            print(f"Failed to post comment: {comment_response.status_code}")
    else:
        print("No significant issues found. Skipping comment.")


if __name__ == '__main__':
    diff = get_pr_diff()
    code_review = get_code_review(diff)
    post_comment_to_pr(code_review)
