"""Fixtures for the step 7b tests (tests/forum_views/). Nothing imports from this file; shared helpers live in
fviews_kit.py, fviews_html.py and fviews_js.py."""
import sys
from pathlib import Path
from unittest import mock

import pytest
from django.core.cache import cache
from django.db import transaction
from django.urls import reverse

sys.path.insert(0, str(Path(__file__).resolve().parent))  # make the helpers importable however pytest is invoked

import fviews_html as H  # noqa: E402
import fviews_kit as K  # noqa: E402


@pytest.fixture(autouse=True)
def _fviews_state(db, settings):
    """A clean cache before and after every test, and the worker mode (no pipeline call) unless a test asks."""
    cache.clear()
    settings.MODERATION_RUN_MODE = "worker"
    yield
    cache.clear()


@pytest.fixture
def clock():
    """Frozen server time; `clock.advance(seconds)` moves it. Both services and model timestamps read it."""
    fake = K.FClock()
    with mock.patch("django.utils.timezone.now", fake.now):
        yield fake


class _Rollback(Exception):
    pass


@pytest.fixture(scope="session")
def field_names(django_db_setup, django_db_blocker):
    """The form-field names the builder chose ("proposition" form and message composer), read from the rendered pages
    inside a transaction that is rolled back. The contract fixes the pages, not the field names."""
    found = {}
    with django_db_blocker.unblock():
        try:
            with transaction.atomic():
                duo = K.Duo("Probe proposition for field discovery.")
                page = H.doc(duo.ca.get(duo.url))
                form = H.form_with_action(page, duo.post_url)
                assert form is not None, "an active conversation page must hold a form posting to forum:post"
                control = H.text_control(form)
                assert control is not None and control.get("name"), "the composer needs a named text control"
                found["message"] = control.get("name")
                propose = H.doc(duo.ca.get(reverse("forum:propose")))
                pform = next((f for f in H.forms(propose) if H.text_control(f) is not None), None)
                assert pform is not None, "the propose page needs a form with a text control"
                found["proposition"] = H.text_control(pform).get("name")
                raise _Rollback
        except _Rollback:
            pass
    return found
