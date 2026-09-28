"""Step 9a privacy (plan section 10 item 8, brief 9a "Privacy"): without `include_identities` no username, email, user pk or
display name appears anywhere in the output; with it, each participant additionally has `username` and `email`.

The seeded users have recognisable names and explicit primary keys (48211 and 59377), so a scan of the serialized text
finds any leak, including a leak through a nested structure or a key.
"""
import hashlib
from types import SimpleNamespace

import exports_kit as kit
import pytest

pytestmark = pytest.mark.django_db

FORBIDDEN_KEYS = {
    "username", "email", "user", "user_id", "user_pk", "pk", "created_by", "first_name", "last_name", "display_name",
    "full_name", "message_id", "trigger_message_id", "posted_message_id", "in_reply_to_id", "participant_id",
    "issue_id",
}  # fmt: skip
FLAGS_WITHOUT_IDENTITIES = [
    pytest.param(dict(), id="default"),
    pytest.param(dict(include_raw=True), id="raw"),
    pytest.param(dict(include_identities=False, include_raw=True), id="explicit-false+raw"),
]


def hits(text):
    return [s for s in kit.IDENTITY_STRINGS if s in text]


class TestNoIdentitiesByDefault:
    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAGS_WITHOUT_IDENTITIES)
    def test_the_serialized_text_holds_no_seeded_name_email_or_pk(self, world, which, flags):
        assert hits(kit.export_text(getattr(world, which), **flags)) == []

    @pytest.mark.parametrize("flags", FLAGS_WITHOUT_IDENTITIES)
    def test_the_bundle_holds_no_such_string_in_any_key_or_value(self, world, flags):
        b = kit.bundle(world.h, **flags)
        leaves = [str(leaf) for _, leaf in kit.walk(b)]
        keys = [str(k) for k in kit.all_keys(b)]
        assert [s for s in kit.IDENTITY_STRINGS if any(s in x for x in leaves + keys)] == []

    @pytest.mark.parametrize("which", ["h", "s", "e"])
    @pytest.mark.parametrize("flags", FLAGS_WITHOUT_IDENTITIES)
    def test_no_identity_or_primary_key_field_exists_anywhere(self, world, which, flags):
        keys = set(kit.all_keys(kit.bundle(getattr(world, which), **flags)))
        assert keys & FORBIDDEN_KEYS == set()

    def test_the_topic_creator_is_not_exported(self, world):
        # The seeded topic was created by user A.
        assert world.h.topic.created_by_id == kit.USER_A["id"]
        topic = kit.bundle(world.h)["topic"]
        assert "created_by" not in topic
        assert kit.USER_A["username"] not in kit.export_text(world.h)

    def test_a_participant_has_only_label_order_time_and_pseudonym(self, world):
        parts = kit.bundle(world.h)["participants"]
        assert [set(p) for p in parts] == [{"label", "join_order", "joined_at", "pseudonym"}] * 2

    def test_messages_and_runs_refer_to_labels_and_seq_numbers_only(self, world):
        b = kit.bundle(world.h)
        assert {m["participant"] for m in b["messages"]} == {"A", "B", None}
        assert b["conversation"]["ended_by"] == "A"


class TestIdentitiesOnRequest:
    def test_each_participant_gains_username_and_email(self, world):
        parts = kit.bundle(world.h, include_identities=True)["participants"]
        assert [(p["label"], p["username"], p["email"]) for p in parts] == [
            ("A", kit.USER_A["username"], kit.USER_A["email"]),
            ("B", kit.USER_B["username"], kit.USER_B["email"]),
        ]

    def test_the_scan_finds_the_seeded_identities_in_the_identity_export(self, world):
        # Non-vacuity of every scan above: the same scan reports the identities when they are requested.
        found = hits(kit.export_text(world.h, include_identities=True))
        assert {kit.USER_A["username"], kit.USER_A["email"], kit.USER_B["username"], kit.USER_B["email"]} <= set(found)

    def test_each_identity_appears_exactly_once_and_only_among_the_participants(self, world):
        text = kit.export_text(world.h, include_identities=True, include_raw=True)
        for value in (kit.USER_A["username"], kit.USER_A["email"], kit.USER_B["username"], kit.USER_B["email"]):
            assert text.count(value) == 1

    def test_the_pseudonym_is_still_there_with_identities(self, world):
        parts = kit.bundle(world.h, include_identities=True)["participants"]
        assert [bool(kit.PSEUDONYM_RE.match(p["pseudonym"])) for p in parts] == [True, True]

    def test_the_run_and_message_data_do_not_change_when_identities_are_added(self, world):
        plain, ident = kit.bundle(world.h), kit.bundle(world.h, include_identities=True)
        keys = ("conversation", "topic", "messages", "runs", "cost_usd")
        assert {k: plain[k] for k in keys} == {k: ident[k] for k in keys}

    def test_synthetic_participants_have_null_identities(self, world):
        parts = kit.bundle(world.s, include_identities=True)["participants"]
        assert [(p["pseudonym"], p["username"], p["email"]) for p in parts] == [(None, None, None)] * 2

    def test_a_synthetic_export_with_identities_names_nobody(self, world):
        text = kit.export_text(world.s, include_identities=True)
        assert hits(text) == []


