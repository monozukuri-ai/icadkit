"""Shared authored fixture builders exposed to pytest."""

import pytest
from fixture_builders import document_factory as _document_factory
from fixture_builders import icd_factory as _icd_factory
from fixture_builders import resource_factory as _resource_factory

icd_factory = pytest.fixture(_icd_factory)
resource_factory = pytest.fixture(_resource_factory)
document_factory = pytest.fixture(_document_factory)
