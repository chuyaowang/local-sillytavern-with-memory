COMPOSE = docker compose -f docker-compose.yml -f docker-compose.prod.yml
# Shared between dev and prod (GPU/VRAM is the scarce resource) -- ollama is
# NOT here on purpose. It's still defined in docker-compose.yml as a
# fallback but is unused; omitting it here is what keeps it from being
# started by any of these targets. See docs/LLAMA_CPP_BENCHMARK.md.
SHARED_SERVICES = llama-cpp
DEV_SERVICES = qdrant mem0 sillytavern
PROD_SERVICES = qdrant-prod mem0-prod sillytavern-prod

.PHONY: help dev-up dev-down prod-up prod-down down status

help:
	@echo "make dev-up     start the dev stack (+ llama-cpp if not already up)"
	@echo "make dev-down   stop the dev stack, leave llama-cpp/prod running"
	@echo "make prod-up    start the prod stack (+ llama-cpp if not already up)"
	@echo "make prod-down  stop the prod stack, leave llama-cpp/dev running"
	@echo "make down       stop everything that's running"
	@echo "make status     show what's running"

dev-up:
	docker compose up -d $(SHARED_SERVICES) $(DEV_SERVICES)
	@echo ""
	@echo "SillyTavern:    http://localhost:8000"
	@echo "Memory manager: http://localhost:8001/ui/"

dev-down:
	docker compose stop $(DEV_SERVICES)

prod-up:
	$(COMPOSE) up -d $(SHARED_SERVICES) $(PROD_SERVICES)
	@echo ""
	@echo "SillyTavern:    http://localhost:8010"
	@echo "Memory manager: http://localhost:8011/ui/"

prod-down:
	$(COMPOSE) stop $(PROD_SERVICES)

down:
	$(COMPOSE) stop

status:
	$(COMPOSE) ps