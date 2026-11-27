.PHONY: test lint typecheck doctor check

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

doctor:
	uv run llama-mcp-install --doctor

check: lint typecheck test
