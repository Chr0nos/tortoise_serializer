tests:
	poetry run pytest --asyncio-mode=auto --cov tortoise_serializer

.PHONY: tests
