COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml
DEV_SERVICES = qdrant mem0 sillytavern
PROD_SERVICES = qdrant-prod mem0-prod sillytavern-prod

.PHONY: help dev-up dev-down prod-up prod-down down status

help:
	@echo "make dev-up     start the dev stack (+ ollama if not already up)"
	@echo "make dev-down   stop the dev stack, leave ollama/prod running"
	@echo "make prod-up    start the prod stack (+ ollama if not already up)"
	@echo "make prod-down  stop the prod stack, leave ollama/dev running"
	@echo "make down       stop everything, including ollama"
	@echo "make status     show what's running"

dev-up:
	docker compose up -d

dev-down:
	docker compose stop $(DEV_SERVICES)

prod-up:
	$(COMPOSE) up -d $(PROD_SERVICES)

prod-down:
	$(COMPOSE) stop $(PROD_SERVICES)

down:
	$(COMPOSE) stop

status:
	$(COMPOSE) ps