"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.
"""

import pytest
from datetime import datetime, timezone, timedelta
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services import feed_service


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def friends(app):
    with app.app_context():
        main_user = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([main_user, friend])
        db.session.commit()

        db.session.execute(friendships.insert().values(user_id=main_user.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=main_user.id))
        db.session.commit()

        yield main_user, friend


def test_listened_at_is_unambiguously_utc(app, friends):
    """
    The 'listened_at' timestamp returned by get_friends_listening_now() must
    carry an explicit UTC marker (e.g. 'Z' or '+00:00'). A bare ISO string
    with no offset is ambiguous to any client parsing it, and most datetime
    parsers default to interpreting an unmarked string as local time rather
    than UTC.
    """
    with app.app_context():
        main_user, friend = friends
        main_user = db.session.get(User, main_user.id)
        friend = db.session.get(User, friend.id)

        song = Song(title="Test Song", artist="Test Artist", shared_by=friend.id)
        db.session.add(song)
        db.session.commit()

        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        db.session.add(ListeningEvent(user_id=friend.id, song_id=song.id, listened_at=recent_time))
        db.session.commit()

        result = feed_service.get_friends_listening_now(main_user.id)

        assert len(result) == 1
        listened_at_str = result[0]["listened_at"]
        assert listened_at_str.endswith("Z") or "+00:00" in listened_at_str or "+" in listened_at_str[10:], (
            f"listened_at {listened_at_str!r} has no timezone marker — "
            "a client parsing this will not know it's UTC"
        )
