import os
import sys
import json
import pytest
from pydantic import ValidationError
import requests

from main import (
    validate_env_vars,
    get_pr_diff,
    extract_json_from_llm_response,
    analyze_diff,
    format_review_comment,
    post_comment_to_pr,
    main,
    CodeReviewResult,
    ReviewIssue
)

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("GITHUB_TOKEN", "test-gh-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "user/repo")
    monkeypatch.setenv("PR_NUMBER", "42")
    # For testing, we ensure these are present
    import main as main_module
    monkeypatch.setattr(main_module, 'OPENROUTER_API_KEY', "test-or-key")
    monkeypatch.setattr(main_module, 'GITHUB_TOKEN', "test-gh-token")
    monkeypatch.setattr(main_module, 'GITHUB_REPOSITORY', "user/repo")
    monkeypatch.setattr(main_module, 'PR_NUMBER', "42")


def test_validate_env_vars_success(mock_env):
    # Should not raise any exception or sys.exit
    validate_env_vars()

def test_validate_env_vars_missing(monkeypatch, mocker):
    import main as main_module
    monkeypatch.setattr(main_module, 'OPENROUTER_API_KEY', "")
    with pytest.raises(SystemExit) as exc_info:
        validate_env_vars()
    assert exc_info.value.code == 1

def test_get_pr_diff_success(mock_env, mocker):
    mock_response = mocker.Mock()
    mock_response.text = "diff --git a/file b/file"
    mock_response.raise_for_status.return_value = None
    mock_get = mocker.patch("requests.get", return_value=mock_response)
    
    diff = get_pr_diff()
    assert diff == "diff --git a/file b/file"
    mock_get.assert_called_once_with(
        url="https://api.github.com/repos/user/repo/pulls/42",
        headers={"Authorization": "token test-gh-token", "Accept": "application/vnd.github.v3.diff"},
        timeout=15
    )

def test_get_pr_diff_empty(mock_env, mocker):
    mock_response = mocker.Mock()
    mock_response.text = "   \n "
    mock_response.raise_for_status.return_value = None
    mocker.patch("requests.get", return_value=mock_response)
    
    diff = get_pr_diff()
    assert diff is None

def test_get_pr_diff_request_exception(mock_env, mocker):
    mocker.patch("requests.get", side_effect=requests.RequestException("API down"))
    with pytest.raises(SystemExit) as exc_info:
        get_pr_diff()
    assert exc_info.value.code == 1

@pytest.mark.parametrize("input_text, expected", [
    ('{"key": "value"}', '{"key": "value"}'),
    ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
    ('```\n{"key": "value"}\n```', '{"key": "value"}'),
    ('   ```json\n{"key": "value"}\n```   ', '{"key": "value"}')
])
def test_extract_json_from_llm_response(input_text, expected):
    assert extract_json_from_llm_response(input_text) == expected

def test_analyze_diff_success(mock_env, mocker):
    valid_json = {
        "has_issues": True,
        "security": [
            {
                "file_path": "main.py",
                "line": "10",
                "severity": "Critical",
                "description": "SQL Injection",
                "suggestion": "Use parameterized queries"
            }
        ],
        "maintainability": [],
        "readability": [],
        "performance": []
    }
    
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(valid_json)}}]
    }
    mock_response.raise_for_status.return_value = None
    mocker.patch("requests.post", return_value=mock_response)
    
    result = analyze_diff("fake diff")
    assert result is not None
    assert result.has_issues is True
    assert len(result.security) == 1
    assert result.security[0].severity == "Critical"

def test_analyze_diff_request_exception(mock_env, mocker):
    mocker.patch("requests.post", side_effect=requests.RequestException("Timeout"))
    result = analyze_diff("fake diff")
    assert result is None

def test_analyze_diff_invalid_json(mock_env, mocker):
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "Not JSON at all"}}]
    }
    mocker.patch("requests.post", return_value=mock_response)
    result = analyze_diff("fake diff")
    assert result is None

def test_analyze_diff_validation_error(mock_env, mocker):
    invalid_schema_json = {
        "has_issues": 12345,  # something that definitely fails strict bool parsing or isn't a list where expected
        "security": "not a list"
    }
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(invalid_schema_json)}}]
    }
    mocker.patch("requests.post", return_value=mock_response)
    result = analyze_diff("fake diff")
    assert result is None

def test_format_review_comment_no_issues():
    review = CodeReviewResult(has_issues=False)
    comment = format_review_comment(review)
    assert comment == ""

def test_format_review_comment_with_issues():
    review = CodeReviewResult(
        has_issues=True,
        security=[
            ReviewIssue(
                file_path="auth.py",
                line="42",
                severity="Critical",
                description="Hardcoded password",
                suggestion="Use os.getenv()"
            )
        ]
    )
    comment = format_review_comment(review)
    assert "## 🔍 Strict Architectural Code Review" in comment
    assert "### 🚨 Security" in comment
    assert "**File:** `auth.py` (Line `42`) | **Severity:** Critical" in comment
    assert "Hardcoded password" in comment
    assert "Use os.getenv()" in comment

