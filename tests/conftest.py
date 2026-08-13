import sqlite3

import pytest

import storage


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    storage.create_schema(connection)
    yield connection
    connection.close()
