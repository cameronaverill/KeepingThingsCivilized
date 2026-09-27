"""Login throttling with django-axes: lockout after LOGIN_MAX_FAILURES, by username and by IP, reset on success, cool-off."""
from datetime import timedelta

import pytest
from auth_testkit import (
    GENERIC_LOGIN_ERROR,
    LOCKED_PREFIX,
    LOCKOUT_STATUSES,
    PASSWORD,
    WRONG_PASSWORD,
    alice,
    clean_axes,
    cooloff,
    fail_logins,
    is_logged_in,
    lock_out_ip,
    lock_out_username,
    lockout_wait_minutes,
    login_post,
    make_user,
    normalized,
    text,
    time_travel,
)
from django.conf import settings
from django.test import Client

pytestmark = pytest.mark.django_db

LIMIT = settings.LOGIN_MAX_FAILURES
COOLOFF_MINUTES = settings.LOGIN_COOLOFF_MINUTES


def assert_locked(response):
    page = text(response)
    assert response.status_code in LOCKOUT_STATUSES
    assert LOCKED_PREFIX in page
    assert GENERIC_LOGIN_ERROR not in page
    return page


def assert_not_locked_but_refused(response):
    page = text(response)
    assert response.status_code == 200
    assert GENERIC_LOGIN_ERROR in page
    assert LOCKED_PREFIX not in page


# --- one username, one address -----------------------------------------------------------------------------------------


def test_failures_below_the_limit_only_give_the_generic_message(client, alice):
    for response in fail_logins(client, "alice", LIMIT - 1):
        assert_not_locked_but_refused(response)


def test_the_correct_password_still_works_one_failure_below_the_limit(client, alice):
    fail_logins(client, "alice", LIMIT - 1)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert response.status_code == 302 and is_logged_in(client)


def test_after_the_limit_even_the_correct_password_is_refused_with_the_wait(client, alice):
    fail_logins(client, "alice", LIMIT)
    response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    page = assert_locked(response)
    assert not is_logged_in(client)
    assert lockout_wait_minutes(page) in (COOLOFF_MINUTES - 1, COOLOFF_MINUTES)


def test_the_lockout_message_names_the_reason_and_the_wait_in_words(client, alice):
    fail_logins(client, "alice", LIMIT)
    page = assert_locked(login_post(client, "alice", WRONG_PASSWORD, ip="10.0.0.1"))
    assert f"{LOCKED_PREFIX} Try again in " in page
    assert "minute" in page


def test_a_wrong_password_while_locked_is_told_it_is_locked_not_that_it_is_wrong(client, alice):
    fail_logins(client, "alice", LIMIT)
    assert_locked(login_post(client, "alice", WRONG_PASSWORD, ip="10.0.0.1"))


def test_the_wait_follows_the_tunable_not_a_hard_coded_number(client, alice, settings):
    settings.LOGIN_COOLOFF_MINUTES = 7
    settings.AXES_COOLOFF_TIME = timedelta(minutes=7)
    fail_logins(client, "alice", LIMIT)
    page = assert_locked(login_post(client, "alice", PASSWORD, ip="10.0.0.1"))
    assert lockout_wait_minutes(page) in (6, 7)


def test_an_unknown_username_is_locked_out_with_the_same_message_as_a_real_one(client, alice):
    fail_logins(client, "alice", LIMIT)
    fail_logins(client, "ghost_user", LIMIT, ip="10.0.0.2")
    real = login_post(client, "alice", WRONG_PASSWORD, ip="10.0.0.1")
    fake = login_post(client, "ghost_user", WRONG_PASSWORD, ip="10.0.0.2")
    assert_locked(real)
    assert_locked(fake)
    assert real.status_code == fake.status_code
    assert normalized(real, "alice") == normalized(fake, "ghost_user")


def test_a_lockout_does_not_leak_whether_the_account_exists_before_any_failures(client, alice):
    assert_not_locked_but_refused(login_post(client, "alice", WRONG_PASSWORD, ip="10.5.5.5"))
    assert_not_locked_but_refused(login_post(client, "ghost_user", WRONG_PASSWORD, ip="10.5.5.6"))


def test_a_lockout_is_not_a_session_and_does_not_log_in(client, alice):
    fail_logins(client, "alice", LIMIT)
    login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert not is_logged_in(client)


# --- by username, by IP ---------------------------------------------------------------------------------------------------


def test_failures_from_many_addresses_lock_the_username_everywhere(client, alice):
    make_user("bob")
    lock_out_username(client, "alice")
    fresh_ip = "10.9.9.9"
    assert_locked(login_post(client, "alice", PASSWORD, ip=fresh_ip))
    assert not is_logged_in(client)
    # A different account from the same fresh address is untouched.
    other = Client()
    assert login_post(other, "bob", PASSWORD, ip=fresh_ip).status_code == 302
    assert is_logged_in(other)


def test_spelling_a_username_with_other_capitals_does_not_dodge_the_lock(client, alice):
    """Usernames are unique ignoring case (plan section 8), so the counter must not be dodged by typing ALICE."""
    spellings = ["alice", "ALICE", "Alice", "aLiCe", "alicE"][:LIMIT] + ["alice"] * max(0, LIMIT - 5)
    for i, name in enumerate(spellings):
        login_post(client, name, WRONG_PASSWORD, ip=f"10.3.0.{i + 1}")
    assert_locked(login_post(client, "alice", PASSWORD, ip="10.3.9.9"))
    assert not is_logged_in(client)


