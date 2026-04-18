# Flowrex Algo — common dev tasks
# Run from repo root: make <target>

.PHONY: help test test-backend lint lint-frontend dev build deploy logs ps shell-backend psql migrate

help:
	@echo "Flowrex Algo — common dev tasks"
	@echo ""
	@echo "  make test            Run backend pytest suite"
	@echo "  make lint            Run frontend ESLint"
	@echo "  make dev             Start dev stack (docker-compose.yml)"
	@echo "  make build           Build prod backend image"
	@echo "  make deploy          Run scripts/deploy.sh on this server"
	@echo "  make logs            Tail backend logs"
	@echo "  make ps              List running containers"
	@echo "  make shell-backend   Open a shell in the backend container"
	@echo "  make psql            Open psql in the postgres container"
	@echo "  make migrate         Run alembic upgrade head"

test: test-backend

test-backend:
	cd backend && pytest tests/ -v --tb=short

lint: lint-frontend

lint-frontend:
	cd frontend && npm run lint

dev:
	docker compose up -d
	@echo "Dev stack running. Frontend: http://localhost:3000"

build:
	docker compose -f docker-compose.prod.yml build backend

deploy:
	bash scripts/deploy.sh

logs:
	docker logs -f flowrex-backend

ps:
	docker compose -f docker-compose.prod.yml ps

shell-backend:
	docker exec -it flowrex-backend /bin/bash

psql:
	docker exec -it flowrex-postgres psql -U flowrex -d flowrex_algo

migrate:
	docker exec flowrex-backend alembic upgrade head
