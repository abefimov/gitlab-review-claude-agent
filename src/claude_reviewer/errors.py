class ReviewerError(Exception):
    """Base exception for claude-reviewer."""

class ConfigError(ReviewerError):
    """Raised when config loading or validation fails."""

class ReviewFailed(ReviewerError):
    """Raised when the review pipeline cannot produce a valid output."""

class RepoError(ReviewerError):
    """Raised when a git operation on a managed repo fails."""

class ClaudeRunError(ReviewerError):
    """Raised when the `claude` CLI invocation fails or exits non-zero."""

class PostError(ReviewerError):
    """Raised when posting results to GitLab fails."""