def test_spelling_an_unknown_username_with_other_capitals_locks_it_the_same_way(client, alice):
    """Otherwise a real name would lock across spellings and an unknown one would not: that difference reveals which exist."""
    spellings = ["ghost_user", "GHOST_USER", "Ghost_User", "gHoSt_UsEr", "ghost_USER"][:LIMIT]
    spellings += ["ghost_user"] * (LIMIT - len(spellings))
    for i, name in enumerate(spellings):
        login_post(client, name, WRONG_PASSWORD, ip=f"10.6.0.{i + 1}")
    assert_locked(login_post(client, "ghost_user", WRONG_PASSWORD, ip="10.6.9.9"))


def test_failures_from_one_address_lock_that_address_for_every_username(client, alice):
    make_user("bob")
    lock_out_ip(client, "10.7.7.7")
    assert_locked(login_post(client, "bob", PASSWORD, ip="10.7.7.7"))
    assert not is_logged_in(client)
    assert_locked(login_post(client, "alice", PASSWORD, ip="10.7.7.7"))
    # The same accounts from another address are fine.
    other = Client()
    assert login_post(other, "bob", PASSWORD, ip="10.7.7.8").status_code == 302
    assert is_logged_in(other)


def test_a_locked_address_is_locked_only_for_its_own_address(client, alice):
    lock_out_ip(client, "10.7.7.7")
    assert_not_locked_but_refused(login_post(client, "alice", WRONG_PASSWORD, ip="10.7.7.8"))


def test_spreading_failures_across_names_and_addresses_below_the_limit_locks_nobody(client, alice):
    for i in range(LIMIT - 1):
        login_post(client, "alice", WRONG_PASSWORD, ip=f"10.2.0.{i + 1}")
        login_post(client, f"someone_{i}", WRONG_PASSWORD, ip="10.2.1.1")
    response = login_post(client, "alice", PASSWORD, ip="10.2.9.9")
    assert response.status_code == 302 and is_logged_in(client)


def test_a_forged_forwarded_for_header_does_not_dodge_the_address_lock(client, alice):
    for i in range(LIMIT):
        client.post(
            "/accounts/login/",
            {"username": f"someone_{i}", "password": WRONG_PASSWORD},
            REMOTE_ADDR="10.7.7.7",
            HTTP_X_FORWARDED_FOR=f"8.8.8.{i + 1}",
        )
    response = client.post(
        "/accounts/login/",
        {"username": "alice", "password": PASSWORD},
        REMOTE_ADDR="10.7.7.7",
        HTTP_X_FORWARDED_FOR="8.8.8.200",
    )
    assert_locked(response)


# --- reset on success -----------------------------------------------------------------------------------------------------


def test_a_successful_login_resets_the_failure_counter(alice):
    first = Client()
    fail_logins(first, "alice", LIMIT - 1)
    assert login_post(first, "alice", PASSWORD, ip="10.0.0.1").status_code == 302
    second = Client()
    for response in fail_logins(second, "alice", LIMIT - 1):
        assert_not_locked_but_refused(response)
    third = Client()
    assert login_post(third, "alice", PASSWORD, ip="10.0.0.1").status_code == 302
    assert is_logged_in(third)


def test_without_a_success_in_between_the_same_failures_do_lock(alice):
    client = Client()
    fail_logins(client, "alice", LIMIT - 1)
    fail_logins(client, "alice", LIMIT - 1)
    assert_locked(login_post(client, "alice", PASSWORD, ip="10.0.0.1"))


# --- cool-off -------------------------------------------------------------------------------------------------------------


def test_the_lockout_ends_after_the_cool_off(client, alice):
    fail_logins(client, "alice", LIMIT)
    with time_travel(cooloff() + timedelta(minutes=1)):
        response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert response.status_code == 302
    assert is_logged_in(client)


def test_the_lockout_still_holds_just_before_the_cool_off_ends(client, alice):
    fail_logins(client, "alice", LIMIT)
    with time_travel(cooloff() - timedelta(minutes=1)):
        response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert_locked(response)
    assert not is_logged_in(client)


def test_attempts_made_during_the_lockout_do_not_extend_it(client, alice):
    """The wait the page states must stay true, so a try while locked must not restart the clock."""
    fail_logins(client, "alice", LIMIT)
    with time_travel(cooloff() - timedelta(minutes=1)):
        assert_locked(login_post(client, "alice", WRONG_PASSWORD, ip="10.0.0.1"))
    with time_travel(cooloff() + timedelta(minutes=1)):
        response = login_post(client, "alice", PASSWORD, ip="10.0.0.1")
    assert response.status_code == 302
    assert is_logged_in(client)


def test_the_address_lock_also_ends_after_the_cool_off(client, alice):
    lock_out_ip(client, "10.7.7.7")
    with time_travel(cooloff() + timedelta(minutes=1)):
        response = login_post(client, "alice", PASSWORD, ip="10.7.7.7")
    assert response.status_code == 302 and is_logged_in(client)


def test_after_the_cool_off_the_counter_starts_again_from_zero(client, alice):
    fail_logins(client, "alice", LIMIT)
    with time_travel(cooloff() + timedelta(minutes=1)):
        for response in fail_logins(client, "alice", LIMIT - 1):
            assert_not_locked_but_refused(response)
        assert login_post(client, "alice", PASSWORD, ip="10.0.0.1").status_code == 302
