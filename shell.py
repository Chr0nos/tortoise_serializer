import asyncio
from tortoise import Tortoise
from tests.models import * #  noqa
from tortoise_serializer import Serializer, ModelSerializer #  noqa


TORTOISE_CONFIG = {
    "connections": {
        "default": "sqlite://:memory:",
    },
    "app": {
        "models": {
            "models": ["tests.models"],
            "default_connection": "default"
        }
    }
}


async def connect_db():
    await Tortoise.init(
        db_url=TORTOISE_CONFIG['connections']['default'],
        modules={"models": TORTOISE_CONFIG['app']['models']['models']}
    )
    await Tortoise.generate_schemas()


def bye():
    asyncio.run(Tortoise.close_connections())
    exit()


asyncio.run(connect_db())
