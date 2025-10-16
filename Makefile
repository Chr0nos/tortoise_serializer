all: test check

test:
	uv run pytest --asyncio-mode=auto --cov tortoise_serializer

clean:
	find . -name "__pycache__" -type d -exec rm -rf {} +

check:
	uv run ruff check ./tortoise_serializer/

shell:
	uv run ipython -i shell.py

.PHONY: tests clean check shell
