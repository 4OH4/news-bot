"""The rank stage rebuilds the interest profile and adds similarity (#323)."""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.models import (
    DEFAULT_USER_ID,
    Article,
    ExtractionStatus,
    Feedback,
    FeedbackEvent,
    InterestCentroid,
    Profile,
)
from newsfeed.ports import Repository
from newsfeed.rank.interests import InterestsConfig
from newsfeed.rank.personalisation import PersonalisationConfig
from newsfeed.rank.stage import rank_recent
from newsfeed.wiring import Services
from tests.fakes import FakeExtractor, FakeFetcher, InMemoryBlobStore, InMemoryRepository
from tests.rank.support import NOW, SMALL_PROFILE, config, feedback

MODEL = "fake-embedder"
AXES = {"alpha": 0, "beta": 1, "gamma": 2}


class ScriptedEmbedder:
    """Embeds a text as the axis of the first group word (alpha, beta, gamma) in it."""

    model = MODEL
    dimensions = 4

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        axis = next((i for word, i in AXES.items() if word in text), 3)
        return [1.0 if i == axis else 0.0 for i in range(4)]

    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]


class LoggingRepository(InMemoryRepository):
    """Records the order of the calls that matter, and each article write."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []
        self.upserts: list[str] = []

    def list_articles(
        self,
        *,
        status: ExtractionStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        source_ids: Collection[str] | None = None,
    ) -> list[Article]:
        self.events.append("list_articles" if since is not None else "list_all")
        return super().list_articles(status=status, since=since, limit=limit, source_ids=source_ids)

    def save_profile(self, profile: Profile) -> None:
        self.events.append("save_profile")
        super().save_profile(profile)

    def upsert_article(self, article: Article) -> None:
        self.upserts.append(article.title)
        super().upsert_article(article)


def services(
    repository: Repository, blobs: InMemoryBlobStore | LocalBlobStore | None = None
) -> Services:
    return Services(
        repository=repository,
        blobs=blobs or InMemoryBlobStore(),
        fetcher=FakeFetcher(),
        ingesters={},
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
    )


def article(
    group: str, name: str, *, age: timedelta = timedelta(hours=1), **changes: object
) -> Article:
    """An article of a vector group; the default age puts it in the 72 h rank window."""
    url = f"https://news.example.com/{group}-{name}"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": f"Story {group} {name}",
            "fetched_at": NOW - age,
            **changes,
        }
    )


def old(group: str, name: str, *, embedded: bool = True) -> Article:
    """An article from ten days ago, outside the rank window, as feedback usually points at."""
    extra: dict[str, object] = {"embedding": [1.0, 0.0, 0.0, 0.0], "embedding_model": MODEL}
    if group == "beta":
        extra["embedding"] = [0.0, 1.0, 0.0, 0.0]
    return article(group, name, age=timedelta(days=10), **(extra if embedded else {}))


def read_all(repo: Repository, articles: Sequence[Article]) -> None:
    for a in articles:
        repo.upsert_article(a)
        repo.save_feedback(feedback(a.id))


def preseeded_profile(*, built_ago: timedelta, feedback_ago: timedelta) -> Profile:
    return Profile(
        embedding_model=MODEL,
        centroids=[InterestCentroid(vector=[1.0, 0.0, 0.0, 0.0], weight=3.0, size=3)],
        built_at=NOW - built_ago,
        last_feedback_at=NOW - feedback_ago,
        events_used=3,
    )


def rank(
    s: Services,
    cfg: PersonalisationConfig,
    embedder: ScriptedEmbedder | None = None,
    *,
    now: datetime = NOW,
    batch: int = 32,
) -> dict[str, int]:
    return rank_recent(
        s,
        InterestsConfig(),
        since=now - timedelta(hours=72),
        clock=lambda: now,
        embedder=embedder or ScriptedEmbedder(),
        embed_batch_size=batch,
        personalisation=cfg,
    ).counts()


def by_title(repo: Repository) -> dict[str, Article]:
    return {a.title: a for a in repo.list_articles()}


def test_a_rebuilt_profile_adds_similarity_and_leaves_the_score_alone(tmp_path: Path) -> None:
    window = [article("alpha", "w"), article("beta", "w")]
    plain = InMemoryRepository()
    for a in window:
        plain.upsert_article(a)
    rank(services(plain), config(tmp_path, SMALL_PROFILE))  # no feedback, so no profile

    repo = InMemoryRepository()
    for a in window:
        repo.upsert_article(a)
    read_all(repo, [old("alpha", "1"), old("alpha", "2"), old("alpha", "3")])
    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert counts["profile_rebuilt"] == 1
    assert counts["with_similarity"] == 2
    saved = repo.get_profile(DEFAULT_USER_ID)
    assert saved is not None
    assert saved.centroids
    assert saved.embedding_model == MODEL
    got, baseline = by_title(repo), by_title(plain)
    for title in ("Story alpha w", "Story beta w"):
        assert got[title].score == baseline[title].score
        assert got[title].score_breakdown["similarity"] >= 1.0
        assert "similarity_raw" in got[title].score_breakdown
        assert "similarity" not in baseline[title].score_breakdown
    assert got["Story alpha w"].score_breakdown["similarity"] > 1.0
    assert got["Story beta w"].score_breakdown["similarity"] == 1.0
    assert got["Story alpha w"].score_breakdown["similarity_raw"] == pytest.approx(1.0)


def test_with_too_few_positive_stories_no_article_gets_similarity(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    repo.upsert_article(article("alpha", "w"))
    read_all(repo, [old("alpha", "1")])

    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))  # min_events is 2

    assert counts["with_similarity"] == 0
    saved = repo.get_profile(DEFAULT_USER_ID)
    assert saved is not None
    assert saved.centroids == []
    assert saved.embedding_model == MODEL
    assert "similarity" not in by_title(repo)["Story alpha w"].score_breakdown


def test_the_rebuild_runs_before_the_rank_snapshot(tmp_path: Path) -> None:
    repo = LoggingRepository()
    repo.upsert_article(article("alpha", "w"))
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])

    rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert "save_profile" in repo.events
    assert repo.events.index("save_profile") < repo.events.index("list_articles")


def test_an_article_in_the_window_and_in_feedback_is_not_embedded_by_the_rebuild(
    tmp_path: Path,
) -> None:
    repo = LoggingRepository()
    both = article("alpha", "both")
    repo.upsert_article(both)
    repo.save_feedback(feedback(both.id))
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])
    repo.upserts.clear()
    embedder = ScriptedEmbedder()

    rank(services(repo), config(tmp_path, SMALL_PROFILE), embedder)

    assert sum("Story alpha both" in t for t in embedder.texts()) == 1
    assert repo.upserts.count("Story alpha both") == 1  # one write, with score and embedding
    stored = by_title(repo)["Story alpha both"]
    assert stored.embedding is not None
    assert stored.score is not None
    assert "similarity" in stored.score_breakdown


def test_similarity_is_computed_after_every_embedding_batch(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=1), feedback_ago=timedelta(hours=2))
    )
    for i in range(3):  # interleaved, so a batch of two mixes the groups
        repo.upsert_article(article("alpha", str(i), age=timedelta(hours=1 + 2 * i)))
        repo.upsert_article(article("beta", str(i), age=timedelta(hours=2 + 2 * i)))

    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE), batch=2)

    assert counts["profile_rebuilt"] == 0
    assert counts["with_similarity"] == 6
    got = by_title(repo)
    for i in range(3):
        # Against all six: alpha shares ranks 4 to 6 (r = 0.8), beta ranks 1 to 3 (r = 0.2).
        assert got[f"Story alpha {i}"].score_breakdown["similarity"] == pytest.approx(2.2)
        assert got[f"Story beta {i}"].score_breakdown["similarity"] == 1.0


def test_the_profile_is_rebuilt_only_after_rebuild_min_hours(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=2), feedback_ago=timedelta(hours=3))
    )
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])  # newer than the profile
    s, cfg = services(repo), config(tmp_path, SMALL_PROFILE)

    assert rank(s, cfg)["profile_rebuilt"] == 0
    assert rank(s, cfg, now=NOW + timedelta(hours=5))["profile_rebuilt"] == 1  # built 7 h ago


def test_feedback_for_a_deleted_article_does_not_fail_the_rebuild(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])
    repo.save_feedback(feedback("0123456789abcdef"))  # no such article

    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert counts["profile_rebuilt"] == 1
    assert counts["errors"] == 0


def test_a_rebuild_embeds_at_most_200_old_feedback_articles(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    read_all(repo, [old("gamma", f"{i:03d}", embedded=False) for i in range(250)])
    s, cfg = services(repo), config(tmp_path, SMALL_PROFILE)

    def embedded() -> int:
        return sum(a.embedding is not None for a in repo.list_articles())

    rank(s, cfg)
    assert embedded() == 200
    # Newer than every event the first rebuild used, so the second run sees new feedback.
    repo.save_feedback(
        feedback(old("gamma", "000", embedded=False).id, FeedbackEvent.OPEN, None, at=NOW)
    )
    rank(s, cfg, now=NOW + timedelta(hours=7))
    assert embedded() == 250


def test_a_rebuild_logs_no_vector_title_or_feedback_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = InMemoryRepository()
    feedback_articles = [old("alpha", "1"), old("alpha", "2", embedded=False)]
    read_all(repo, feedback_articles)

    with caplog.at_level(logging.DEBUG):
        counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert counts["profile_rebuilt"] == 1
    assert caplog.records
    for record in caplog.records:
        values = [record.getMessage(), *vars(record).values()]
        for value in values:
            assert not (isinstance(value, list) and any(isinstance(v, float) for v in value))
            for a in feedback_articles:
                assert a.title not in str(value)
                assert a.id not in str(value)


def test_a_second_run_reuses_the_saved_profile_in_the_real_repository(tmp_path: Path) -> None:
    with SqliteRepository(tmp_path / "n.db") as repo:
        s = services(repo, LocalBlobStore(tmp_path / "blobs"))
        cfg = config(tmp_path, SMALL_PROFILE)
        repo.upsert_article(article("alpha", "w"))
        repo.upsert_article(article("beta", "w"))
        read_all(repo, [old("alpha", "1"), old("alpha", "2")])

        first = rank(s, cfg)
        saved = repo.get_profile(DEFAULT_USER_ID)
        second = rank(s, cfg, now=NOW + timedelta(hours=1))

        assert first["profile_rebuilt"] == 1
        assert second["profile_rebuilt"] == 0
        assert second["with_similarity"] == 2
        assert repo.get_profile(DEFAULT_USER_ID) == saved
        assert saved is not None
        assert saved.embedding_model == MODEL


@pytest.mark.parametrize("event", [FeedbackEvent.OPEN, FeedbackEvent.READ])
def test_an_article_the_owner_opened_or_read_gets_no_similarity_lift(
    tmp_path: Path, event: FeedbackEvent
) -> None:
    repo = InMemoryRepository()
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=1), feedback_ago=timedelta(hours=2))
    )
    # Same vector, top raw. The unseen one is fetched too long before to share the seen one's
    # story (#367), but published at the same time so the other factors match.
    published = NOW - timedelta(hours=1)
    seen = article("alpha", "seen", published_at=published)
    unseen = article("alpha", "unseen", age=timedelta(hours=60), published_at=published)
    for a in (seen, unseen, article("beta", "1"), article("beta", "2")):
        repo.upsert_article(a)
    seconds = 60 if event is FeedbackEvent.READ else None
    repo.save_feedback(feedback(seen.id, event, seconds, ago=timedelta(minutes=30)))

    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert counts["profile_rebuilt"] == 0  # the preseeded profile is used as it is
    got = by_title(repo)
    seen_row, unseen_row = got["Story alpha seen"], got["Story alpha unseen"]
    assert seen_row.score_breakdown["similarity"] == 1.0
    assert "already_read" in seen_row.score_breakdown
    assert seen_row.score == unseen_row.score  # its other factors are unchanged
    assert unseen_row.score_breakdown["similarity"] > 1.0
    assert "already_read" not in unseen_row.score_breakdown


def test_feedback_dated_in_the_future_does_not_stop_later_rebuilds(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    seen = [old("alpha", "1"), old("alpha", "2")]
    read_all(repo, seen)
    # The phone's clock is a day ahead.
    repo.save_feedback(feedback(seen[0].id, FeedbackEvent.OPEN, None, at=NOW + timedelta(days=1)))
    s = services(repo)
    cfg = config(tmp_path, "profile: {min_events: 2, rebuild_min_hours: 0.5}\n")

    assert rank(s, cfg)["profile_rebuilt"] == 1
    later = NOW + timedelta(hours=1)
    repo.save_feedback(
        feedback(seen[1].id, FeedbackEvent.OPEN, None, at=later - timedelta(minutes=30))
    )
    assert rank(s, cfg, now=later)["profile_rebuilt"] == 1


class FeedbackDownRepository(InMemoryRepository):
    """A store whose feedback can't be read."""

    def list_feedback(self, since: datetime | None = None) -> list[Feedback]:
        raise OSError("feedback store unavailable")


