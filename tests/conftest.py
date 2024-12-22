import pytest
from tortoise import Tortoise


@pytest.fixture(scope="session")
def tortoise_config():
    return {
        "connections": {
            "default": "sqlite://:memory:"  # Using in-memory SQLite for testing
        },
        "apps": {
            "models": {
                "models": [
                    "tests.models",
                    # "aerich.models",
                ],  # Replace with your actual models
                "default_connection": "default",
            },
        },
    }


@pytest.fixture(scope="session", autouse=True)
async def initialize_tortoise(tortoise_config):
    """
    Initialize Tortoise ORM for the test session.
    """
    await Tortoise.init(
        db_url=tortoise_config["connections"]["default"],
        modules={"models": tortoise_config["apps"]["models"]["models"]},
    )
    await Tortoise.generate_schemas()
    yield
    await Tortoise.close_connections()


@pytest.fixture(scope="function", autouse=True)
async def cleanup_db():
    """
    Clean up the database by truncating all tables after each test.
    This ensures a clean state for subsequent tests.
    """
    for model in Tortoise.apps.get("models").values():
        await model.all().delete()
