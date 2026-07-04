"""
tests/test_notifications.py — Mixtape

Tests for notification creation on song rating.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications, add_to_playlist
from services.playlist_service import create_playlist


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def sharer_and_rater(app):
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(title="Test Song", artist="Test Artist", shared_by=sharer.id)
        db.session.add(song)
        db.session.commit()

        yield sharer, rater, song


def test_sharer_is_notified_when_song_is_rated(app, sharer_and_rater):
    """Rating a friend's song should notify the person who originally shared it."""
    with app.app_context():
        sharer, rater, song = sharer_and_rater

        rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1
        assert notifications[0]["type"] == "song_rated"
        assert rater.username in notifications[0]["body"]
        assert song.title in notifications[0]["body"]


def test_no_self_notification_when_rating_own_song(app, sharer_and_rater):
    """A user rating their own song should not generate a notification."""
    with app.app_context():
        sharer, rater, song = sharer_and_rater

        rate_song(sharer.id, song.id, 3)

        notifications = get_notifications(sharer.id)
        assert notifications == []


def test_no_duplicate_notification_when_resubmitting_same_score(app, sharer_and_rater):
    """
    Rating a song, then submitting the exact same score again, is a no-op —
    it should not generate a second notification.
    """
    with app.app_context():
        sharer, rater, song = sharer_and_rater

        rate_song(rater.id, song.id, 5)
        rate_song(rater.id, song.id, 5)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1


def test_notification_when_rating_is_changed(app, sharer_and_rater):
    """Changing an existing rating to a different score should notify again."""
    with app.app_context():
        sharer, rater, song = sharer_and_rater

        rate_song(rater.id, song.id, 5)
        rate_song(rater.id, song.id, 2)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 2


def test_no_duplicate_notification_when_song_already_in_playlist(app, sharer_and_rater):
    """
    Adding a song to a playlist, then calling add_to_playlist again for the
    same song, is a no-op — it should not generate a second notification.
    """
    with app.app_context():
        sharer, rater, song = sharer_and_rater
        playlist = create_playlist("Test Playlist", rater.id)

        add_to_playlist(playlist.id, song.id, rater.id)
        add_to_playlist(playlist.id, song.id, rater.id)

        notifications = get_notifications(sharer.id)
        assert len(notifications) == 1
