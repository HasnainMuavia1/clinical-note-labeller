.PHONY: up down logs ps test test-backend test-frontend build-frontend fmt

up:
	docker compose up --build -d

down:
	docker compose down

clean:
	docker compose down -v

logs:
	docker compose logs -f api worker

ps:
	docker compose ps

test: test-backend test-frontend

test-backend:
	cd backend && REFERENCE_ROOT=.. .venv/bin/python -m pytest -q

test-frontend:
	cd frontend && npm test

build-frontend:
	cd frontend && npm run build

fmt:
	cd backend && .venv/bin/ruff check --fix . && .venv/bin/ruff format .
