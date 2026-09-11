#!/usr/bin/env bash
#
# Деплой бота на продакшн-VPS.
#
#   ./deploy.sh          — деплой с подтверждением
#   ./deploy.sh -y       — без подтверждения
#   ./deploy.sh --logs   — только показать логи, ничего не деплоить
#
# Аутентификация — по SSH-ключу (~/.ssh/id_ed25519). Пароли в скрипте
# не хранятся и не запрашиваются: если ключ перестанет работать,
# ssh честно откажет, а не начнёт спрашивать пароль.
#
# Переопределяется через переменные окружения:
#   BOT_SERVER=root@1.2.3.4 ./deploy.sh

set -euo pipefail

SERVER="${BOT_SERVER:-root@201.24.125.174}"
APP_DIR="${BOT_APP_DIR:-/opt/bratchikovbot}"
SERVICE="bratchikovbot.service"
RUN_AS="deploy"          # владелец файлов на сервере, от него делаем git pull
BRANCH="main"

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
step() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
die()  { red "✗ $*"; exit 1; }

remote() { ssh -o ConnectTimeout=20 "$SERVER" "$@"; }

# Ждём, пока служба поднимется, вместо фиксированной паузы: бот стартует
# то за секунду, то за десять — зависит от ответа Telegram и БД.
wait_active() {
	local i
	for i in $(seq 1 15); do
		if remote "systemctl is-active --quiet $SERVICE"; then
			# active сразу после restart ещё не значит, что процесс выжил:
			# при падении systemd успевает пометить его активным до краха.
			sleep 2
			remote "systemctl is-active --quiet $SERVICE" && return 0
			return 1
		fi
		sleep 2
	done
	return 1
}

# --- только логи ---------------------------------------------------------
if [[ "${1:-}" == "--logs" ]]; then
	remote "journalctl -u $SERVICE -n 60 --no-pager"
	exit 0
fi

ASSUME_YES=false
[[ "${1:-}" == "-y" || "${1:-}" == "--yes" ]] && ASSUME_YES=true

cd "$(dirname "$0")"

# --- 1. проверки локально ------------------------------------------------
step "Проверяю локальное состояние"

current_branch=$(git branch --show-current)
[[ "$current_branch" == "$BRANCH" ]] \
	|| die "Вы на ветке '$current_branch', а деплой идёт с '$BRANCH'. Переключитесь: git checkout $BRANCH"

if [[ -n "$(git status --porcelain)" ]]; then
	git status --short
	die "Есть незакоммиченные изменения. Закоммитьте их — на сервер попадает только то, что в git."
fi

git fetch --quiet origin "$BRANCH"
ahead=$(git rev-list --count "origin/$BRANCH..HEAD")
behind=$(git rev-list --count "HEAD..origin/$BRANCH")

[[ "$behind" -gt 0 ]] \
	&& die "Локальная ветка на $behind коммит(ов) позади origin. Сделайте git pull и проверьте, что ничего не сломалось."

if [[ "$ahead" -gt 0 ]]; then
	ylw "Не запушено коммитов: $ahead"
	git --no-pager log --oneline "origin/$BRANCH..HEAD"
else
	grn "✓ Всё запушено"
fi

# --- 2. что поедет на сервер --------------------------------------------
step "Сравниваю с сервером"

remote_sha=$(remote "cd $APP_DIR && sudo -u $RUN_AS git rev-parse HEAD") \
	|| die "Не удалось подключиться к $SERVER или прочитать $APP_DIR"
local_sha=$(git rev-parse HEAD)

if [[ "$remote_sha" == "$local_sha" ]]; then
	grn "✓ Сервер уже на $(git rev-parse --short HEAD) — деплоить нечего"
	if remote "systemctl is-active --quiet $SERVICE"; then
		grn "✓ Служба работает"
		exit 0
	fi
	red "✗ Но служба не работает — перезапускаю"
	remote "systemctl restart $SERVICE"
	wait_active || die "Служба не поднялась. Логи: ./deploy.sh --logs"
	grn "✓ Служба запущена"
	exit 0
fi

echo "Сервер:  ${remote_sha:0:7}"
echo "Локально: ${local_sha:0:7}"
echo
echo "Поедут коммиты:"
git --no-pager log --oneline "$remote_sha..$local_sha" 2>/dev/null \
	|| echo "  (сервер на коммите, которого нет локально — проверьте вручную)"

if ! $ASSUME_YES; then
	echo
	ylw "Бот перезапустится — на несколько секунд перестанет отвечать."
	read -r -p "Деплоить? [y/N] " answer
	[[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "Отменено."; exit 0; }
fi

# --- 3. пушим -----------------------------------------------------------
if [[ "$ahead" -gt 0 ]]; then
	step "Пушу в origin/$BRANCH"
	git push origin "$BRANCH"
fi

# --- 4. деплой ----------------------------------------------------------
step "Обновляю код на сервере"

remote "bash -s" <<REMOTE
set -euo pipefail
cd $APP_DIR

# requirements.txt мог измениться — запомним, чтобы поставить зависимости только при необходимости
before=\$(sha256sum requirements.txt | cut -d' ' -f1)

# git pull от владельца файлов, иначе после обновления сломаются права
sudo -u $RUN_AS git pull --ff-only origin $BRANCH

after=\$(sha256sum requirements.txt | cut -d' ' -f1)
if [ "\$before" != "\$after" ]; then
	echo "requirements.txt изменился — обновляю зависимости"
	sudo -u $RUN_AS $APP_DIR/.venv/bin/pip install --quiet -r requirements.txt
else
	echo "requirements.txt без изменений — зависимости не трогаю"
fi

echo "Развёрнут коммит: \$(sudo -u $RUN_AS git log --oneline -1)"
REMOTE

# --- 5. рестарт ---------------------------------------------------------
step "Перезапускаю бота"

# Telegram не допускает два polling-процесса на один токен, поэтому
# именно restart (не start) — старый процесс должен умереть до старта нового.
remote "systemctl restart $SERVICE"

if wait_active; then
	grn "✓ Служба запущена, PID $(remote "systemctl show $SERVICE -p ExecMainPID --value")"
else
	red "✗ Служба не поднялась!"
	remote "journalctl -u $SERVICE -n 40 --no-pager"
	die "Деплой неуспешен. Откат: ssh $SERVER 'cd $APP_DIR && sudo -u $RUN_AS git reset --hard $remote_sha' && ssh $SERVER 'systemctl restart $SERVICE'"
fi

# --- 6. проверка --------------------------------------------------------
step "Проверяю логи на ошибки"

logs=$(remote "journalctl -u $SERVICE --since '-1 min' --no-pager")
echo "$logs" | tail -20

if echo "$logs" | grep -qiE 'Traceback|ModuleNotFoundError|Conflict: terminated by other getUpdates'; then
	echo
	red "✗ В логах есть ошибки — посмотрите вывод выше"
	echo "  Полные логи: ./deploy.sh --logs"
	exit 1
fi

if remote "test -f $APP_DIR/custom_rate.json"; then
	echo
	ylw "⚠ На сервере есть custom_rate.json — курс задан вручную через /set_krw_rate"
	remote "cat $APP_DIR/custom_rate.json"
	ylw "  Он перекрывает автоматический курс ЦБ. Сбросить: команда /reset_krw_rate в боте."
fi

echo
grn "✓ Деплой завершён: $(git rev-parse --short HEAD)"
echo "  Логи в реальном времени: ssh $SERVER 'journalctl -u $SERVICE -f'"