def test_unreadable_feedback_skips_similarity_rather_than_lifting_read_articles(
    tmp_path: Path,
) -> None:
    repo = FeedbackDownRepository()
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=1), feedback_ago=timedelta(hours=2))
    )
    for a in (article("alpha", "1"), article("beta", "1")):
        repo.upsert_article(a)

    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert counts["scored"] == 2
    assert counts["errors"] == 1
    assert counts["with_similarity"] == 0
    assert all("similarity" not in a.score_breakdown for a in repo.list_articles())


def test_an_article_in_the_same_story_as_one_the_owner_read_gets_no_similarity_lift(
    tmp_path: Path,
) -> None:
    repo = InMemoryRepository()
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=1), feedback_ago=timedelta(hours=2))
    )
    read = article("alpha", "read", source_id="gazette")
    mate = article("alpha", "mate", source_id="herald")  # the same story, another source
    # The same vector, but fetched too long before to be part of the story (default gap 48 h).
    other = article("alpha", "other", age=timedelta(hours=60), source_id="courier")
    for a in (read, mate, other, article("beta", "1"), article("beta", "2")):
        repo.upsert_article(a)
    repo.save_feedback(feedback(read.id, ago=timedelta(minutes=30)))

    rank(services(repo), config(tmp_path, SMALL_PROFILE))

    got = by_title(repo)
    assert got["Story alpha mate"].cluster_id == got["Story alpha read"].cluster_id
    assert got["Story alpha other"].cluster_id != got["Story alpha read"].cluster_id
    assert got["Story alpha mate"].score_breakdown["similarity"] == 1.0
    assert "already_read" in got["Story alpha mate"].score_breakdown
    assert got["Story alpha other"].score_breakdown["similarity"] > 1.0
    assert "already_read" not in got["Story alpha other"].score_breakdown
