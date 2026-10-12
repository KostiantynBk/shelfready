import pytest


@pytest.fixture
def anyio_backend():
    # Async tests (@pytest.mark.anyio) run on asyncio only, like the app
    return "asyncio"
