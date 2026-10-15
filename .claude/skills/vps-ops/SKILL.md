---
name: vps-ops
description: Moscow VPS details and Telegram connectivity troubleshooting for this bot. Use when deploying, reading server logs, or when the bot replies slowly or not at all (ConnectTimeout / Max retries exceeded api.telegram.org).
---

# VPS operations

- Server: `root@201.24.125.174`, app in `/opt/bratchikovbot`, systemd unit `bratchikovbot.service`. Deploy with `./deploy.sh`; read logs with `./deploy.sh --logs` or `journalctl -u bratchikovbot -f` on the server.
- Telegram is throttled from this VPS. The IPv4 that DNS returns for `api.telegram.org` (149.154.166.110) is fully blocked, and about a third of new connections to the other Telegram addresses are dropped.
- Two countermeasures, both required:
  - `main.py` sets `telebot.apihelper.CONNECT_TIMEOUT = 3` with `RETRY_ON_ERROR`, so a dropped connection is retried in seconds instead of hanging for 30 s.
  - The server's `/etc/hosts` pins `api.telegram.org` to `2001:67c:4e8:f004::9` and `149.154.167.220`, between `# BEGIN/END bratchikovbot telegram pin` markers (backups at `/etc/hosts.bak-*`). If Telegram moves its API to new addresses, update or remove that block.
- Symptoms: the service shows "running" but the bot replies slowly or not at all, and logs show `ConnectTimeout` / `Max retries exceeded ... api.telegram.org`. To check, run `curl -sS --connect-timeout 3 -o /dev/null -w '%{http_code}\n' https://api.telegram.org` on the VPS a few times; `302` means it got through.
