import shutil

import pytest

from specledger.config import Settings
from specledger.demo import create_demo
from specledger.parsing import Parser
from specledger.store import Ledger


@pytest.fixture(scope="session")
def demo_data(tmp_path_factory):
    root = tmp_path_factory.mktemp("specledger-demo")
    create_demo(root)
    return root


@pytest.fixture
def ledger(tmp_path, demo_data):
    data = tmp_path / "data"
    shutil.copytree(demo_data, data)
    settings = Settings(data, backend="text")
    return Ledger(settings, Parser(settings))
