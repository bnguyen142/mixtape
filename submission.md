# Mixtape Bug Hunt — Submission

## AI Usage

<!--
TODO: Fill this in as you go (or at the end, while it's fresh). Be specific:
- What did you ask AI tools to explain, trace, or summarize?
- What did they help you understand?
- Where did you have to verify something yourself, or where was the AI's
  explanation incomplete / wrong / pointing you the wrong way?

Example of the level of detail expected:
"I gave Claude the contents of streak_service.py and asked it to explain what
update_listening_streak() does step by step. It correctly described the
day-difference logic but I had to verify the weekday() boundary behavior
myself by testing it in a Python shell, since the AI's first explanation of
the ISO weekday numbering was wrong."
-->

Read through the flow of code, asking AI as coach to confirm thinking is correct on certain things and asking questions like search and q functions. also ask to clarify when terms like "feed" was used to in feed_bp vs feed inside a function. after going thruogh and mapping the services to route, I ask AI to confirm my connections

While investigating Issue #4 (notification on rating), I initially thought the expected behavior was that the playlist's creator should be notified when someone rates a song in that playlist. I asked the AI to check that interpretation against the code. It pointed out that the `Rating` model (`id, user_id, song_id, score, rated_at`) has no `playlist_id` field at all, and that `rate_song()` never receives a `playlist_id` — so there's no way to even determine "which playlist" a rating belongs to, since a song can be in multiple playlists at once. That confirmed my interpretation was wrong: the correct parallel is "notify the song's original sharer," the same pattern `add_to_playlist()` already uses, not "notify the playlist creator." I had to revise my understanding based on that check rather than going in with the playlist-creator assumption.

---

## Codebase Map

### Main files and their roles

- **app.py** — Flask application factory (`create_app`). Initializes the `db`
  (Flask-SQLAlchemy) object, applies config (`SQLALCHEMY_DATABASE_URI`,
  `SECRET_KEY`), registers the four blueprints (`songs`, `playlists`, `users`,
  `feed`), and calls `db.create_all()`. Must be started with
  `FLASK_APP=app:create_app flask run`, not `python app.py`.
- **models.py** — SQLAlchemy models: `User`, `Tag`, `Song`, `ListeningEvent`,
  `Rating`, `Playlist`, `Notification`. Three association tables:
  - `friendships` — symmetric many-to-many between users.
  - `song_tags` — many-to-many between songs and tags.
  - `playlist_entries` — many-to-many between playlists and songs, but not a
    plain join table: it also carries `position` (explicit ordering, not
    insertion order), `added_by`, and `added_at`.
  Every model has a `to_dict()` used directly as the JSON response shape.
- **routes/songs.py** — `GET /songs/search` (search_service.search_songs),
  `GET /songs/<id>` (search_service.get_song), `POST /songs/<id>/rate`
  (notification_service.rate_song), `POST /songs/<id>/listen`
  (streak_service.record_listening_event). Rating and "listening" are two
  distinct actions with two distinct services — rating does not touch the
  streak, and listening does not touch ratings.
- **routes/playlists.py** — `POST /playlists/` (playlist_service.create_playlist),
  `GET /playlists/<id>` (playlist_service.get_playlist, metadata only),
  `GET /playlists/<id>/songs` (playlist_service.get_playlist_songs),
  `POST /playlists/<id>/songs` (notification_service.add_to_playlist — this
  one lives in notification_service, not playlist_service, because adding a
  song also has to trigger a notification to the original sharer).
- **routes/users.py** — `GET /users/<id>` (reads the User directly, no
  service), `GET /users/<id>/streak` (streak_service.get_streak),
  `GET /users/<id>/notifications` (notification_service.get_notifications),
  `POST /users/notifications/<id>/read` (notification_service.mark_as_read).
- **routes/feed.py** — `GET /feed/<user_id>/listening-now`
  (feed_service.get_friends_listening_now) and `GET /feed/<user_id>/activity`
  (feed_service.get_activity_feed).
- **services/streak_service.py** — `record_listening_event()` creates a
  `ListeningEvent` and calls `update_listening_streak()`, which compares
  `today` vs. the user's `last_listened_at` date to decide whether to leave
  the streak alone (same day), increment it (exactly one day since last
  listen), or reset it to 1 (more than one day, or no prior listen).
  `get_streak()` just reads `User.listening_streak`.
- **services/feed_service.py** — `get_friends_listening_now()` pulls the
  current user's friend IDs, queries `ListeningEvent` rows for those friends
  within a 24-hour `RECENT_THRESHOLD`, and dedupes to one (most recent) event
  per friend. `get_activity_feed()` is the non-time-filtered version — most
  recent N events from friends regardless of age.
- **services/search_service.py** — `search_songs()` does a case-insensitive
  `ilike` match against `title` or `artist`, outer-joined to `song_tags` so
  tags can be included. `get_song()` fetches a single song by ID.
- **services/notification_service.py** — `create_notification()` is the
  shared primitive that inserts a `Notification` row. `add_to_playlist()` and
  `rate_song()` are two independent call sites that each decide, on their
  own, whether/how to invoke `create_notification()`. `get_notifications()`
  and `mark_as_read()` handle reading/updating notification state.
- **services/playlist_service.py** — `create_playlist()`,
  `get_playlist()` (metadata only), `get_playlist_songs()` (songs ordered by
  `playlist_entries.position`), `get_user_playlists()`.
- **seed_data.py** — populates the DB with users, friendships, songs, tags,
  listening events, ratings, and playlists for local testing.
- **tests/** — `test_streaks.py`, `test_search.py`, `test_playlists.py`,
  `test_feed.py` (added while investigating Issue #2 — no test previously
  covered `feed_service.py`) (pytest, run with `pytest tests/`).

### Data flow — a user rates a song

1. Client sends `POST /songs/<song_id>/rate` with `{user_id, score}`.
2. `routes/songs.py::rate()` parses/validates the body (`user_id` and `score`
   both required) and calls `notification_service.rate_song(user_id, song_id,
   int(score))`.
3. `rate_song()` validates `score` is 1–5, loads the `Song` and `User`, then
   checks for an existing `Rating` for this `(user_id, song_id)` pair — if
   one exists it updates the score in place, otherwise it inserts a new
   `Rating` row (there's a `UniqueConstraint("user_id", "song_id")` on the
   table backing this).
4. The route returns `rating.to_dict()` with a 201.
5. Note: unlike `add_to_playlist()`, `rate_song()` does not call
   `create_notification()` anywhere in its body — no `Notification` row is
   produced as a side effect of rating a song, only of adding a song to a
   playlist.

### Patterns noticed

- Every route function is thin: parse request → call one service function →
  jsonify the result or catch `ValueError` and turn it into a 4xx. All
  business logic lives in `services/`.
- Models expose their own `to_dict()`, and routes/services pass that dict
  straight through as the JSON response — there's no separate serializer
  layer.
- Cross-service imports are done lazily, inside function bodies (e.g.
  `notification_service.add_to_playlist()` imports `Playlist` and
  `playlist_service.get_playlist_songs` inside the function rather than at
  module level) — looks like this is to avoid circular imports between
  `services/notification_service.py` and `services/playlist_service.py`.
- Association tables aren't always plain join tables — `playlist_entries`
  carries extra columns (`position`, `added_by`, `added_at`) beyond the two
  foreign keys.

### Confirmed limitations (no route/service exists for these)

- **Friendships are read-only via the API.** No route or service function
  creates or modifies a `friendships` row. The only place it's populated is
  `seed_data.py`, via a raw insert directly against the association table
  (`friendships.insert().values(...)`). There is no "add friend" endpoint.
- **Song tags are read-only via the API.** Same pattern — `song_tags` is only
  ever written to in `seed_data.py` (`song_tags.insert().values(...)`).
  `search_service.py` reads through it (outer join), but nothing in the app
  creates, adds, or removes a tag from a song at runtime.
- **There is no route to create a new song.** `Song(...)` is only
  instantiated in `seed_data.py` and directly inside test setup code
  (`tests/test_search.py`, `tests/test_playlists.py`). Despite the app's
  premise being "friends share songs," no `POST /songs` (or equivalent)
  endpoint exists anywhere in `routes/`.

---

## Root Cause Analysis

<!--
Fill in one entry per bug you fix (at least 3). Do this right after you fix
each bug, not at the end. Six fields per entry, matching the grading rubric
exactly (it grades "fix description" and "side-effect check" separately —
don't merge them into one bullet):

1. Reproduction steps    — inputs / actions / data state that trigger it.
2. Navigation strategy   — which files you opened, what you followed (a call,
   a query, a data flow), and the specific moment you became confident this
   was the root cause and not just a suspicious area. Show the path, not just
   the destination.
3. Root cause            — name the specific function/variable/condition and
   explain the mechanism: why THIS causes the reported behavior under THESE
   conditions. "The code was wrong" or a restatement of the bug report earns
   no credit here.
4. Fix description       — what you changed and why that specific change
   addresses the mechanism described above (not just "I fixed it").
5. Side-effect check     — a specific, deliberate check of a specific other
   behavior/code path that touches the same data, plus why that check was
   sufficient. "The app still ran" does not satisfy this on its own.
-->
### Issue #1: My listening streak keeps resetting

- **Reproduction steps:** Ran `python -m pytest tests/test_streaks.py -v`. 4 of
  5 tests pass; `test_streak_increments_on_sunday` fails with
  `assert 1 == 2`. That test listens on Saturday (2024-06-15) then Sunday
  (2024-06-16) — one calendar day apart — and expects the streak to go from 1
  to 2, but it stays at 1.
- **Navigation strategy:** Started from the existing test file
  `tests/test_streaks.py` (already had a case for this) and ran the whole
  file to see which cases pass/fail. Compared the failing test's setup
  against `test_streak_increments_on_consecutive_day`, which tests the same
  "one day apart" scenario (Monday → Tuesday) and passes. Since both tests
  exercise the same `days_since_last == 1` scenario but only one fails, the
  difference had to be day-of-week specific, not day-count specific. Opened
  `services/streak_service.py::update_listening_streak()` and read it
  condition by condition, looking for anything referencing a specific
  weekday rather than a day count — found it on the `elif` that guards the
  increment.
- **Root cause:** In `update_listening_streak()`, the increment branch is
  `elif days_since_last == 1 and today.weekday() != 6:`. It combines two
  conditions with `and`: "exactly one day since last listen" AND "today is
  not a Sunday" (`weekday() == 6` is Sunday). On a Saturday→Sunday listen,
  `days_since_last` is `1`, but `today.weekday()` is `6`, so the second half
  of the condition is `False`, the whole `elif` is `False`, and execution
  falls through to the `else` branch, resetting the streak to `1` instead of
  incrementing it. My working theory on intent: this looks like an attempt
  at some kind of "weekly streak, reset on Sunday" feature, but it was folded
  directly into the general consecutive-day increment condition instead of
  being its own explicit branch — so it fires on every Sunday regardless,
  breaking the basic one-day-apart case whenever that day happens to be a
  Sunday.
- **Fix description:** Removed the `and today.weekday() != 6` clause from the
  increment branch, so it now reads `elif days_since_last == 1:`. This makes
  the increment depend only on the actual day-count gap (`days_since_last`),
  which is the thing the streak is supposed to track, and removes the
  incidental dependency on which weekday the listen happens to fall on.
- **Side-effect check:** Re-ran the full `tests/test_streaks.py` suite (not
  just the Sunday test) with
  `python -m pytest tests/test_streaks.py -v`. All 5 tests pass, including
  `test_streak_does_not_double_count_same_day` (the `days_since_last == 0`
  branch, untouched by this change) and `test_streak_resets_after_skipped_day`
  (the `else` branch, also untouched) — confirming the fix didn't
  inadvertently affect the same-day or skipped-day behavior that shares the
  same function and the same `days_since_last` variable.

### Issue #2: Friends Listening Now shows people from yesterday

- **Reproduction steps:** No existing test covered this, so I wrote
  `tests/test_feed.py` from scratch. I first tried to reproduce this as a
  filtering bug — single friend with two events, a friend at the exact 23h/
  25h boundary, two separate friends (one recent, one stale), the full
  `seed_data.py` dataset (5 users, events spanning 2–58 hours old), the same
  scenario through a live `curl` call against the running Flask server, and
  finally an event placed at the *exact* 24-hour cutoff instant. All six
  produced correct filtering — nothing older than 24 hours ever leaked into
  the result. That ruled out the threshold comparison itself. The actual
  reproducible test ended up being about the *shape* of the returned data,
  not filtering: `test_listened_at_is_unambiguously_utc` creates one friend
  with a recent `ListeningEvent`, calls `get_friends_listening_now()`, and
  asserts the returned `listened_at` string contains a UTC marker (`Z` or
  `+00:00`). It fails with the string `'2026-07-03T22:53:41.359842'` — no
  marker at all.
- **Navigation strategy:** Spent significant time testing
  `feed_service.py`'s `RECENT_THRESHOLD`/`cutoff` filter under many time
  scenarios (see above) before concluding the filter itself was correct. I
  also inspected the compiled SQL (`query.statement.compile(compile_kwargs=
  {"literal_binds": True})`) and confirmed the DB stores `listened_at` as
  plain naive text with no timezone suffix, while `cutoff`'s literal
  includes `+00:00` — a real discrepancy, but I proved via a direct
  exact-boundary test that it only causes borderline-recent events to be
  *excluded*, never causes stale events to be *included*, so it doesn't
  match the reported symptom. I also ruled out a "friend's timezone" theory
  by confirming the `User` model has no timezone/location field anywhere —
  every timestamp in the app is server-generated UTC, so a friend's real
  timezone can't be a factor. That redirected me to the last place a
  timestamp is touched: line 59's `event.listened_at.isoformat()` inside the
  dedup loop. Since I'd already proven (during Issue #1) that SQLite/
  SQLAlchemy round-trips a stored UTC datetime as *naive* (no `tzinfo`),
  I recognized `.isoformat()` on that naive value would produce a marker-
  less string — confirmed by checking the actual curl JSON response I'd
  gotten earlier, which showed `listened_at` values with no `Z` or offset.
- **Root cause:** `event.listened_at`, once loaded back from SQLite via
  SQLAlchemy, is a naive `datetime` (no `tzinfo`), even though it was
  written as UTC-aware. Calling `.isoformat()` on a naive datetime produces
  a string with no timezone indicator at all (e.g.
  `"2026-07-03T22:53:41.359842"`). The server's own filtering logic is
  correct because it operates on Python `datetime` objects internally, but
  the JSON response handed to any client is ambiguous — most datetime
  parsers default to interpreting a marker-less ISO string as local time,
  not UTC. A client several hours off from UTC would compute the wrong
  elapsed time (or even the wrong calendar day) for a friend's listening
  event, which is what produces the "shows people from yesterday" symptom —
  it's a display/interpretation bug on the consumer side caused by an
  ambiguous server response, not an incorrect filter.
- **Fix description:** In the dedup loop of `get_friends_listening_now()`,
  before calling `.isoformat()`, I added the same defensive check I'd
  already found in `streak_service.py::update_listening_streak()`: if
  `event.listened_at.tzinfo is None`, reattach it via
  `.replace(tzinfo=timezone.utc)` before formatting. This guarantees the
  serialized string always carries an explicit `+00:00` offset, so any
  client parsing it knows unambiguously it represents UTC.
- **Side-effect check:** Ran the full test suite
  (`python -m pytest -v`, 14 tests total). `test_feed.py`'s new test now
  passes, and all of `test_streaks.py` (5/5) and `test_search.py` (5/5)
  still pass — this change only touches the result-serialization step in
  `feed_service.py`, so I specifically checked that nothing else consumes
  `get_friends_listening_now()`'s output in a way that expects the old
  (unmarked) string format; `routes/feed.py` just passes the dict straight
  through to `jsonify()`, so there's no intermediate parsing of that string
  anywhere in the app to break. The 2 remaining failures are in
  `test_playlists.py` and are pre-existing/unrelated (Issue #5 — I never
  touched `playlist_service.py`).

### Issue #3: The same song keeps showing up twice in search

- **Reproduction steps:** TODO
- **Navigation strategy:** TODO
- **Root cause:** TODO
- **Fix description:** TODO
- **Side-effect check:** TODO

### Issue #4: I got notified when a friend added my song to a playlist but not when they rated it

- **Reproduction steps:** TODO
- **Navigation strategy:** TODO
- **Root cause:** TODO
- **Fix description:** TODO
- **Side-effect check:** TODO

### Issue #5: The last song in a playlist never shows up

- **Reproduction steps:** TODO
- **Navigation strategy:** TODO
- **Root cause:** TODO
- **Fix description:** TODO
- **Side-effect check:** TODO

---

## Regression Test (stretch)

<!-- If you write one, name the test file/function and which bug it covers. -->

TODO (optional)

---

## git log --oneline screenshot

<!-- Paste/attach the screenshot here, or note where it's included in your submission. -->

TODO