def test_post_comment_to_pr_success(mock_env, mocker):
    mock_response = mocker.Mock()
    mock_response.raise_for_status.return_value = None
    mock_post = mocker.patch("requests.post", return_value=mock_response)
    
    # Should not raise
    post_comment_to_pr("comment body")
    mock_post.assert_called_once_with(
        url="https://api.github.com/repos/user/repo/issues/42/comments",
        headers={"Authorization": "token test-gh-token", "Content-Type": "application/json"},
        json={"body": "comment body"},
        timeout=15
    )

def test_post_comment_to_pr_empty_body(mock_env, mocker):
    post_mock = mocker.patch("requests.post")
    post_comment_to_pr("")
    post_mock.assert_not_called()

def test_post_comment_to_pr_exception(mock_env, mocker):
    mocker.patch("requests.post", side_effect=requests.RequestException("Fail"))
    with pytest.raises(SystemExit) as exc_info:
        post_comment_to_pr("comment body")
    assert exc_info.value.code == 1

def test_main_flow_success(mock_env, mocker):
    mocker.patch("main.get_pr_diff", return_value="fake diff")
    review = CodeReviewResult(
        has_issues=True,
        security=[ReviewIssue(file_path="a.py", line="1", severity="Critical", description="bad", suggestion="fix")]
    )
    mocker.patch("main.analyze_diff", return_value=review)
    mock_post = mocker.patch("main.post_comment_to_pr")
    
    main()
    mock_post.assert_called_once()

def test_main_flow_no_diff(mock_env, mocker):
    mocker.patch("main.get_pr_diff", return_value=None)
    mock_analyze = mocker.patch("main.analyze_diff")
    main()
    mock_analyze.assert_not_called()

def test_main_flow_analyze_fails(mock_env, mocker):
    mocker.patch("main.get_pr_diff", return_value="fake diff")
    mocker.patch("main.analyze_diff", return_value=None)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1

def test_main_flow_no_issues(mock_env, mocker):
    mocker.patch("main.get_pr_diff", return_value="fake diff")
    mocker.patch("main.analyze_diff", return_value=CodeReviewResult(has_issues=False))
    mock_post = mocker.patch("main.post_comment_to_pr")
    main()
    mock_post.assert_not_called()

# Additional tests for uncovered lines

def test_validate_env_vars_invalid_pr_number(mock_env, monkeypatch):
    import main as main_module
    monkeypatch.setattr(main_module, 'PR_NUMBER', "not-digits")
    with pytest.raises(SystemExit) as exc_info:
        validate_env_vars()
    assert exc_info.value.code == 1

def test_validate_env_vars_invalid_github_repository(mock_env, monkeypatch):
    import main as main_module
    monkeypatch.setattr(main_module, 'GITHUB_REPOSITORY', "invalidrepo")
    with pytest.raises(SystemExit) as exc_info:
        validate_env_vars()
    assert exc_info.value.code == 1

def test_get_pr_diff_too_large(mock_env, mocker):
    mock_response = mocker.Mock()
    # Create a string larger than 50000 chars
    large_diff = "a" * 50005
    mock_response.text = large_diff
    mock_response.raise_for_status.return_value = None
    mocker.patch("requests.get", return_value=mock_response)
    
    diff = get_pr_diff()
    assert len(diff) == 50000
    assert diff == "a" * 50000

def test_analyze_diff_request_exception_with_response(mock_env, mocker):
    exc = requests.RequestException("API down")
    mock_resp = mocker.Mock()
    mock_resp.text = "API error body"
    exc.response = mock_resp
    mocker.patch("requests.post", side_effect=exc)
    
    result = analyze_diff("fake diff")
    assert result is None

def test_format_review_comment_empty_issues():
    review = CodeReviewResult(has_issues=True, security=[], maintainability=[], readability=[], performance=[])
    comment = format_review_comment(review)
    assert comment == ""

def test_post_comment_to_pr_exception_with_response(mock_env, mocker):
    exc = requests.RequestException("GitHub down")
    mock_resp = mocker.Mock()
    mock_resp.text = "GitHub error body"
    exc.response = mock_resp
    mocker.patch("requests.post", side_effect=exc)
    
    with pytest.raises(SystemExit) as exc_info:
        post_comment_to_pr("comment body")
    assert exc_info.value.code == 1

def test_main_empty_comment(mock_env, mocker):
    mocker.patch("main.get_pr_diff", return_value="fake diff")
    # Return a review result that produces an empty comment
    empty_review = CodeReviewResult(has_issues=True, security=[], maintainability=[], readability=[], performance=[])
    mocker.patch("main.analyze_diff", return_value=empty_review)
    mock_post = mocker.patch("main.post_comment_to_pr")
    
    # Should not post because format_review_comment returns ""
    main()
    mock_post.assert_not_called()

