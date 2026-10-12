import pytest

from radar.db import connect


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / 'nested' / 'radar.db')
    yield connection
    connection.close()


@pytest.fixture
def assessment():
    return {
        'module_code': 'COMP0001', 'module_name': 'Computing', 'title': 'Essay',
        'type': 'essay', 'due_date': '2026-12-04', 'due_time': None,
        'weighting_pct': 40, 'word_count': 2000, 'estimated_hours': 20,
        'source_quote': 'Submit by 4 December 2026.',
    }
