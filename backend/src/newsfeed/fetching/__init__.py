"""The shared polite HTTP fetcher: every outbound request goes through here (CLAUDE.md rule 6)."""

from newsfeed.fetching.http_fetcher import DEFAULT_USER_AGENT, HttpFetcher

__all__ = ["DEFAULT_USER_AGENT", "HttpFetcher"]
