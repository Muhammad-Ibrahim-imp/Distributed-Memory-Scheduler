import pytest

from common.types.generation import CoordinatorGeneration


def test_generation_is_canonical_uuid4_and_immutable():
    generation = CoordinatorGeneration.create()
    assert len(generation.value) == 36
    assert CoordinatorGeneration(generation.value) == generation
    with pytest.raises((ValueError, AttributeError)):
        generation.value = "bad"
    with pytest.raises(ValueError):
        CoordinatorGeneration("not-a-uuid")


def test_each_coordinator_process_gets_a_new_generation():
    first, restarted = CoordinatorGeneration.create(), CoordinatorGeneration.create()
    assert first.value != restarted.value
