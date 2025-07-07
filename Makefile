tests:
	poetry run pytest --asyncio-mode=auto --cov tortoise_serializer

clean:
	find . -name "__pycache__" -type d -exec rm -rf {} +

.PHONY: tests clean
