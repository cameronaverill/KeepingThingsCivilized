"""seeding.arms.write_transcripts: files, no overwrite, determinism, and every shipped ready fact builds (88 transcripts)."""
import json
from pathlib import Path

import gen_kit as kit
import pytest


def write(items, directory, **kwargs):
    from seeding import arms

    return arms.write_transcripts(items, directory, **kwargs)


class TestWriting:
    def test_one_file_per_transcript_named_by_id(self, tmp_path):
        items = kit.transcripts()
        paths = write(items, tmp_path / "out")
        assert ([p.name for p in paths], kit.files_under(tmp_path / "out")) == (
            [f"{t['id']}.json" for t in items], sorted(f"{t['id']}.json" for t in items),
        )

    def test_the_returned_paths_are_paths_inside_the_directory(self, tmp_path):
        paths = write(kit.transcripts()[:2], tmp_path / "out")
        assert [(isinstance(p, Path), p.parent == tmp_path / "out", p.is_file()) for p in paths] == [(True, True, True)] * 2

    def test_a_missing_directory_is_created_with_its_parents(self, tmp_path):
        write(kit.transcripts()[:1], tmp_path / "a" / "b" / "c")
        assert (tmp_path / "a" / "b" / "c").is_dir()

    def test_a_file_reads_back_as_the_same_transcript(self, tmp_path):
        item = kit.transcripts()[3]
        (path,) = write([item], tmp_path)
        assert json.loads(path.read_text(encoding="utf-8")) == item

    def test_a_file_is_indented_by_two_and_ends_with_one_newline(self, tmp_path):
        item = kit.transcripts()[0]
        (path,) = write([item], tmp_path)
        text = path.read_text(encoding="utf-8")
        assert (text.startswith('{\n  "'), text.endswith("}\n"), text.endswith("\n\n")) == (True, True, False)

    def test_utf8_text_survives(self, tmp_path):
        left = kit.with_message(kit.base("left"), 0, text="Café — naïve déjà vu 中文 " + "thing " * 24 + "end.")
        item = kit.transcripts(kit.range_fact(), left, kit.base("right"))[0]
        (path,) = write([item], tmp_path)
        assert json.loads(path.read_bytes().decode("utf-8"))["messages"][0]["text"] == left["messages"][0]["text"]

    def test_the_written_files_pass_the_replay_validator(self, tmp_path):
        from moderation.transcripts import validate_transcript

        paths = write(kit.transcripts(), tmp_path)
        assert [validate_transcript(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths] == [None] * 8

    def test_writing_twice_to_two_directories_gives_identical_bytes(self, tmp_path):
        first = write(kit.transcripts(), tmp_path / "one")
        second = write(kit.transcripts(), tmp_path / "two")
        assert [p.read_bytes() for p in first] == [p.read_bytes() for p in second]

    def test_nothing_is_written_for_an_empty_list(self, tmp_path):
        assert (write([], tmp_path / "out"), kit.files_under(tmp_path / "out")) == ([], [])


class TestNoOverwrite:
    def test_an_existing_file_is_refused_and_left_alone(self, tmp_path):
        item = kit.transcripts()[0]
        target = tmp_path / f"{item['id']}.json"
        target.write_text("precious", encoding="utf-8")
        with pytest.raises((FileExistsError, ValueError)):
            write([item], tmp_path)
        assert target.read_text(encoding="utf-8") == "precious"

    def test_overwrite_true_replaces_the_file(self, tmp_path):
        item = kit.transcripts()[0]
        target = tmp_path / f"{item['id']}.json"
        target.write_text("old", encoding="utf-8")
        write([item], tmp_path, overwrite=True)
        assert json.loads(target.read_text(encoding="utf-8")) == item

    def test_overwrite_defaults_to_false(self):
        import inspect

        from seeding import arms

        assert inspect.signature(arms.write_transcripts).parameters["overwrite"].default is False


class TestShippedFacts:
    def facts(self):
        from seeding.facts import load_facts

        return [f for f in load_facts() if f.ready()]

    def build_all(self):
        out = []
        for fact in self.facts():
            out.extend(kit.transcripts(fact, *kit.pair(fact.id)))
        return out

    def test_twenty_facts_are_ready(self):
        assert len(self.facts()) == 20

    def test_they_build_120_transcripts(self):
        assert len(self.build_all()) == 120

    def test_the_ids_are_unique_slugs(self):
        import re

        ids = [t["id"] for t in self.build_all()]
        assert (len(set(ids)), all(re.fullmatch(r"[a-z0-9_]+", i) for i in ids)) == (120, True)

    def test_every_transcript_passes_the_replay_validator(self):
        assert [kit.validate(t) for t in self.build_all()] == [None] * 120

    def test_ten_statistics_give_eight_and_ten_non_statistics_give_four(self):
        counts = {}
        for fact in self.facts():
            counts[fact.type] = counts.get(fact.type, 0) + len(kit.transcripts(fact, *kit.pair(fact.id)))
        assert counts == {"statistic": 80, "law": 36, "qualitative": 4}

    def test_every_error_arm_plants_exactly_one_item_and_every_true_arm_none(self):
        planted = {}
        for t in self.build_all():
            planted.setdefault(t["seed"]["arm"] == "true", set()).add(sum(len(m["planted"]) for m in t["messages"]))
        assert planted == {True: {0}, False: {1}}

    def test_every_error_arm_phrase_differs_from_the_true_claim(self):
        bad = [t["id"] for t in self.build_all() if t["seed"]["arm"] != "true" and t["messages"][-1]["planted"][0]["phrase"] == t["messages"][-1]["planted"][0]["correction"].rstrip(".")]
        assert bad == []


class TestDefaultDirectoryAndAtomicity:
    """conftest runs every test in its own empty working directory, so the default `generated/transcripts` lands there."""

    def test_the_default_directory_is_generated_transcripts_and_is_created(self, tmp_path):
        items = kit.transcripts()[:2]
        paths = write(items, None)
        assert (kit.files_under(tmp_path / "generated" / "transcripts"), [p.parent.name for p in paths]) == (
            sorted(f"{t['id']}.json" for t in items), ["transcripts", "transcripts"],
        )

    def test_the_directory_argument_may_be_left_out(self, tmp_path):
        from seeding import arms

        arms.write_transcripts(kit.transcripts()[:1])
        assert kit.files_under(tmp_path / "generated" / "transcripts") == [f"{kit.transcripts()[0]['id']}.json"]

    def test_one_existing_file_stops_the_whole_write(self, tmp_path):
        items = kit.transcripts()
        (tmp_path / f"{items[2]['id']}.json").write_text("old", encoding="utf-8")
        with pytest.raises((FileExistsError, ValueError)):
            write(items, tmp_path)
        assert kit.files_under(tmp_path) == [f"{items[2]['id']}.json"]


def classify(items, directory):
    from seeding import arms

    return arms.classify_transcripts(items, directory)


def stat_ns(path):
    return path.stat().st_mtime_ns


class TestIdenticalFilesAreSkipped:
    """Ruling after the 12-fact run 2: a byte-identical existing target is left alone; only a differing one is a conflict."""

    def test_writing_the_same_transcripts_twice_is_fine_and_returns_every_path(self, tmp_path):
        items = kit.transcripts()
        first = write(items, tmp_path)
        second = write(items, tmp_path)
        assert (second, [p.name for p in second]) == (first, [f"{t['id']}.json" for t in items])

    def test_an_identical_file_is_not_rewritten(self, tmp_path):
        import os

        items = kit.transcripts()
        paths = write(items, tmp_path)
        os.utime(paths[0], ns=(1_000_000_000, 1_000_000_000))
        write(items, tmp_path)
        assert stat_ns(paths[0]) == 1_000_000_000

    def test_the_bytes_stay_the_same(self, tmp_path):
        items = kit.transcripts()
        before = {p.name: p.read_bytes() for p in write(items, tmp_path)}
        write(items, tmp_path)
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before

    def test_missing_files_are_written_next_to_identical_ones(self, tmp_path):
        items = kit.transcripts()
        write(items[:3], tmp_path)
        paths = write(items, tmp_path)
        assert (len(paths), kit.files_under(tmp_path)) == (8, sorted(f"{t['id']}.json" for t in items))

    def test_an_identical_file_is_skipped_even_with_overwrite(self, tmp_path):
        import os

        items = kit.transcripts()
        paths = write(items, tmp_path)
        os.utime(paths[1], ns=(2_000_000_000, 2_000_000_000))
        write(items, tmp_path, overwrite=True)
        assert stat_ns(paths[1]) == 2_000_000_000


class TestADifferingFileIsAConflict:
    def test_it_is_refused_without_overwrite(self, tmp_path):
        items = kit.transcripts()
        write(items[:1], tmp_path)
        changed = dict(items[0], description="changed")
        with pytest.raises((FileExistsError, ValueError)):
            write([changed], tmp_path)

    def test_nothing_at_all_is_written_when_one_target_differs(self, tmp_path):
        items = kit.transcripts()
        write(items[:1], tmp_path)
        changed = [dict(items[0], description="changed")] + items[1:]
        with pytest.raises((FileExistsError, ValueError)):
            write(changed, tmp_path)
        assert kit.files_under(tmp_path) == [f"{items[0]['id']}.json"]

    def test_the_differing_file_keeps_its_old_content(self, tmp_path):
        items = kit.transcripts()
        (path,) = write(items[:1], tmp_path)
        before = path.read_bytes()
        with pytest.raises((FileExistsError, ValueError)):
            write([dict(items[0], description="changed")], tmp_path)
        assert path.read_bytes() == before

    def test_identical_files_next_to_a_conflict_are_left_alone_and_new_ones_not_written(self, tmp_path):
        items = kit.transcripts()
        write(items[:2], tmp_path)
        mixed = [items[0], dict(items[1], description="changed"), items[2], items[3]]
        with pytest.raises((FileExistsError, ValueError)):
            write(mixed, tmp_path)
        assert kit.files_under(tmp_path) == sorted(f"{t['id']}.json" for t in items[:2])

    def test_a_file_missing_only_its_final_newline_is_a_conflict(self, tmp_path):
        items = kit.transcripts()
        (path,) = write(items[:1], tmp_path)
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
        with pytest.raises((FileExistsError, ValueError)):
            write(items[:1], tmp_path)

    def test_a_file_with_other_line_endings_is_a_conflict(self, tmp_path):
        items = kit.transcripts()
        (path,) = write(items[:1], tmp_path)
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        with pytest.raises((FileExistsError, ValueError)):
            write(items[:1], tmp_path)

    def test_overwrite_replaces_only_the_differing_file_and_writes_the_new_ones(self, tmp_path):
        import os

        items = kit.transcripts()
        first = write(items[:2], tmp_path)
        os.utime(first[0], ns=(3_000_000_000, 3_000_000_000))
        mixed = [items[0], dict(items[1], description="changed"), items[2]]
        paths = write(mixed, tmp_path, overwrite=True)
        assert (
            stat_ns(first[0]), json.loads(paths[1].read_text(encoding="utf-8"))["description"], kit.files_under(tmp_path),
        ) == (3_000_000_000, "changed", sorted(f"{t['id']}.json" for t in items[:3]))


class TestClassify:
    def test_everything_is_new_in_an_empty_directory(self, tmp_path):
        items = kit.transcripts()
        new, same, conflicts = classify(items, tmp_path / "missing")
        assert ([p.name for p in new], same, conflicts) == ([f"{t['id']}.json" for t in items], [], [])

    def test_it_writes_nothing(self, tmp_path):
        classify(kit.transcripts(), tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_written_files_are_unchanged(self, tmp_path):
        items = kit.transcripts()
        write(items, tmp_path)
        new, same, conflicts = classify(items, tmp_path)
        assert (new, [p.name for p in same], conflicts) == ([], [f"{t['id']}.json" for t in items], [])

    def test_the_three_groups_at_once(self, tmp_path):
        items = kit.transcripts()
        write(items[:2], tmp_path)
        (tmp_path / f"{items[1]['id']}.json").write_text("different", encoding="utf-8")
        new, same, conflicts = classify(items[:3], tmp_path)
        assert ([p.name for p in new], [p.name for p in same], [p.name for p in conflicts]) == (
            [f"{items[2]['id']}.json"], [f"{items[0]['id']}.json"], [f"{items[1]['id']}.json"],
        )

    def test_the_default_directory_is_generated_transcripts(self, tmp_path):
        from seeding import arms

        items = kit.transcripts()
        write(items[:1], None)
        new, same, conflicts = arms.classify_transcripts(items[:2])
        assert ([p.name for p in same], [p.parent for p in new]) == ([f"{items[0]['id']}.json"], [Path("generated") / "transcripts"])

    def test_the_agreement_with_write_transcripts(self, tmp_path):
        items = kit.transcripts()
        write(items[:3], tmp_path)
        new, same, conflicts = classify(items, tmp_path)
        assert (len(new), len(same), len(conflicts)) == (5, 3, 0)
