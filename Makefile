.PHONY: help build up down restart logs shell migrate makemigrations superuser collectstatic test clean deploy check-domain

help: ## Показать список команд
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Собрать Docker образы
	docker-compose build

up: ## Запустить все сервисы
	docker-compose up -d

down: ## Остановить все сервисы
	docker-compose down

restart: ## Перезапустить сервисы
	docker-compose restart

logs: ## Показать логи
	docker-compose logs -f

shell: ## Войти в контейнер web
	docker-compose exec web sh

migrate: ## Применить миграции
	docker-compose exec web python manage.py migrate

makemigrations: ## Создать миграции
	docker-compose exec web python manage.py makemigrations

superuser: ## Создать суперпользователя
	docker-compose exec web python manage.py createsuperuser

collectstatic: ## Собрать статику
	docker-compose exec web python manage.py collectstatic --noinput

test: ## Запустить тесты
	docker-compose exec web python manage.py test

clean: ## Очистить контейнеры и образы
	docker-compose down --rmi all --volumes --remove-orphans

deploy: check-domain build up migrate collectstatic ## Полный деплой

check-domain: ## Проверить соответствие домена
	@python scripts/check_domain.py
