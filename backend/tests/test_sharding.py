import pytest

from tests import sharding


def test_every_module_lands_on_exactly_one_shard() -> None:
    ids = [f"tests/test_{m}.py::t{i}" for m in "abcdefg" for i in range(1, 4 + ord(m) % 5)]
    owner = sharding.assign(ids, 3)
    assert set(owner) == {sharding.module_of(i) for i in ids}
    assert set(owner.values()) <= {1, 2, 3}
    assert sharding.assign(list(reversed(ids)), 3) == owner  # the order of collection does not matter


def test_the_shards_are_roughly_even() -> None:
    ids = [f"tests/test_{m}.py::t{i}" for m in range(20) for i in range(m + 1)]
    owner = sharding.assign(ids, 2)
    load = [sum(1 for i in ids if owner[sharding.module_of(i)] == s) for s in (1, 2)]
    assert abs(load[0] - load[1]) <= 20


def test_bad_specs_are_refused() -> None:
    assert sharding.parse("2/3") == (2, 3)
    for bad in ("0/2", "3/2", "x", "1"):
        with pytest.raises(ValueError):
            sharding.parse(bad)
