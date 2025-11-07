#!/bin/bash
# Скрипт установки Repology Bot как systemd сервиса

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==================================="
echo "Установка Repology Bot как сервиса"
echo "==================================="
echo

# Определение текущего пользователя и группы
# Можно передать пользователя первым аргументом
if [ -n "$1" ]; then
    CURRENT_USER="$1"
    CURRENT_GROUP=$(id -gn "$CURRENT_USER")
else
    CURRENT_USER=$(whoami)
    CURRENT_GROUP=$(id -gn)
fi
PROJECT_DIR="$SCRIPT_DIR"

# Если запущено от root и пользователь не указан - ошибка
if [ "$CURRENT_USER" = "root" ] && [ -z "$1" ]; then
    echo "❌ ОШИБКА: Нельзя устанавливать сервис от root без указания пользователя!"
    echo "Запустите от имени нужного пользователя, или укажите пользователя:"
    echo "  sudo ./install-service.sh username"
    exit 1
fi

echo "Параметры установки:"
echo "  Пользователь: $CURRENT_USER"
echo "  Группа: $CURRENT_GROUP"
echo "  Директория проекта: $PROJECT_DIR"
echo

# Проверка конфигурации
echo "Проверка конфигурации..."
if [ ! -f "config.toml" ]; then
    echo "❌ Файл config.toml не найден!"
    echo "Скопируйте config.toml.example в config.toml и настройте его"
    exit 1
fi

# Проверка что бот работает
echo "Проверка что бот запускается..."
if ! uv run python -c "from core.config import load_config; load_config()" 2>/dev/null; then
    echo "❌ Ошибка в конфигурации!"
    exit 1
fi

echo "✅ Конфигурация в порядке"
echo

# Создание директорий
echo "Создание директорий..."
mkdir -p data logs .uv-cache

# Установка владельца директорий
if [ "$CURRENT_USER" != "$(whoami)" ]; then
    chown -R "$CURRENT_USER:$CURRENT_GROUP" data logs .uv-cache
fi

echo "✅ Директории созданы"
echo

# Установка
echo "Установка системного сервиса..."

# Создание временного service файла с подстановкой переменных
echo "Создание service файла..."
TEMP_SERVICE="$PROJECT_DIR/.repology-bot.service.tmp"
sed -e "s|USER_PLACEHOLDER|$CURRENT_USER|g" \
    -e "s|GROUP_PLACEHOLDER|$CURRENT_GROUP|g" \
    -e "s|WORKDIR_PLACEHOLDER|$PROJECT_DIR|g" \
    -e "s|READWRITE_PLACEHOLDER|$PROJECT_DIR/data $PROJECT_DIR/logs $PROJECT_DIR/.uv-cache|g" \
    repology-bot.service > "$TEMP_SERVICE"

# Копирование service файла
sudo cp "$TEMP_SERVICE" /etc/systemd/system/repology-bot.service
rm "$TEMP_SERVICE"

echo "✅ Service файл настроен для пользователя $CURRENT_USER"
echo

# Перезагрузка systemd
sudo systemctl daemon-reload

# Включение и запуск
sudo systemctl enable repology-bot.service
sudo systemctl start repology-bot.service

echo
echo "✅ Сервис установлен и запущен!"
echo
echo "Проверка статуса:"
sudo systemctl status repology-bot.service --no-pager
