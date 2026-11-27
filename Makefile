.PHONY: test lint typecheck doctor verify-all check

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run mypy src

doctor:
	uv run llama-mcp-install --doctor

verify-all:
	uv run llama-mcp-install --verify-all

check: lint typecheck test
