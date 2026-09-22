.DEFAULT_GOAL := help

.PHONY: help up down restart logs deploy shell migrate superuser check-domain init-env
help:            ## Показать список доступных команд
	@echo "Доступные команды:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Просто 'make' без аргументов показывает эту справку."

up: init-env     ## Собрать и запустить проект
	docker compose build
	docker compose up -d
	docker compose exec web python manage.py migrate --noinput
	docker compose exec web python manage.py collectstatic --noinput

down:            ## Остановить проект
	docker compose down

restart:         ## Перезапустить проект
	docker compose restart

logs:            ## Логи всех сервисов
	docker compose logs -f

deploy: check-domain up  ## Полный деплой с проверкой домена

shell:           ## Bash в контейнере web
	docker compose exec web bash

migrate:         ## Применить миграции
	docker compose exec web python manage.py migrate

superuser:       ## Создать суперпользователя
	docker compose exec web python manage.py createsuperuser

check-domain:    ## Проверить соответствие домена в .env и Caddyfile
	docker compose exec web python /app/scripts/check_domain.py

init-env:        ## Инициализировать .env (генерация ключей + запрос параметров)
	docker compose run --rm web python /app/scripts/init_env.py
