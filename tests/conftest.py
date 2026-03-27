import pytest_asyncio
from tortoise.contrib.test import tortoise_test_context
from typing import AsyncGenerator
from tortoise.context import TortoiseContext


@pytest_asyncio.fixture(autouse=True)
async def db() -> AsyncGenerator[TortoiseContext, None]:
    async with tortoise_test_context(
        modules=["tests.models"],
        db_url="sqlite://:memory:",
    ) as ctx:
        await ctx.generate_schemas()
        yield ctx
        for app_models in ctx.apps.values():
            for model in app_models.values():
                await model.all().delete()
