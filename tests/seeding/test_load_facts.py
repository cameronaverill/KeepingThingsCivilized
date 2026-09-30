"""load_facts and the shipped seeding/data/facts.json checked against the contract."""
import json
from pathlib import Path

import pytest
import seeding_kit as kit
from seeding.facts import Fact, load_facts

SHIPPED = Path(__file__).resolve().parents[2] / "seeding" / "data" / "facts.json"
IDS = [
    "sanctuary_jurisdiction_count",
    "statewide_sanctuary_states",
    "statewide_ban_states",
    "declined_detainers_2014_2017",
    "violent_offender_exceptions_share",
    "incarceration_rates",
    "federal_agents_authority",
    "noncitizen_criminal_law",
]
TYPES = ["statistic"] * 5 + ["qualitative", "law", "law"]


def dump(tmp_path, records, name="f.json"):
    p = tmp_path / name
    p.write_text(json.dumps(records))
    return p


def rec(**over):
    return {**kit.stat_kwargs(), **over}


class TestLoadFacts:
    def test_loads_list_in_order(self, tmp_path):
        p = dump(tmp_path, [rec(id="b"), rec(id="a")])
        assert [f.id for f in load_facts(p)] == ["b", "a"]

    def test_accepts_str_path(self, tmp_path):
        p = dump(tmp_path, [rec(id="a")])
        assert [f.id for f in load_facts(str(p))] == ["a"]

    def test_returns_fact_objects(self, tmp_path):
        p = dump(tmp_path, [rec(id="a")])
        assert isinstance(load_facts(p)[0], Fact)

    def test_duplicate_ids_raise_value_error(self, tmp_path):
        p = dump(tmp_path, [rec(id="a"), rec(id="b"), rec(id="a")])
        with pytest.raises(ValueError):
            load_facts(p)

    def test_empty_list_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            load_facts(dump(tmp_path, []))

    def test_empty_file_raises_value_error(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("")
        with pytest.raises(ValueError):
            load_facts(p)

    def test_invalid_record_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError):
            load_facts(dump(tmp_path, [rec(true_values=[-1])]))


@pytest.fixture(scope="module")
def shipped():
    return load_facts()


@pytest.fixture(scope="module")
def by_id(shipped):
    return {f.id: f for f in shipped}


class TestShippedFacts:
    def test_file_exists(self):
        assert SHIPPED.is_file()

    def test_default_path_equals_explicit_shipped_path(self, shipped):
        assert [f.model_dump() for f in load_facts(SHIPPED)] == [f.model_dump() for f in shipped]

    def test_ids_in_order(self, shipped):
        assert [f.id for f in shipped] == IDS

    def test_types_in_order(self, shipped):
        assert [f.type for f in shipped] == TYPES

    def test_eight_unique_ids(self, shipped):
        assert len({f.id for f in shipped}) == 8

    def test_none_ready(self, shipped):
        assert [f.ready() for f in shipped] == [False] * 8

    def test_none_owner_verified(self, shipped):
        assert [f.owner_verified_true for f in shipped] == [False] * 8

    def test_statistics_have_no_inflate_favors(self, shipped):
        assert [f.inflate_favors for f in shipped[:5]] == [None] * 5

    def test_non_statistics_have_no_error_claims_and_no_mirrors(self, shipped):
        assert [(f.error_claims, f.mirrors_approved) for f in shipped[5:]] == [(None, False)] * 3

    def test_source_notes(self, shipped):
        expected = "Owner-supplied 2026-09-30; not yet verified against a primary source."
        assert [f.source_note for f in shipped] == [expected] * 8

    def test_claim_true_non_empty(self, shipped):
        assert [bool(f.claim_true.strip()) for f in shipped] == [True] * 8

    def test_sanctuary_jurisdiction_count(self, by_id):
        f = by_id["sanctuary_jurisdiction_count"]
        assert (f.true_values, f.integer, f.max_value) == ([500, 560], True, None)
        assert "{v0}" in f.claim_template and "{v1}" in f.claim_template

    @pytest.mark.parametrize(
        "fid,value",
        [("statewide_sanctuary_states", 12), ("statewide_ban_states", 13), ("declined_detainers_2014_2017", 10000)],
    )
    def test_single_integer_statistics(self, by_id, fid, value):
        f = by_id[fid]
        assert (f.true_values, f.integer) == ([value], True)
        assert "{v0}" in f.claim_template and "{v1}" not in f.claim_template

    def test_violent_offender_share(self, by_id):
        f = by_id["violent_offender_exceptions_share"]
        assert (f.true_values, f.max_value) == ([60, 70], 100)
        assert "{v0}%" in f.claim_template and "{v1}%" in f.claim_template

    def test_violent_offender_overrides_exact(self, by_id):
        ov = by_id["violent_offender_exceptions_share"].level_overrides
        assert ov == {
            1: {"inflate": [66, 77], "deflate": [55, 63]},
            2: {"inflate": [80, 90], "deflate": [40, 47]},
            3: {"inflate": [95, 100], "deflate": [20, 23]},
        }

    def test_violent_offender_overrides_within_max_and_ordered(self, by_id):
        f = by_id["violent_offender_exceptions_share"]
        flat = [tuple(v) for lvl in f.level_overrides.values() for v in lvl.values()]
        assert [(0 < a < b <= f.max_value) for a, b in flat] == [True] * 6

    def test_violent_offender_inflate_above_and_deflate_below_true(self, by_id):
        f = by_id["violent_offender_exceptions_share"]
        infl = [f.level_overrides[l]["inflate"][0] for l in (1, 2, 3)]
        defl = [f.level_overrides[l]["deflate"][1] for l in (1, 2, 3)]
        assert (all(x > 60 for x in infl), all(x < 70 for x in defl)) == (True, True)

    def test_qualitative_fact(self, by_id):
        assert "lower rates" in by_id["incarceration_rates"].claim_true

    def test_law_facts_text(self, by_id):
        assert by_id["federal_agents_authority"].claim_true.startswith("Federal agents can operate")
        assert by_id["noncitizen_criminal_law"].claim_true.startswith("Noncitizens accused of state or local crimes")

    @pytest.mark.parametrize("side", ["left", "right"])
    @pytest.mark.parametrize("fid", IDS[:5])
    def test_every_statistic_seeds_without_error_once_made_ready(self, by_id, fid, side):
        from seeding.seeds import build_seeds

        f = by_id[fid].model_copy(update={"owner_verified_true": True, "inflate_favors": side})
        assert len(build_seeds(f)) == 6
