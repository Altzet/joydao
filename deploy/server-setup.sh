#!/bin/bash
# Установка JoyDao на Ubuntu (22.04/24.04). Запуск: bash deploy/server-setup.sh ваш.домен
# HTTPS: nginx + certbot (snap). Caddy НЕ ставим — он конфликтует с nginx за порты 80/443.
set -e
DOMAIN=${1:?Использование: bash deploy/server-setup.sh ваш.домен (например joylao.duckdns.org)}

echo "=== 1/4 Python, nginx, certbot ==="
apt update
apt install -y python3-venv python3-pip nginx snapd
# ponytail: certbot из snap — у него свой Python; apt-версия ломается от pip-пакетов cryptography
snap install --classic certbot
ln -sf /snap/bin/certbot /usr/bin/certbot

echo "=== 2/4 Python-окружение ==="
cd /opt/joydao
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

echo "=== 3/4 HTTPS (nginx + certbot) ==="
# Не трогаем конфиг, если домен уже настроен (иначе дубль server_name)
if ! grep -rqs "server_name $DOMAIN" /etc/nginx/sites-enabled/; then
cat > /etc/nginx/sites-available/joydao <<NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/joydao /etc/nginx/sites-enabled/joydao
fi
systemctl disable --now caddy 2>/dev/null || true
nginx -t && systemctl enable --now nginx && systemctl reload nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect

echo "=== 4/4 Автозапуск (systemd) ==="
cp deploy/joydao.service /etc/systemd/system/joydao.service
systemctl daemon-reload
systemctl enable --now joydao

sleep 2
systemctl status joydao --no-pager -l | head -8
echo ""
echo "Готово! Проверьте: https://$DOMAIN"
