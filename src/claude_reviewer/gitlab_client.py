from __future__ import annotations
import os
import re
from dataclasses import dataclass
import gitlab as pygitlab

from claude_reviewer.config import Config
from claude_reviewer.errors import ReviewerError
from claude_reviewer.types import MRRefs

MR_URL_RE = re.compile(
    r"^(?P<base>https?://[^/]+)/(?P<path>.+?)/-/merge_requests/(?P<iid>\d+)/?$"
)


@dataclass(frozen=True)
class GitLabMRRef:
    project_path: str
    mr_iid: int


def parse_mr_url(url: str) -> GitLabMRRef:
    m = MR_URL_RE.match(url.strip())
    if not m:
        raise ValueError(f"not a merge request URL: {url}")
    return GitLabMRRef(project_path=m.group("path"), mr_iid=int(m.group("iid")))


class GitLabClient:
    def __init__(self, cfg: Config, token: str | None = None):
        self.cfg = cfg
        self.token = token or os.environ.get("GITLAB_TOKEN")
        if not self.token:
            raise ReviewerError("GITLAB_TOKEN env is not set")
        self.gl = pygitlab.Gitlab(cfg.gitlab.base_url, private_token=self.token)

    def get_project(self, path_or_id: str | int):
        return self.gl.projects.get(path_or_id)

    def get_mr(self, project_path_or_id: str | int, mr_iid: int):
        return self.get_project(project_path_or_id).mergerequests.get(mr_iid)

    def get_diff_refs(self, mr) -> MRRefs:
        refs = mr.attributes.get("diff_refs") or {}
        try:
            return MRRefs(
                base_sha=refs["base_sha"],
                start_sha=refs["start_sha"],
                head_sha=refs["head_sha"],
            )
        except KeyError as e:
            raise ReviewerError(
                f"MR {getattr(mr, 'iid', '?')} is missing diff_refs ({e}); "
                "is it a draft without commits?"
            ) from e

    def list_opened_mrs(self, project_path_or_id, updated_after=None,
                        target_branches: list[str] | None = None):
        kwargs = {"state": "opened", "all": True}
        if updated_after:
            kwargs["updated_after"] = updated_after.isoformat()
        proj = self.get_project(project_path_or_id)
        mrs = proj.mergerequests.list(**kwargs)
        if target_branches:
            mrs = [m for m in mrs if m.target_branch in target_branches]
        return mrs

    def get_discussion(self, project_path_or_id, mr_iid: int, discussion_id: str):
        return self.get_mr(project_path_or_id, mr_iid).discussions.get(discussion_id)

    def list_discussions(self, mr):
        return mr.discussions.list(all=True)
