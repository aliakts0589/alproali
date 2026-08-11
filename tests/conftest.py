from __future__ import annotations

import os
import tempfile

# Isolated, network-free test environment — must be set before alpro imports.
_tmp = tempfile.mkdtemp(prefix="alpro-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ALPRO_ALLOW_NETWORK"] = "0"
os.environ["ALPRO_USER_NAME"] = "Ali"
os.environ["ALPRO_BASE_CURRENCY"] = "TRY"
# Multi-user auth fixtures (magic link + invite gate + API-key bridge)
os.environ["ALPRO_API_TOKEN"] = "test-admin-token"
os.environ["ALPRO_FOUNDER_EMAIL"] = "founder@alpro.test"
os.environ["ALPRO_INVITES"] = "a@alpro.test,b@alpro.test,c@alpro.test"
os.environ["ALPRO_EMAIL_MODE"] = "console"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("EVDS_API_KEY", None)

import pytest
from sqlalchemy import update

from alpro.core.db import init_db, session
from alpro.core.models import Transaction
from alpro.data.bist_demo import run_all
from alpro.demo import seed_demo


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()
    from alpro.api.auth import get_or_create_founder

    with session() as s:
        seed_demo(s)
        run_all(s)  # fixture mode everywhere (network disabled)
        # Demo işlemler kurucuya bağlanır — app startup'taki benimsemeyle aynı.
        founder = get_or_create_founder(s)
        s.execute(
            update(Transaction)
            .where(Transaction.user_id.is_(None))
            .values(user_id=founder.id)
        )
    yield


@pytest.fixture()
def db_session():
    with session() as s:
        yield s