class TestPseudonyms:
    def pseudonyms(self, conv, **flags):
        return [p["pseudonym"] for p in kit.bundle(conv, **flags)["participants"]]

    def test_format(self, world):
        ps = self.pseudonyms(world.h)
        assert [bool(kit.PSEUDONYM_RE.match(p)) for p in ps] == [True, True]

    def test_two_people_get_two_pseudonyms(self, world):
        a, b = self.pseudonyms(world.h)
        assert a != b

    def test_stable_across_calls_and_flags(self, world):
        first = self.pseudonyms(world.h)
        assert self.pseudonyms(world.h) == first
        assert self.pseudonyms(world.h, include_identities=True) == first
        assert self.pseudonyms(world.h, include_raw=True) == first

    def test_the_same_person_has_the_same_pseudonym_in_every_conversation(self, world):
        in_h = self.pseudonyms(world.h)[0]
        in_e = self.pseudonyms(world.e)[0]
        assert in_h == in_e

    def test_a_new_person_gets_a_new_pseudonym_and_the_old_ones_do_not_change(self, world):
        before = self.pseudonyms(world.h)
        other = kit.light()
        newcomer = self.pseudonyms(other.conv)
        assert set(newcomer).isdisjoint(before)
        assert self.pseudonyms(world.h) == before

    def test_it_is_not_the_pk_and_does_not_contain_it(self, world):
        a, b = self.pseudonyms(world.h)
        assert str(kit.USER_A["id"]) not in a
        assert str(kit.USER_B["id"]) not in b
        assert a != f"p-{kit.USER_A['id']}"

    @pytest.mark.parametrize("algorithm", ["md5", "sha1", "sha256"])
    def test_it_is_not_an_unkeyed_hash_of_the_pk(self, world, algorithm):
        a = self.pseudonyms(world.h)[0]
        pk = kit.USER_A["id"]
        candidates = [str(pk), f"user-{pk}", f"user:{pk}", f"pseudonym:{pk}", f"export-pseudonym:{pk}", f"p-{pk}"]
        digests = ["p-" + hashlib.new(algorithm, c.encode()).hexdigest()[:8] for c in candidates]
        assert a not in digests

    def test_it_is_keyed_with_the_project_secret(self, world, settings):
        before = self.pseudonyms(world.h)
        settings.SECRET_KEY = kit.altered(settings.SECRET_KEY)
        after = self.pseudonyms(world.h)
        assert after != before
        assert [bool(kit.PSEUDONYM_RE.match(p)) for p in after] == [True, True]

    def test_the_original_key_gives_the_original_pseudonyms_back(self, world, settings):
        original = settings.SECRET_KEY
        before = self.pseudonyms(world.h)
        settings.SECRET_KEY = kit.altered(original)
        self.pseudonyms(world.h)
        settings.SECRET_KEY = original
        assert self.pseudonyms(world.h) == before

    def test_a_synthetic_participant_has_no_pseudonym(self, world):
        assert self.pseudonyms(world.s) == [None, None]


class TestOmissionIsFieldLevelNotContentScanning:
    """A message may legitimately contain a string identical to another participant's real username or email (a
    quote, a mention). The identity rule removes the `username`/`email` keys from a participant's own record; it is
    not a scan of message text, so a message that happens to contain such a string is exported unchanged."""

    @pytest.fixture
    def collision(self, db):
        conv = kit.make_conversation()
        user_a = kit.make_user()
        user_b = kit.make_user()
        kit.make_participant(conv, "A", 1, user=user_a)
        speaker = kit.make_participant(conv, "B", 2, user=user_b)
        text = f"Someone using the handle {user_a.username} said this exact thing, reachable at {user_a.email}."
        kit.make_message(conv, "user", speaker, text)
        return SimpleNamespace(conv=conv, user_a=user_a, text=text)

    def test_the_colliding_message_content_is_exported_byte_for_byte(self, collision):
        bundle = kit.bundle(collision.conv)

        assert bundle["messages"][0]["content"] == collision.text

    def test_the_named_users_identity_fields_are_still_omitted(self, collision):
        bundle = kit.bundle(collision.conv)

        participant_a = bundle["participants"][0]

        assert "username" not in participant_a
        assert "email" not in participant_a
