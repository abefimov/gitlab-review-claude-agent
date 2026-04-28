import re
import pytest
from claude_reviewer.gitlab_client import parse_mr_url, GitLabMRRef


@pytest.mark.parametrize("url,expected", [
    (
        "https://gitlab.example.com/example/mobile/sample-ios/-/merge_requests/249",
        GitLabMRRef(project_path="example/mobile/sample-ios", mr_iid=249),
    ),
    (
        "https://gitlab.example/group/sub/proj/-/merge_requests/1",
        GitLabMRRef(project_path="group/sub/proj", mr_iid=1),
    ),
])
def test_parse_mr_url(url, expected):
    assert parse_mr_url(url) == expected


def test_parse_mr_url_rejects_non_mr():
    with pytest.raises(ValueError):
        parse_mr_url("https://gitlab.example.com/example/mobile/sample-ios/-/issues/1")
