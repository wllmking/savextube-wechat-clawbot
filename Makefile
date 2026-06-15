.PHONY: help check test lint build up logs stop restart login
 
.PHONY: setup

help:
	@echo "Available targets: check test lint build up logs stop restart login"
	@echo "  check   - run deployment sanity checks"
	@echo "  test    - run unit tests"
	@echo "  lint    - run Python syntax checks"
	@echo "  build   - build the Docker image"
	@echo "  up      - start the service in detached mode"
	@echo "  logs    - follow container logs"
	@echo "  stop    - stop the service"
	@echo "  restart - restart the service"
	@echo "  login   - start login flow for the ClawBot"

	@echo "  setup   - create runtime dirs and copy example config"

check:
	python3 deploy_check.py

setup:
	./scripts/setup.sh

test:
	python3 -m unittest discover -s tests -v

lint:
	python3 -m py_compile savextube_wechat.py clawbot_wechat.py wechat_downloader.py douyin_note_downloader.py xiaohongshu_downloader.py config_reader.py deploy_check.py

build:
	docker compose build

up:
	docker compose up -d

logs:
	docker compose logs -f savextube-wechat

stop:
	docker compose stop

restart:
	docker compose restart

login:
	docker compose run --rm savextube-wechat login
