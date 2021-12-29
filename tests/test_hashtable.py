import pytest
import numpy as np
from npstructures import HashTable

@pytest.fixture
def array_list():
    return [[0, 1, 2],
            [2, 1],
            [1, 2, 3, 4],
            [3]]

def test_lookup():
    keys = [0, 3, 7, 11]
    values = np.arange(4)
    table = HashTable(keys, values, 5)
    assert np.all(table[keys] == values)