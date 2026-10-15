# CLAUDE.md

Telegram bot for the company "Импорт без проблем" that calculates the cost of importing a car to Russia from South Korea (Encar links) and China (che168 links).

## China Flow (global.che168.com)

- Both `https://global.che168.com/{locale}/detail/{infoid}` links and old `m.che168.com` links (`infoid=` in the query) are calculated. Old links are converted to the global link with the same infoid first.
- `che168_scraper.py` calls the JSON API at `globalapi.che168.com/api/v1` (`carinfo/{infoid}` and `specparam?specid=`). The HTML pages are behind a Tencent EdgeOne captcha, so never scrape them.
- The API gives prices in USD. The site converts from yuan at a fixed rate (`CHE168_CNY_PER_USD`, default 6.575), so the bot recovers the exact yuan price and calculates in CNY as before. A log warning "site CNY/USD rate may have changed" means the rate needs re-deriving.
- Car age comes from `manufacturedate`, falling back to `regdate`. Horsepower depends on fuel type: engine Ps for petrol/diesel, motor Ps for EV and range extender, combined system Ps for hybrids. It is never defaulted; if missing, the bot asks the user.
- Tests: `venv/bin/python -m unittest discover -s tests -v` (fixtures are real API responses).

## Deployment

- The bot runs on a Timeweb VPS in Moscow as the systemd unit `bratchikovbot.service`, not on Heroku. Deploy with `./deploy.sh`; read logs with `./deploy.sh --logs`. Server details and Telegram connectivity troubleshooting are in the `vps-ops` skill.
- Telegram is throttled from the VPS. Never remove `telebot.apihelper.CONNECT_TIMEOUT = 3` / `RETRY_ON_ERROR` in `main.py`: without them a dropped connection hangs for 30 s and the bot stops replying.
- The subscription check on `/start` has always failed with `chat not found`, because `@bratchikov_cars` is not a channel the bot can see. Users just get the "subscribe" prompt; nothing else is gated.
