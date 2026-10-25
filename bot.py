#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FunPay Studio 1.1 — весь проект в одном bot.py, без платного диска Render.

В GitHub загрузите только этот файл с именем bot.py.
Render -> New -> Web Service -> Python 3 -> Instance type: Free.
Build Command: python bot.py --install
Start Command: python bot.py
Health Check Path: /healthz

Environment:
  PYTHON_VERSION=3.12.14
  DATABASE_URL=<прямой PostgreSQL URL из Neon; Connection pooling выключен>
  ADMIN_PASSWORD=<случайный пароль 16+ символов>
  MASTER_KEY=<отдельный постоянный секрет 32+ символов>
Не добавляйте диск. Не размещайте секреты в коде или GitHub.

python bot.py --secrets  : сгенерировать два секрета
python bot.py --guide    : полная инструкция без установки зависимостей
python bot.py --help     : список команд

Панель, исходники расширения, иконки, звуки и инструкция встроены ниже.
Расширение скачивается ZIP-архивом на /install, затем устанавливается вручную.
На Render данные сохраняются в DATABASE_URL; локально доступна SQLite.
Render Free спит после 15 минут без входящих запросов: в это время бот не работает.
Free-тариф базы также имеет квоты. Круглосуточная облачная работа не гарантируется.
Реальные операции на FunPay перед использованием необходимо проверить на своём аккаунте.

Зависимости устанавливаются из PyPI, FunPayAPI — из зафиксированного upstream
commit с проверкой SHA-256. Сторонний исходник не встроен в этот файл.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIR = BASE_DIR / '.funpay_studio_runtime' / 'vendor'
DEPENDENCIES = (
    'fastapi==0.159.1', 'uvicorn==0.54.0', 'cryptography==50.0.2',
    'httpx[socks]==0.28.1', 'requests[socks]==2.34.2', 'requests-toolbelt==1.0.0',
    'beautifulsoup4==4.15.0', 'lxml==6.1.3', 'psycopg[binary]==3.3.6',
)
API_COMMIT = 'c09dd13be25072bb3d5cdbba33d1e0d70e477710'
API_SHA256 = '1e1c656376778fde600051bb6ebfb0b664cb6d8518dc7461a5a17bd0c3941bbc'


def install_dependencies():
    """Explicit build step. No package installation during normal web requests."""
    if sys.version_info < (3, 12):
        raise RuntimeError('Use Python 3.12 or newer.')
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', *DEPENDENCIES])
    url = f'https://codeload.github.com/sidor0912/FunPayCardinal/zip/{API_COMMIT}'
    request = urllib.request.Request(url, headers={'User-Agent': 'FunPayStudio-Build/1.1'})
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read(10 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != API_SHA256:
        raise RuntimeError('FunPayAPI checksum mismatch: installation stopped.')
    prefix = f'FunPayCardinal-{API_COMMIT}/FunPayAPI/'
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=VENDOR_DIR) as temp:
        package = Path(temp) / 'FunPayAPI'
        package.mkdir()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for entry in archive.infolist():
                if not entry.filename.startswith(prefix) or not entry.filename.endswith('.py'):
                    continue
                relative = Path(entry.filename[len(prefix):])
                if '..' in relative.parts or relative.is_absolute():
                    raise RuntimeError('Unsafe upstream path')
                destination = package / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(entry))
        if not (package / 'account.py').exists():
            raise RuntimeError('FunPayAPI package is missing')
        destination = VENDOR_DIR / 'FunPayAPI'
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(package), str(destination))
    (VENDOR_DIR / '.commit').write_text(API_COMMIT)
    print('Dependencies installed. Start: python bot.py')


def load_local_env():
    """Optional local .env; real environment variables always take precedence."""
    path = BASE_DIR / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, value = line.split('=', 1)
        name, value = name.strip(), value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if name and name.replace('_', '').isalnum():
            os.environ.setdefault(name, value)


def early_cli():
    parser = argparse.ArgumentParser(description='FunPay Studio: один bot.py, панель + расширение + Telegram.')
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument('--install', action='store_true', help='Установить зависимости; Render Build Command.')
    commands.add_argument('--guide', action='store_true', help='Вывести полную инструкцию.')
    commands.add_argument('--secrets', action='store_true', help='Создать ADMIN_PASSWORD и MASTER_KEY.')
    commands.add_argument('--init-local', action='store_true', help='Создать .env для локального запуска, если его нет.')
    commands.add_argument('--restore', metavar='BACKUP_JSON', help='Восстановить копию в пустую базу; нужен исходный MASTER_KEY.')
    commands.add_argument('--import-sqlite', metavar='OLD_SQLITE', help='Перенести старую базу 1.0 в пустую PostgreSQL.')
    commands.add_argument('--check-db', action='store_true', help='Проверить подключение и создать таблицы.')
    args = parser.parse_args()
    if args.guide:
        print(USER_GUIDE)
        raise SystemExit(0)
    if args.secrets:
        print('ADMIN_PASSWORD=' + secrets.token_urlsafe(24))
        print('MASTER_KEY=' + secrets.token_urlsafe(48))
        raise SystemExit(0)
    if args.init_local:
        target = BASE_DIR / '.env'
        value = 'ADMIN_PASSWORD=' + secrets.token_urlsafe(24) + '\nMASTER_KEY=' + secrets.token_urlsafe(48)
        value += '\nPUBLIC_URL=http://127.0.0.1:8000\nDATA_DIR=./data\n'
        with target.open('x', encoding='utf-8') as out:
            out.write(value)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        print('Создан .env. Пароль панели — значение ADMIN_PASSWORD в этом файле. Не загружайте .env в GitHub.')
        raise SystemExit(0)
    if args.install:
        install_dependencies()
        raise SystemExit(0)
    load_local_env()
    return args


USER_GUIDE = r'''# FunPay Studio 1.1 — один bot.py

Расширение для настольного Chromium + личная веб-панель + сервер автоматизации + Telegram-бот.
Исходный код, панель, расширение и эта инструкция находятся в одном bot.py. Реальных ключей и аккаунтов в файле нет.

## Что сделано

| Функция | Реализация |
|---|---|
| Фон FunPay | Своя PNG/JPEG/WebP-картинка, цвет, затемнение, акцент кнопок. Видно только в вашем браузере. |
| Свои звуки | Отдельное аудио для сообщений и важных событий; громкость; тест; возврат стандартных WAV. |
| Оригинальные звуки | По выбору заглушается вся вкладка FunPay; при потере связи возвращается прежнее состояние. |
| Панель | Каталог, склад, заказы, автоответы, шаблоны, расписание, бонусы, подключения, журнал. |
| Автовыдача | Проверка оплаченного заказа и продавца; точное название + раздел; несколько записей на заказ; атомарный резерв. |
| Склад | Массовый ввод, шифрование записей, отклонение дубликатов, исключение свободных записей. |
| Автоответы | По словам, полному совпадению или любому входящему сообщению; пауза на чат и защита от повторов. |
| Поднятие | По умолчанию каждые 241 минуту; можно 240. Более долгий cooldown FunPay учитывается. |
| Автолистер | Публикация нового лота или изменение своего существующего; отложенная публикация; сохранённые тексты и поля. |
| Шаблоны в браузере | Сохранение формы редактирования лота; вставка шаблона без автоматического нажатия «Сохранить». |
| Бонус | Одно начисление часов после проверки отзыва в закрытом заказе; независимо от оценки. |
| Реальное продление | Подписанный webhook вашего сервиса с подтверждением применения; отдельно от внутреннего учёта. |
| Telegram | Уведомления владельцу, кнопки панели/установки, /status /pause /resume. |
| Защита доступа | Пароль, HttpOnly-сессия, CSRF, одноразовое сопряжение расширения, отзыв устройств. |
| Резервная копия | Согласованный JSON-экспорт таблиц с зашифрованным складом и настройками. |

**Граница проверки:** автоматизация реализована через неофициальный интерфейс FunPay. Проверены локальные сценарии и совместимость адаптера с зафиксированным исходником библиотеки. Живые заказы, отправка сообщений, публикация и поднятие на аккаунте продавца не проверялись: аккаунт не предоставлен. Изменения FunPay или блокировка запросов с хостинга могут потребовать обновления адаптера.

## 1. Один файл в GitHub

В GitHub нужен только **bot.py**. requirements.txt, render.yaml, папки app/web/extension загружать не нужно: собственный код и ресурсы встроены в bot.py. Зависимости устанавливаются Build Command. Секреты не вшивайте в файл.

Создайте приватный репозиторий → Add file → Upload files → загрузите bot.py в корень → Commit changes.

## 2. Бесплатный Render без диска + PostgreSQL

### Создать базу в Neon

1. Откройте https://neon.com и создайте проект на тарифе **Free**.
2. Откройте проект → **Connect**.
3. Выключите **Connection pooling**. Нужен прямой адрес без `-pooler` в имени хоста: приложение использует блокировку активного процесса на уровне соединения.
4. Скопируйте только строку подключения вида `postgresql://USER:PASSWORD@HOST/DB?sslmode=require`. Сохраняйте дополнительные параметры, если Neon их добавил. Не копируйте команду `psql`, кавычки вокруг URL и поясняющий текст.
5. Эта строка — секрет. Вставьте её в Render Environment как DATABASE_URL, не в GitHub и не в чат.

У Neon Free есть квоты на хранение, вычисления и трафик. Постоянный опрос не даёт вычислениям базы заснуть и расходует квоту. Проверьте Usage в Neon: бесплатная база не означает безлимитную круглосуточную работу. Не включайте платный план или автопереход, если не хотите платить.

### Создать сервис Render

Render → New → **Web Service** → выберите GitHub-репозиторий.

| Поле | Значение |
|---|---|
| Language | Python 3 |
| Root Directory | Пусто: bot.py находится в корне |
| Build Command | `python bot.py --install` |
| Start Command | `python bot.py` |
| Instance Type | **Free** |
| Health Check Path | `/healthz` |
| Disk | **Не добавлять** |

### Environment

| Переменная | Что указать |
|---|---|
| `PYTHON_VERSION` | `3.12.14` |
| `DATABASE_URL` | Прямая строка PostgreSQL из Neon, pooling выключен |
| `ADMIN_PASSWORD` | Уникальный случайный пароль от 16 символов |
| `MASTER_KEY` | Другой случайный секрет от 32 символов, неизменный после загрузки склада |
| `TELEGRAM_BOT_TOKEN` | Необязательно: токен бота от @BotFather |
| `TELEGRAM_OWNER_ID` | Необязательно: ваш числовой Telegram ID |
| `PUBLIC_URL` | Обычно не нужен; Render-адрес определяется автоматически. Для своего домена — полный HTTPS URL |
| `ACCESS_WEBHOOK_URL` | Необязательно: HTTPS-обработчик вашего сервиса доступа |
| `ACCESS_WEBHOOK_SECRET` | Вместе с webhook: секрет подписи |

PORT задаёт Render. DATA_DIR и постоянный диск на Render не нужны. При отсутствии DATABASE_URL сервис на Render откажется запускаться, чтобы не потерять склад в локальной SQLite.

Получить случайные ADMIN_PASSWORD и MASTER_KEY на своём компьютере: `python bot.py --secrets`. Эта команда не требует установки зависимостей. Сохраните MASTER_KEY отдельно: без него восстановить зашифрованный склад нельзя.

Нажмите Deploy Web Service → дождитесь Live → откройте адрес → войдите с ADMIN_PASSWORD.

### Ограничение бесплатного запуска

Render Free засыпает после 15 минут без входящих HTTP/WebSocket-запросов. Исходящий опрос FunPay и Telegram сам по себе не будит сервер. Во сне остановлены автовыдача, ответы, поднятие, бонусы и Telegram-команды. Открытие панели будит сервис, обычно не мгновенно. Новые заказы проверяются после возобновления; при большом перерыве сверяйте продажи вручную. Гарантии круглосуточной мгновенной работы здесь нет.

Этот проект не запускает искусственные запросы к самому себе. Для бесплатной работы без сна Render можно запустить bot.py на своём постоянно включённом компьютере (раздел 14). Если нужен постоянный облачный процесс, его тариф — отдельный вопрос; диск всё равно не нужен при PostgreSQL.

Бесплатная база Render Postgres имеет ограниченный срок: на дату подготовки — 30 дней. Поэтому для этого варианта используется отдельная PostgreSQL, например Neon, а не временная Render DB. Актуальные ограничения: https://render.com/docs/free и https://neon.com/pricing.

Сервис запускает один worker. PostgreSQL-блокировка не допускает одновременную работу двух копий автоматики при обновлении. Во время передачи управления новый процесс может кратковременно показывать ожидание.

## 3. Подключение FunPay

1. В браузере войдите в свой аккаунт FunPay.
2. Откройте F12 → **Application / Приложение → Storage / Хранилище → Cookies → https://funpay.com**.
3. Найдите cookie `golden_key` и скопируйте только её значение.
4. В собственной HTTPS-панели Studio откройте «Настройки».
5. Вставьте ключ в поле Golden key. User-Agent заполнится из браузера; если копируете ключ из другого браузера, укажите User-Agent того браузера.
6. Нажмите «Проверить и подключить». После успешной проверки появится имя продавца.

Ключ предоставляет доступ к аккаунту. Вводите его только в собственную панель. Не помещайте в GitHub, сообщения боту и скриншоты. Расширение не запрашивает право читать cookies и не извлекает ключ автоматически.

База закрепляется за первым подключённым FunPay ID. Для другого аккаунта нужна отдельная установка, чтобы не перепутать склад и покупателей.

## 4. Установка расширения

Поддерживается настольный Chrome 116+ и совместимые Chromium-браузеры с Manifest V3 и Offscreen API: Edge, Brave; Opera/Yandex зависят от версии. Firefox и мобильный Chrome этой сборкой не поддерживаются.

1. В панели откройте «Расширение» → «Скачать расширение». Расширение встроено в bot.py и формируется сервером при скачивании.
2. Распакуйте ZIP. Папку потом не удаляйте и не перемещайте.
3. Откройте:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
   - Brave: `brave://extensions`
4. Включите «Режим разработчика».
5. Нажмите «Загрузить распакованное расширение».
6. Выберите папку, **не ZIP**, непосредственно содержащую `manifest.json`.
7. В панели Studio → «Расширение» → «Получить код подключения».
8. Значок расширения → «Настроить фон и звуки».
9. Введите HTTPS-адрес панели и одноразовый код; нажмите «Подключить».
10. Разрешите доступ к указанному серверу, когда Chrome спросит.
11. Обновите открытые страницы FunPay. Справа снизу появится кнопка **Studio**.

Код действует 10 минут и используется один раз. Пароль администратора расширению не нужен. Устройство можно отключить в панели, отозвав его ключ. Ключ расширения позволяет читать события и работать с шаблонами, но не получать склад, резервную копию или менять настройки автоматики.

Автоматическая установка без действия пользователя невозможна. Ссылка из Telegram ведёт на `/install`, где есть кнопка скачивания и инструкция. Расширение не опубликовано в Chrome Web Store.

## 5. Товар и склад

1. Создайте первый лот в обычном интерфейсе FunPay.
2. В Studio → «Товары и склад» → «Мои лоты». Скопируйте точное название и **ID раздела**.
3. «Добавить товар»: внутреннее имя, название для сопоставления, ID раздела, текст выдачи.
4. В текст выдачи обязательно вставьте `{items}`.
5. Если продаёте доступ на срок, укажите основной срок в часах. Для одноразового ключа оставьте 0.
6. «Пополнить»: загрузите собственные данные. Форматы:

**Одна запись на строку:**

```text
your-login-1 : your-password-1
your-login-2 : your-password-2
```

**Записи разделены пустой строкой:**

```text
Логин: your-login-1
Пароль: your-password-1
Инструкция: ваши условия использования

Логин: your-login-2
Пароль: your-password-2
Инструкция: ваши условия использования
```

Это примеры структуры, не реальные аккаунты. Вторая форма сохраняет многострочную запись как один товар. Каждая запись закрепляется за одним заказом. Если куплены 3 штуки, резервируются сразу 3 записи; при нехватке не выдаётся часть заказа.

Лимит одной загрузки: 2000 записей / около 2 MB. Дубликаты определяются по всему складу, включая уже выданные записи. Из списка склада можно исключить свободную позицию. Пароли в интерфейсе списка и журнале скрыты.

Переменные текста: `{items}`, `{buyer}`, `{order_id}`, `{product}`, `{quantity}`. В конец выдачи добавляется «Выдача №…» для проверки после сетевой ошибки.

Не используйте для одного лота одновременно Studio и встроенную автовыдачу FunPay / другого бота. Studio пропускает заказ, в котором FunPay уже вернул свои выданные товары.

## 6. Автоответы и запуск

В «Автоответы» создайте правило:

- «Сообщение содержит»: одно из ключевых слов через запятую;
- «Полное совпадение»: совпадает весь текст без учёта регистра и лишних пробелов;
- «Любое входящее сообщение»: общее приветствие.

В ответе доступно `{buyer}`. Минимальная пауза — 60 секунд, по умолчанию 300 на один чат и правило. Срабатывает первое подходящее правило в списке хранения; на одно сообщение отправляется максимум один ответ. На свои и системные сообщения ответы не отправляются.

В «Обзор → Автоматизация» включите нужные функции. Затем нажмите общий **«Запустить»** вверху. «Пауза» останавливает серверную обработку FunPay; сохранённые товары и правила остаются. Уже начатый HTTP-запрос завершится. Обработчик внешнего провайдера и Telegram работают отдельно.

При первом старте найденные прежние заказы не выдаются автоматически. После проверки такого заказа используйте «Заказы → Проверить заказ». Если автовыдача включена, этот явный запуск может выдать товар. При дальнейших перезапусках уже выданные заказы не выдаются повторно.

## 7. Предложения и автолистер

### Из панели

1. «Предложения → Создать шаблон».
2. Укажите ID своего исходного лота → «Импортировать». Так сохраняются обязательные поля раздела, сервер, платформа и другие параметры.
3. Измените заголовок, описание, цену, количество.
4. **ID для изменения = 0:** создаётся новый лот. **Свой существующий ID:** изменяется этот лот.
5. Сохраните шаблон, нажмите «Опубликовать» и проверьте параметры.
6. При желании выберите дату/время. Время берётся из часового пояса браузера и сохраняется как абсолютный timestamp.
7. Включите общий запуск. Результат появится в очереди публикаций.

Кнопка «Опубликовать» отправляет лот в очередь один раз. Двойной сетевой повтор одного запроса не создаёт второе задание. При неоднозначной ошибке публикация остаётся на проверке и не повторяется автоматически, чтобы не создать дубликат. Проверьте лоты FunPay перед новым запуском. В этой версии очередь не клонирует объявления бесконечно каждые четыре часа: периодическое действие — именно поднятие, а публикация — по вашему заданию.

### На FunPay

Откройте редактирование предложения → кнопка **Studio** → «Сохранить форму как шаблон». В дальнейшем «Загрузить мои шаблоны» → выбрать → «Вставить в форму». Чужой раздел не подставляется. Текущий ID редактируемого лота не меняется. Отправка формы остаётся отдельным действием на FunPay.

## 8. Поднятие каждые 4 часа 1 минуту

1. «Автоподнятие → Мои лоты».
2. Найдите **ID категории**, а не ID раздела и не ID лота.
3. «Добавить категорию», введите ID и 241 минуту.
4. Включите функцию «Автоподнятие» и общий запуск.

Первая попытка после добавления — в ближайшем цикле. После успеха следующая назначается через 241 минуту. Можно выбрать 240. Если FunPay требует ждать дольше, используется срок сайта. Время хранится в базе и не сбрасывается при перезапуске.

## 9. Бонус за отзыв и подписки

В карточке товара задайте основной срок и бонусные часы. Обещание бонуса можно написать в описании предложения и сообщении выдачи. Включите «Бонусы за отзывы».

Studio проверяет **реальный отзыв в закрытом заказе**, а не фразу покупателя в чате. Начисление не зависит от оценки, выполняется один раз. Проверка идёт очередью: до 5 подходящих заказов примерно раз в 90 секунд; на большом каталоге ожидание будет дольше.

Без внешнего провайдера меняется только срок в панели. **Это не продлевает подписку ChatGPT Plus/Pro, Steam или другого стороннего сервиса.** Для реального продления нужен механизм управления доступом у вашего поставщика. Если вы управляете собственным сервисом, подключите webhook по `docs/ACCESS_WEBHOOK.md`.

Панель показывает два разных факта: «бонус учтён» и «провайдер подтвердил применение». Ответ HTTP 200 без `{ "applied": true, "event_id": "..." }` не считается подтверждением. При возврате товара данные не возвращаются на склад. Отзыв доступа после возврата требует проверки владельцем.

## 10. Telegram

1. В Telegram откройте официального **@BotFather**, выполните `/newbot`, задайте имя и username.
2. Токен поместите в `TELEGRAM_BOT_TOKEN` в Render Environment.
3. Получите собственный числовой user ID из доверенного средства Telegram. Либо отправьте своему боту сообщение и локально запросите официальный Bot API `getUpdates`; поле `message.from.id` — ваш ID. Не используйте сторонний сайт с токеном.
4. Укажите ID в `TELEGRAM_OWNER_ID`.
5. Сохраните переменные и дождитесь перезапуска.
6. Отправьте своему боту `/start`.

| Команда | Действие |
|---|---|
| `/start`, `/panel` | Панель и страница установки. |
| `/install` | Кнопки, включая установку расширения. |
| `/status` | Включена ли автоматизация, количество товара. |
| `/pause` | Остановить обработку FunPay. |
| `/resume` | Возобновить с сохранёнными правилами. |

Бот опрашивает Telegram Bot API, публичный Telegram webhook не нужен. Используйте отдельного бота без установленного webhook и без второго обработчика getUpdates. Управление и уведомления доступны только указанному владельцу в личном чате. Учётные данные товара в Telegram не отправляются. При сетевой неопределённости уведомление может быть пропущено, но заказ остаётся в журнале Studio.

Discord-бот в эту сборку не входит: упомянутый переход «как в Discord» реализован кнопкой со страницей установки.

## 11. Фон и звуки

Настройки расширения → включите изменение фона → загрузите PNG/JPEG/WebP до 4 MB → цвет и затемнение → «Сохранить».

Загрузите аудио до 2 MB для сообщения и заказа; браузер должен поддерживать формат. Выберите громкость, включите уведомления. «Проверить звук» сначала сохраняет настройки. При необходимости разрешите уведомления Chrome в настройках ОС.

Опция отключения оригинального звука глушит **всю вкладку** FunPay. Собственный звук воспроизводится из документа расширения. При обнаруженной потере соединения с сервером прежнее состояние звука восстанавливается. Перед удалением расширения выключите эту опцию; после принудительного удаления Chrome может оставить вкладку заглушённой — включите звук через контекстное меню вкладки.

С открытым FunPay расширение проверяет события примерно раз в 5 секунд, сервер по умолчанию — раз в 8 секунд. Это не мгновенный push. Без вкладки работает минутный alarm, пока браузер открыт; ограничения фонового режима могут увеличить задержку. Закрытый браузер не воспроизводит звуки, но Render продолжает выдачу и Telegram-уведомления.

## 12. Неоднозначная отправка / восстановление

Если запрос отправки завершился тайм-аутом, нельзя достоверно узнать, ушло ли сообщение. Studio ставит «Нужна проверка», оставляет ту же запись в резерве и **не выдаёт новую**.

«Заказы → Проверить отправку» ищет исходный текст в доступной истории. Если найден — выдача подтверждается. Если нет — проверьте чат вручную. Кнопка повторной отправки отправляет **ту же запись**. Отсутствие в короткой доступной истории не доказывает, что сообщения никогда не было.

После падения процесса незавершённые отправки и публикации также переходят в состояние проверки. Заказ с недостаточным складом автоматически повторно проверяется после пополнения, пока включена автоматизация.

## 13. Резервные копии и переезд

Панель → Настройки → Скачать резервную копию. Получится JSON с таблицами; записи склада и golden_key остаются зашифрованными. Копия содержит служебные данные доступа, храните её приватно, а MASTER_KEY — отдельно.

Для восстановления на компьютере с установленными зависимостями задайте DATABASE_URL **новой пустой базы**, исходный MASTER_KEY и ADMIN_PASSWORD, затем выполните:

```bash
python bot.py --restore funpay-studio-backup.json
```

Скрипт не перезаписывает непустую базу. Автоматизация после восстановления выключена; старые сессии и ключи расширений не восстанавливаются. Переподключите браузеры. Незавершённые внешние задания требуют ручной сверки. Старые выданные записи не возвращаются в свободный склад.

Для переноса SQLite из предыдущей версии в пустую PostgreSQL:

```bash
python bot.py --import-sqlite studio.sqlite3
```

Остановите старого бота перед копированием базы. Используйте полноценную online backup из прежней панели или остановленную базу вместе с её WAL. Не копируйте один основной SQLite-файл во время записи. При переносе нужен исходный MASTER_KEY. Перед включением сверяйте продажи, появившиеся после даты копии.

После восстановления замените DATABASE_URL сервиса Render новой строкой подключения. Не запускайте старую и восстановленную базы одновременно для одного аккаунта.

Обновление программы: замените bot.py в GitHub → Commit → Render deploy. DATABASE_URL и MASTER_KEY сохранить. Рабочие данные находятся в PostgreSQL и переживают пересборку. Архив FunPayAPI скачивается только на этапе --install, commit и SHA-256 зафиксированы.

## 14. Бесплатный локальный запуск

На компьютере установите Python 3.12. Положите bot.py в отдельную папку. Откройте терминал в этой папке:

```bash
python bot.py --install
python bot.py --init-local
python bot.py
```

--init-local создаёт .env с двумя случайными секретами и не перезаписывает существующий файл. Пароль входа указан внутри .env в ADMIN_PASSWORD. Откройте http://127.0.0.1:8000. Не загружайте .env или папку data в GitHub.

Без DATABASE_URL используется SQLite в папке data рядом с файлом. На своём компьютере платить за отдельный диск не нужно. Для PostgreSQL добавьте DATABASE_URL в .env. Локальные секреты и база — данные программы, дополнительные исходные файлы для запуска не требуются.

Бот работает, пока компьютер включён, не спит и запущен процесс. Ссылка localhost из Telegram доступна только на том же компьютере. Если управляете с телефона, используйте HTTPS-адрес Render и PostgreSQL.

Папка .funpay_studio_runtime появляется при установке: это загруженная зависимость FunPayAPI, не отдельная часть исходного проекта. Удалять её до запуска нельзя; восстановить можно повторным --install.

## 15. Проверка и помощь

```bash
python bot.py --help
python bot.py --guide
python bot.py --check-db
```

--check-db подключается к выбранной базе и создаёт необходимые таблицы; реальные сделки не выполняет. Полная инструкция встроена в bot.py и доступна на странице /help. Установщик расширения — /install.

Локальные тесты проверяют выдачу, склад, права доступа и адаптер. Отдельно проверена PostgreSQL-ветка через локальный PGlite и psycopg. Реальный аккаунт FunPay, Neon, Render и Telegram не предоставлялись: реальные операции на этих аккаунтах не проверены. Сначала проверьте один собственный товар и один заказ.

## 16. Частые проблемы

| Проблема | Что проверить |
|---|---|
| Render не находит bot.py | Файл должен называться bot.py и лежать в корне репозитория; Root Directory пусто. |
| Ошибка MASTER_KEY | Не изменился ли ключ относительно существующей базы? Не удаляйте склад для исправления. |
| Сервис засыпает | Ограничение Render Free. Во сне бот не работает. Открытие панели возобновляет работу. |
| Пустой склад после переезда | Проверьте DATABASE_URL и выбранную базу. Старый склад не переносится без экспорта/импорта. |
| Ошибка pooling | В Neon → Connect выключите Connection pooling, скопируйте прямой URL без -pooler. |
| Исчерпана квота Neon | Посмотрите Usage; бесплатный план ограничен. Временно используйте локальный запуск/восстановление копии. |
| Golden key не подходит | Войдите заново в свой FunPay и проверьте актуальный cookie; не перешлите его другому человеку. |
| 403 / CAPTCHA / временный отказ FunPay | Приостановите автоматику, проверьте доступ в своём обычном браузере и требования FunPay. Защита сайта не обходится. |
| Заказ не выдаётся | Общий запуск, переключатель автовыдачи, точное название, ID раздела, статус PAID, склад, отсутствие встроенной выдачи. |
| Бонус виден, подписка не продлилась | Без интеграции провайдера это только учёт. Посмотрите статус задания webhook. |
| Публикация на паузе | Включите общий запуск; проверьте дату и обязательные поля исходного лота. |
| «Нужна проверка» у публикации | Сначала проверьте список предложений FunPay; не нажимайте повторно вслепую. |
| Расширение не ставится | Выбирается распакованная папка с manifest.json, а не архив; нужен совместимый настольный Chromium. |
| Не появились кнопки на FunPay | Обновите страницу после установки. Инструменты шаблонов доступны в форме редактирования лота. |
| Нет звука | Проверьте звук в настройках, формат файла, громкость и разрешения уведомлений ОС; браузер должен работать. |
| Telegram Conflict / нет команд | У отдельного бота не должно быть другого getUpdates-процесса или webhook. Проверьте токен и OWNER_ID. |

## Источники интеграций

Источники; ограничения бесплатных тарифов проверены 23 октября 2026 года:

- FunPayAPI / исходники текущего адаптера: https://github.com/sidor0912/FunPayCardinal/tree/c09dd13be25072bb3d5cdbba33d1e0d70e477710/FunPayAPI
- Исходный проект FunPayAPI: https://pypi.org/project/FunPayAPI/
- Правила FunPay: https://funpay.com/trade/info
- Render FastAPI: https://render.com/docs/deploy-fastapi
- Neon Free: https://neon.com/pricing
- Neon connection string: https://neon.com/docs/connect/connect-from-any-app
- Psycopg: https://www.psycopg.org/psycopg3/docs/
- Ограничения бесплатного Render: https://render.com/docs/free
- Chrome Offscreen API: https://developer.chrome.com/docs/extensions/reference/api/offscreen
- Telegram Bot API: https://core.telegram.org/bots/api

Используйте принадлежащие вам товары и доступы, которые вы вправе предоставлять. Условия FunPay и поставщика услуги действуют независимо от возможностей этого инструмента. Не добавляйте бонусные условия, требующие исключительно положительной оценки или изменения негативного отзыва.

## Сторонние компоненты

Зависимость FunPayAPI скачивается при --install из https://github.com/sidor0912/FunPayCardinal по commit c09dd13be25072bb3d5cdbba33d1e0d70e477710. Исходный проект FunPayAPI опубликован под GPL-3.0: https://github.com/LIMBODS/FunPayAPI . Сторонний исходник не встроен в bot.py. При распространении собранных зависимостей соблюдайте их лицензии.
'''


# Embedded web resources
WEB_ASSETS = {
    'app.js': r''''use strict';
const $ = (s, root=document) => root.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const id = () => {
 if (crypto.randomUUID) return crypto.randomUUID();
 const bytes = crypto.getRandomValues(new Uint8Array(16));
 bytes[6] = (bytes[6] & 15) | 64;
 bytes[8] = (bytes[8] & 63) | 128;
 const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
 return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
};
const state = {csrf:'', page:'overview', status:null, products:[], rules:[], templates:[], orders:[], jobs:[], events:[], boosts:[]};
const labels = {overview:'Обзор',products:'Товары и склад',orders:'Заказы',rules:'Автоответы',offers:'Предложения',boosts:'Автоподнятие',bonuses:'Бонусы за отзывы',extension:'Расширение',settings:'Настройки',events:'Журнал событий'};
const paths = {overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',products:'m3 7 9-4 9 4v11l-9 4-9-4z M3 7l9 4 9-4 M12 11v11',orders:'M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6',rules:'M3 4h18v13H9l-6 4z M7 8h10 M7 12h7',offers:'M4 4h16v16H4z M8 8h8 M8 12h8 M8 16h5',boosts:'m5 12 7-8 7 8 M12 4v17',bonuses:'M3 8h18v4H3z M5 12v9h14v-9 M12 8v13 M12 8C4 8 5 0 9 3l3 5z M12 8c8 0 7-8 3-5z',extension:'M8 3h8v5h5v8h-5v5H8v-5H3V8h5z',settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',events:'M5 3h14v18H5z M8 7h8 M8 11h8 M8 15h6'};
const statusNames={delivered:'Выдан',uncertain:'Нужна проверка',out_of_stock:'Нет товара',pending:'Ожидает',sending:'Отправляется',refunded:'Возврат',done:'Выполнено',running:'Выполняется',cancelled:'Отменено',disabled:'Убрано',available:'Свободно',reserved:'Резерв'};
const date = n => n ? new Date(n*1000).toLocaleString('ru-RU',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}) : '—';
const badge = (text, kind='') => `<span class="badge ${kind}">${esc(text)}</span>`;
const empty = (title,text,button='') => `<div class="empty"><div class="empty-symbol">◇</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${button}</div>`;
const head = (title,subtitle,actions='') => `<div class="page-head"><div><h1>${title}</h1><p class="muted">${subtitle}</p></div><div class="actions">${actions}</div></div>`;
const btn = (action,text,cls='',extra='') => `<button data-action="${action}" class="${cls}" ${extra}>${text}</button>`;
const field = (name,label,value='',type='text',extra='') => `<label>${label}<input name="${name}" type="${type}" value="${esc(value)}" ${extra}></label>`;
const area = (name,label,value='',extra='') => `<label>${label}<textarea name="${name}" ${extra}>${esc(value)}</textarea></label>`;
const check = (name,label,value=true) => `<label class="check"><input type="checkbox" name="${name}" ${value?'checked':''}>${label}</label>`;
let toastTimer;
function toast(text,error=false){const n=$('#toast');n.textContent=text;n.className='toast visible'+(error?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>n.classList.remove('visible'),7000);}
async function api(path,method='GET',body){
 const opts={method,headers:{},credentials:'same-origin'};
 if(body!==undefined){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
 if(method!=='GET')opts.headers['X-CSRF-Token']=state.csrf;
 const response=await fetch('/api'+path,opts);let data;
 try{data=await response.json();}catch{throw new Error('Сервер недоступен. Проверьте Render и соединение.');}
 if(!response.ok){if(response.status===401&&path!='/login')showLogin();const detail=typeof data.detail==='string'?data.detail: data.detail?.map(x=>x.msg).join('; ');throw new Error(detail||'Операция не выполнена.');}
 return data;
}
function showLogin(){$('#login').classList.remove('hidden');$('#shell').classList.add('hidden');}
function showShell(){$('#login').classList.add('hidden');$('#shell').classList.remove('hidden');}
function header(){const s=state.status;$('#connection').className='badge '+(s.connected?'good':'');$('#connection').textContent=s.connected?'● '+(s.account?.username||'Подключено'):'○ Нет связи с FunPay';$('#global-toggle').disabled=s.worker_ready===false;$('#global-toggle').title=s.worker_ready===false?'Ожидание подключения к базе или завершения обновления':'';$('#global-toggle').textContent=s.settings.enabled?'Пауза':'Запустить';$('#global-toggle').className='small-button '+(s.settings.enabled?'':'primary');}
function switchRow(key,title,desc){return `<div class="switch-line"><div><h3>${title}</h3><p>${desc}</p></div><input class="switch" type="checkbox" data-setting="${key}" aria-label="${title}" ${state.status.settings[key]?'checked':''}></div>`;}
function eventList(rows){return rows.length?rows.map(e=>`<div class="event"><span class="event-dot">${e.kind==='delivery'?'✓':e.kind==='attention'?'!':'↗'}</span><div><p>${esc(e.title)}</p><small>${esc(e.detail)}</small></div><time class="event-time">${date(e.at)}</time></div>`).join(''):empty('Здесь появятся события','Выдачи, сообщения и поднятия предложений сохраняются автоматически.');}
async function recentEvents(){const latest=await api('/events?after=-1');return (await api('/events?after='+Math.max(0,latest.cursor-100))).events.reverse();}
async function render(){
 const page=location.hash.slice(1)||'overview';state.page=labels[page]?page:'overview';
 $('#crumb').textContent=labels[state.page];$('#nav').innerHTML=Object.keys(labels).map(k=>`<a href="#${k}" class="${k===state.page?'active':''}"><svg viewBox="0 0 24 24"><path d="${paths[k]}"/></svg>${labels[k]}</a>`).join('');
 try{state.status=await api('/status');header();const s=state.status;let html='';
 if(state.page==='overview'){
  state.events=await recentEvents();
  html=`<section class="hero"><div class="hero-text"><p class="eyebrow">SELLER CONTROL CENTER</p><h1>Магазин под контролем.</h1><p>Заказы, товары и сообщения — в одном месте.<br>Настройте правила, остальное возьмёт на себя Studio.</p><div class="actions"><a href="#products">${btn('add-product','+ Добавить товар','primary')}</a><a href="#extension">${btn('go-extension','Подключить расширение')}</a></div></div><div class="hero-art" aria-hidden="true"><div class="orb">↗</div><div class="hero-chip">ВАШ РАБОЧИЙ РИТМ</div></div></section>`;
  html+=`<div class="stats">${[['Выдано заказов',s.counts.delivered,'За всё время','✓'],['Свободно на складе',s.counts.stock,'Готовы к выдаче','▧'],['Требуют внимания',s.counts.attention,'Ожидают вашего решения','◷'],['Товаров',s.counts.products,'В каталоге Studio','◇']].map(([l,v,d,i])=>`<div class="stat"><div class="stat-label">${l}<span class="spark">${i}</span></div><strong>${v}</strong><small>${d}</small></div>`).join('')}</div>`;
  html+=`<div class="grid-2"><section class="card"><div class="card-head"><h2>Готовность магазина</h2>${badge(s.settings.enabled?'Автоматизация включена':'На паузе',s.settings.enabled?'good':'warn')}</div>${[['settings','Подключите FunPay','Проверьте подключение к своему аккаунту.',s.has_key],['products','Добавьте товар и склад','Точное совпадение названия и раздела.',s.counts.stock>0],['extension','Настройте рабочее место','Свой фон, звуки и быстрый доступ.',false]].map(([k,t,d,done],i)=>`<a href="#${k}" class="setup-item ${done?'done':''}"><span class="step-num">${done?'✓':String(i+1).padStart(2,'0')}</span><div><h3>${t}</h3><p>${d}</p></div><span class="arrow">↗</span></a>`).join('')}</section><section class="card"><div class="card-head"><h2>Автоматизация</h2><a class="text-link" href="#settings">Настроить ↗</a></div>${switchRow('auto_delivery','Автовыдача','Только после проверки оплаченного заказа')}${switchRow('auto_reply','Автоответы','Ваши тексты и паузы между ответами')}${switchRow('auto_raise','Автоподнятие','По расписанию и ограничениям FunPay')}${switchRow('review_bonus','Бонусы за отзывы','Однократное начисление')}</section></div><section class="card"><div class="card-head"><h2>Последние события</h2><a href="#events" class="text-link">Весь журнал ↗</a></div>${eventList(state.events.slice(0,5))}</section>`;
 } else if(state.page==='products'){
  state.products=await api('/products');html=head('Товары и склад','Добавьте записи для выдачи и свяжите их с предложениями FunPay.',btn('my-lots','Мои лоты')+btn('add-product','+ Добавить товар','primary'));
  html+=state.products.length?`<div class="product-grid">${state.products.map(p=>`<section class="product-card"><div class="product-top"><span class="product-icon">${esc(p.name.slice(0,2).toUpperCase())}</span>${badge(p.enabled?'Активен':'Выключен',p.enabled?'good':'')}</div><h2>${esc(p.name)}</h2><p class="muted">${esc(p.match_title)}<br>Раздел #${p.subcategory_id}${p.access_hours?' · '+p.access_hours+' ч. доступа':''}</p><div class="stock-count"><span>Готово к выдаче</span><b>${p.stock.available||0} шт.</b></div><div class="actions">${btn('stock','+ Пополнить','primary small-button',`data-id="${p.id}"`)}${btn('edit-product','Изменить','small-button',`data-id="${p.id}"`)}</div><div style="margin-top:14px">${btn('stock-list','Посмотреть позиции','link-button tiny',`data-id="${p.id}"`)}</div></section>`).join('')}</div>`:empty('Добавьте первый товар','Каждая запись выдаётся одному заказу. Дубликаты при загрузке пропускаются.',btn('add-product','+ Добавить товар','primary'));
 } else if(state.page==='orders'){
  state.orders=await api('/orders');html=head('Заказы','Статусы выдачи, сроки доступа и заказы, которым нужна помощь.',btn('process-order','Проверить заказ'))+`<div class="searchbar"><input id="order-search" aria-label="Поиск заказа" placeholder="Номер заказа или покупатель"><select id="order-filter" aria-label="Статус"><option value="">Все статусы</option><option value="delivered">Выданные</option><option value="uncertain">Нужна проверка</option><option value="out_of_stock">Нет товара</option><option value="pending">Ожидают</option><option value="refunded">Возвраты</option></select></div><div class="card" id="orders-table">${ordersTable(state.orders)}</div>`;
 } else if(state.page==='rules'){
  state.rules=await api('/rules');html=head('Автоответы','Ответы на входящие сообщения. Одно правило за раз, с защитой от повторов.',btn('add-rule','+ Добавить правило','primary'));
  html+=`<section class="card">${state.rules.length?state.rules.map(r=>`<div class="rule-row"><div class="rule-content"><h3>${esc(r.name)} ${badge(r.enabled?'Включено':'Пауза',r.enabled?'good':'')}</h3><p>${esc(r.reply)}</p><div class="rule-meta">${esc(r.mode==='any'?'Любое сообщение':r.keywords.join(', '))} · пауза ${r.cooldown_seconds} сек.</div></div><div class="actions">${btn('edit-rule','Изменить','small-button',`data-id="${r.id}"`)}${btn('delete-rule','Удалить','quiet small-button',`data-id="${r.id}"`)}</div></div>`).join(''):empty('Ответы вашим голосом','Создайте приветствие, инструкцию или ответ на частый вопрос.',btn('add-rule','Создать правило','primary'))}</section>`;
 } else if(state.page==='offers'){
  [state.templates,state.jobs]=await Promise.all([api('/templates'),api('/jobs')]);html=head('Предложения','Сохранённые тексты, создание лотов и отложенная публикация.',btn('add-offer','+ Создать шаблон','primary'));
  html+=`<div class="notice">Импортируйте свой существующий лот или сохраните форму через расширение. Так сохранятся обязательные поля именно вашего раздела.</div><section class="card">${state.templates.length?state.templates.map(t=>`<div class="rule-row"><div class="rule-content"><h3>${esc(t.name)}</h3><p>${esc(t.title)}</p><div class="rule-meta">${esc(t.price)} · количество ${t.amount} · ${t.target_id?'изменение лота #'+t.target_id:'новый лот'}</div></div><div class="actions">${btn('edit-offer','Изменить','small-button',`data-id="${t.id}"`)}${btn('publish','Опубликовать','primary small-button',`data-id="${t.id}"`)}</div></div>`).join(''):empty('Ваши тексты всегда под рукой','Шаблон можно отредактировать, вставить на FunPay или отправить на публикацию.',btn('add-offer','Создать шаблон','primary'))}</section><h2 class="section-title">Очередь публикаций</h2><section class="card">${jobsTable(state.jobs.filter(j=>j.kind==='publish'))}</section>`;
 } else if(state.page==='boosts'){
  state.boosts=await api('/boosts');html=head('Автоподнятие','Стандартный интервал — 4 часа 1 минута. Время ожидания FunPay учитывается.',btn('my-lots','Мои лоты')+btn('add-boost','+ Добавить категорию','primary'));
  html+=`<section class="card">${state.boosts.length?`<div class="table-wrap"><table><thead><tr><th>Категория</th><th>Интервал</th><th>Следующая попытка</th><th>Статус</th><th></th></tr></thead><tbody>${state.boosts.map(b=>`<tr><td>${esc(b.name)}<small>ID категории: ${b.category_id}</small></td><td>${Math.floor(b.interval_minutes/60)} ч. ${b.interval_minutes%60} мин.</td><td>${date(b.next_run)}</td><td>${badge(b.enabled?'Включено':'Пауза',b.enabled?'good':'')}</td><td>${btn('edit-boost','Изменить','small-button',`data-id="${b.category_id}"`)} ${btn('delete-boost','×','quiet',`data-id="${b.category_id}"`)}</td></tr>`).join('')}</tbody></table></div>`:empty('Поднятие по расписанию','Добавьте категорию из своих лотов. ID категории отличается от ID раздела.',btn('add-boost','Добавить категорию','primary'))}</section>`;
 } else if(state.page==='bonuses'){
  [state.orders,state.jobs]=await Promise.all([api('/orders'),api('/jobs')]);html=head('Бонусы за отзывы','Дополнительное время начисляется один раз на проверенный заказ.');
  html+=`<div class="notice ${s.provider?'':'amber'}">${s.provider?'Подключён обработчик доступа. Применение подтверждается отдельным статусом задания.':'Сейчас ведётся только учёт срока. Для реального продления подключите ACCESS_WEBHOOK_URL и ACCESS_WEBHOOK_SECRET на сервере.'} Бонус не зависит от оценки отзыва.</div><section class="card"><div class="card-head"><h2>Сроки доступа</h2><a href="#products" class="text-link">Настроить часы в товарах ↗</a></div>${state.orders.some(o=>o.expiry)?`<div class="table-wrap"><table><thead><tr><th>Заказ</th><th>Покупатель</th><th>Доступ до</th><th>Бонус</th></tr></thead><tbody>${state.orders.filter(o=>o.expiry).map(o=>`<tr><td>#${esc(o.id)}</td><td>${esc(o.buyer)}</td><td>${date(o.expiry)}</td><td>${o.review_applied?badge('+'+o.bonus_hours+' ч.','good'):badge('Ожидается отзыв')}</td></tr>`).join('')}</tbody></table></div>`:empty('Пока нет сроков доступа','Укажите срок и бонусные часы в карточке товара. Учёт начинается после выдачи.')}</section><section class="card"><h2>Применение у провайдера</h2>${jobsTable(state.jobs.filter(j=>j.kind==='provider'))}</section>`;
 } else if(state.page==='extension'){
  const devices=await api('/devices');html=head('Ваше рабочее место','Фон, свои звуки и быстрый доступ прямо на FunPay.',`<a class="primary-link" href="/download/extension.zip">Скачать расширение ↓</a>`);
  html+=`<div class="grid-2"><section class="card"><h2>Подключите браузер</h2><p class="muted">Скачайте и распакуйте расширение. Загрузите его на странице <code>chrome://extensions</code> в режиме разработчика.</p><p class="muted">В настройках расширения укажите адрес этой панели: <code>${esc(s.public_url)}</code> и одноразовый код ниже.</p>${btn('pair-code','Получить код подключения','primary')}<div id="pair-result" style="margin-top:20px"></div><p style="margin-top:22px"><a class="text-link" href="/install" target="_blank" rel="noopener">Полная инструкция установки ↗</a></p></section><section class="card"><h2>Что настраивается</h2>${[['Фон FunPay','Своя картинка, затемнение и цвет акцента.'],['Уведомления','Свои аудиофайлы для заказов и сообщений.'],['Предложения','Сохранить форму как шаблон и вставить её позже.']].map(([t,d])=>`<div class="setup-item"><span class="step-num">✓</span><div><h3>${t}</h3><p>${d}</p></div></div>`).join('')}</section></div><section class="card"><h2>Подключённые устройства</h2>${devices.length?devices.map(d=>`<div class="rule-row"><div><h3>${esc(d.name)}</h3><small>${date(d.created)}</small></div>${btn('revoke-device','Отключить','small-button danger',`data-id="${d.id}"`)}</div>`).join(''):empty('Браузеры ещё не подключены','Код действует 10 минут. Он не заменяет пароль панели.')}</section>`;
 } else if(state.page==='settings'){
  html=head('Настройки','Подключения и правила работы вашего магазина.')+`<div class="grid-2"><section class="card"><h2>Аккаунт FunPay</h2><p class="muted tiny">Введите golden_key из Cookies своего аккаунта. Ключ сохраняется зашифрованным; расширение не читает ваши cookies.</p><form id="connect-form">${field('golden_key','Golden key','','password','autocomplete="off" required minlength="16"')}${field('user_agent','User-Agent браузера',navigator.userAgent)}<button class="primary">Проверить и подключить</button></form></section><section class="card"><h2>Автоматизация</h2>${switchRow('auto_delivery','Автовыдача','Выдавать свободные записи после оплаты')}${switchRow('auto_reply','Автоответы','Применять ваши правила входящих сообщений')}${switchRow('auto_raise','Автоподнятие','Поднимать категории по расписанию')}${switchRow('review_bonus','Бонусы','Проверять отзывы и начислять часы')}<label style="margin-top:15px">Опрос FunPay, секунд<input id="poll-seconds" type="number" min="6" max="60" value="${s.settings.poll_seconds}"></label>${btn('save-poll','Сохранить интервал','small-button')}</section></div><div class="grid-2"><section class="card"><h2>Telegram</h2>${badge(s.telegram?'Настроен на сервере':'Не подключён',s.telegram?'good':'')}<p class="muted tiny" style="margin-top:14px">В Render Environment задайте <code>TELEGRAM_BOT_TOKEN</code> и <code>TELEGRAM_OWNER_ID</code>. Отправьте своему боту /start. Управление доступно только указанному владельцу в личном чате.</p><p class="muted tiny">Команды: /status · /panel · /install · /pause · /resume</p></section><section class="card"><h2>Резервная копия</h2><p class="muted tiny">Копия включает зашифрованный склад, заказы и настройки. Сохраните MASTER_KEY отдельно: без него восстановление невозможно.</p><a class="primary-link" href="/api/backup">Скачать резервную копию ↓</a><p class="tiny muted">Хранилище: ${s.storage==='postgresql'?'PostgreSQL — диск Render не нужен':'SQLite на этом компьютере'}. <a href="/help">Инструкция</a></p></section></div>`;
 } else if(state.page==='events'){
  state.events=await recentEvents();html=head('Журнал событий','Последние 100 событий. Логины и пароли товаров в журнал не записываются.',btn('refresh','Обновить'))+`<section class="card">${eventList(state.events)}</section>`;
 }
 $('#main').innerHTML=html;bindPage();
 }catch(e){toast(e.message,true);}
}
function ordersTable(rows){return rows.length?`<div class="table-wrap"><table><thead><tr><th>Заказ</th><th>Покупатель</th><th>Кол-во</th><th>Статус</th><th>Дата</th><th></th></tr></thead><tbody>${rows.map(o=>`<tr><td><a class="table-link" target="_blank" rel="noopener" href="https://funpay.com/orders/${encodeURIComponent(o.id)}/">#${esc(o.id)} ↗</a></td><td>${esc(o.buyer)}</td><td>${o.quantity}</td><td>${badge(statusNames[o.status]||o.status,o.status==='delivered'?'good':o.status==='uncertain'||o.status==='out_of_stock'?'warn':'')}</td><td>${date(o.created)}</td><td>${o.status==='uncertain'?btn('reconcile','Проверить отправку','small-button',`data-id="${esc(o.id)}"`):''}</td></tr>`).join('')}</tbody></table></div>`:empty('Заказов пока нет','Новые подходящие заказы появятся после подключения. Прежний заказ можно проверить по номеру.');}
function jobsTable(rows){return rows.length?`<div class="table-wrap"><table><thead><tr><th>Задание</th><th>Статус</th><th>Время</th><th>Результат</th><th></th></tr></thead><tbody>${rows.map(j=>`<tr><td>${esc(j.id.slice(0,28))}</td><td>${badge(statusNames[j.status]||j.status,j.status==='done'?'good':j.status==='uncertain'?'warn':'')}</td><td>${date(j.next_run)}</td><td>${esc(j.result||'—')}</td><td>${j.kind==='publish'&&j.status==='pending'?btn('cancel-job','Отменить','quiet small-button',`data-id="${esc(j.id)}"`):''}</td></tr>`).join('')}</tbody></table></div>`:empty('Очередь пуста','Здесь будет виден фактический результат выполнения заданий.');}
function bindPage(){
 $('#main').querySelectorAll('[data-setting]').forEach(n=>n.onchange=async()=>{try{await api('/settings','PUT',{...state.status.settings,[n.dataset.setting]:n.checked});state.status.settings[n.dataset.setting]=n.checked;toast('Настройка сохранена.');}catch(e){n.checked=!n.checked;toast(e.message,true);}});
 $('#order-search')?.addEventListener('input',filterOrders);$('#order-filter')?.addEventListener('change',filterOrders);
 $('#connect-form')?.addEventListener('submit',async e=>{e.preventDefault();const form=e.currentTarget;const b=$('button',form);b.disabled=true;try{const d=Object.fromEntries(new FormData(form));const result=await api('/connect','POST',d);form.reset();toast('Подключён аккаунт '+result.username);await render();}catch(err){toast(err.message,true);}finally{b.disabled=false;}});
}
function filterOrders(){const q=$('#order-search').value.toLowerCase();const filter=$('#order-filter').value;$('#orders-table').innerHTML=ordersTable(state.orders.filter(o=>(!filter||o.status===filter)&&(o.id+' '+o.buyer).toLowerCase().includes(q)));}
function modal(title,body,save,button='Сохранить'){
 $('#dialog-title').textContent=title;$('#dialog-body').innerHTML=body;$('#dialog-submit').textContent=button;$('#dialog-submit').hidden=!save;$('#dialog').showModal();
 $('#dialog-form').onsubmit=async e=>{e.preventDefault();if(!save)return;const b=$('#dialog-submit');b.disabled=true;try{await save(new FormData(e.currentTarget),e.currentTarget);$('#dialog').close();await render();}catch(err){toast(err.message,true);}finally{b.disabled=false;}};
}
function productModal(p={}){
 modal(p.id?'Изменить товар':'Новый товар',`<div class="grid-form"><div class="span-2">${field('name','Название в вашей панели',p.name||'','text','required maxlength="150"')}</div><div class="span-2">${field('match_title','Точное название оплаченного лота',p.match_title||'','text','required')}<p class="muted tiny">Скопируйте из «Мои лоты». Сопоставление учитывает название и раздел.</p></div>${field('subcategory_id','ID раздела (node)',p.subcategory_id||'','number','required min="1"')}${field('access_hours','Основной срок доступа, ч.',p.access_hours||0,'number','min="0" max="87600"')}${field('bonus_hours','Бонус за отзыв, ч.',p.bonus_hours||0,'number','min="0" max="8760"')}<div>${check('enabled','Правило выдачи включено',p.enabled??true)}</div><div class="span-2">${area('delivery_text','Сообщение покупателю',p.delivery_text||'Спасибо за заказ, {buyer}!\n\n{items}','required')}<p class="muted tiny">Переменные: {items}, {buyer}, {order_id}, {product}, {quantity}. {items} обязательно.</p></div></div>`,async f=>{await api('/products','POST',{id:p.id||id(),name:f.get('name'),match_title:f.get('match_title'),subcategory_id:+f.get('subcategory_id'),access_hours:+f.get('access_hours'),bonus_hours:+f.get('bonus_hours'),delivery_text:f.get('delivery_text'),enabled:f.has('enabled')});toast('Товар сохранён.');});
}
function ruleModal(r={}){
 modal('Правило автоответа',field('name','Название правила',r.name||'','text','required')+`<label>Срабатывание<select name="mode">${[['contains','Сообщение содержит'],['exact','Полное совпадение'],['any','Любое входящее сообщение']].map(([v,l])=>`<option value="${v}" ${r.mode===v?'selected':''}>${l}</option>`).join('')}</select></label>`+field('keywords','Ключевые слова, через запятую',(r.keywords||[]).join(', '))+area('reply','Ваш ответ',r.reply||'','required')+field('cooldown_seconds','Пауза в одном чате, секунд',r.cooldown_seconds||300,'number','min="60" max="86400" required')+check('enabled','Включено',r.enabled??true),async f=>{const mode=f.get('mode'),keywords=String(f.get('keywords')).split(',').map(v=>v.trim()).filter(Boolean);if(mode!=='any'&&!keywords.length)throw new Error('Добавьте ключевое слово.');await api('/rules','POST',{id:r.id||id(),name:f.get('name'),mode,keywords,reply:f.get('reply'),cooldown_seconds:+f.get('cooldown_seconds'),enabled:f.has('enabled')});toast('Правило сохранено.');});
}
function offerModal(t={}){
 let fields=t.fields||{};
 modal('Шаблон предложения',`<div class="field-row">${field('source','ID вашего лота для импорта','','number','min="1"')}${btn('import-fields','Импортировать','small-button')}</div><p id="fields-status" class="muted tiny">${fields.node_id?'Поля раздела #'+esc(fields.node_id)+' сохранены.':'Импортируйте исходный лот: специфичные поля раздела будут сохранены.'}</p>`+field('name','Название шаблона',t.name||'','text','required')+field('title','Заголовок предложения',t.title||'','text','required')+area('description','Описание предложения',t.description||'')+`<div class="grid-form">${field('price','Цена в валюте аккаунта',t.price||'','number','required min="0.01" step="0.01"')}${field('amount','Количество',t.amount||1,'number','required min="1"')}${field('target_id','ID лота для изменения; 0 = новый',t.target_id||0,'number','min="0"')}<div>${check('active','Активное предложение',t.active??true)}</div></div>`,async f=>{await api('/templates','POST',{id:t.id||id(),name:f.get('name'),title:f.get('title'),description:f.get('description'),price:+f.get('price'),amount:+f.get('amount'),target_id:+f.get('target_id'),active:f.has('active'),fields});toast('Шаблон сохранён.');});
 $('[data-action="import-fields"]',$('#dialog')).onclick=async e=>{e.preventDefault();const b=e.currentTarget;b.disabled=true;try{const source=+$('[name=source]',$('#dialog')).value;if(!source)throw new Error('Укажите ID своего лота.');fields=await api('/lots/'+source+'/fields');const form=$('#dialog-form');for(const [name,key] of [['title','fields[summary][ru]'],['description','fields[desc][ru]'],['price','price'],['amount','amount']]){if(fields[key])form.elements[name].value=fields[key];}if(!form.elements.name.value)form.elements.name.value=fields['fields[summary][ru]']||'Мой шаблон';$('#fields-status').textContent='Импортированы поля раздела #'+fields.node_id;toast('Поля импортированы.');}catch(err){toast(err.message,true);}finally{b.disabled=false;}};
}
function boostModal(b={}){modal('Расписание поднятия',field('name','Название категории',b.name||'','text','required')+field('category_id','ID категории из «Мои лоты»',b.category_id||'','number','required min="1"')+field('interval_minutes','Интервал, минут',b.interval_minutes||241,'number','required min="240" max="10080"')+'<p class="muted tiny">241 = 4 часа 1 минута. 240 = ровно 4 часа. FunPay может увеличить ожидание.</p>'+check('enabled','Включено',b.enabled??true),async f=>{await api('/boosts','POST',{name:f.get('name'),category_id:+f.get('category_id'),interval_minutes:+f.get('interval_minutes'),enabled:f.has('enabled')});toast('Расписание сохранено.');});}
async function action(name,ident){
 if(name==='refresh')return render();
 if(name==='go-extension'){location.hash='extension';return;}
 if(name==='add-product'||name==='edit-product')return productModal(state.products.find(p=>p.id===ident));
 if(name==='add-rule'||name==='edit-rule')return ruleModal(state.rules.find(r=>r.id===ident));
 if(name==='add-offer'||name==='edit-offer')return offerModal(state.templates.find(t=>t.id===ident));
 if(name==='add-boost'||name==='edit-boost')return boostModal(state.boosts.find(b=>String(b.category_id)===ident));
 if(name==='stock')return modal('Пополнить склад',`<div class="notice">Логины, пароли, ключи или инструкции сохраняются зашифрованными. Одна запись выдаётся один раз.</div><label>Формат<select name="format"><option value="lines">Одна запись на строку</option><option value="blocks">Записи разделены пустой строкой</option></select></label>`+area('items','Записи склада','','required placeholder="Введите свои данные для выдачи" style="min-height:220px"'),async f=>{const items=String(f.get('items')).split(f.get('format')==='blocks'?/\r?\n\s*\r?\n/:/\r?\n/).map(v=>v.trim()).filter(Boolean);const result=await api('/products/'+ident+'/stock','POST',{items});toast('Добавлено: '+result.added+'. Дубликатов: '+result.duplicates);},'Добавить на склад');
 if(name==='stock-list'){const rows=await api('/products/'+ident+'/stock');return modal('Позиции склада',`<p class="muted tiny">Данные выдачи скрыты. Номер позиции используется для учёта.</p><div class="table-wrap"><table><thead><tr><th>Позиция</th><th>Статус</th><th>Заказ</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr><td>#${r.id}</td><td>${esc(statusNames[r.state]||r.state)}</td><td>${esc(r.order_id||'—')}</td><td>${r.state==='available'?btn('remove-stock','Убрать','small-button danger',`data-id="${r.id}"`):''}</td></tr>`).join('')}</tbody></table></div>`,null);}
 if(name==='remove-stock'){if(!confirm('Убрать эту свободную позицию из выдачи?'))return;await api('/stock/'+ident,'DELETE');$('#dialog').close();toast('Позиция убрана.');return render();}
 if(name==='delete-rule'||name==='delete-boost'||name==='revoke-device'){if(!confirm('Подтвердить удаление / отключение?'))return;await api('/'+({'delete-rule':'rules','delete-boost':'boosts','revoke-device':'devices'}[name])+'/'+ident,'DELETE');toast('Готово.');return render();}
 if(name==='process-order')return modal('Проверить заказ на FunPay',field('order','Номер заказа без #','','text','required')+'<p class="muted tiny">Будет проверен реальный статус заказа. Если включена автовыдача и найден товар, покупателю отправятся данные.</p>',async f=>{await api('/orders/'+encodeURIComponent(String(f.get('order')).replace(/^#/,''))+'/process','POST',{});toast('Заказ проверен. Результат — в заказах и журнале.');},'Проверить и обработать');
 if(name==='reconcile'){const r=await api('/orders/'+ident+'/reconcile','POST',{resend:false});if(r.status==='not_found')return modal('Сообщение не найдено',`<div class="notice amber">В доступной истории нет подтверждения выдачи #${esc(ident)}. Проверьте чат на FunPay. Если выдачи не было, можно отправить ту же зарезервированную запись повторно.</div>`,async()=>{await api('/orders/'+ident+'/reconcile','POST',{resend:true});toast('Выдача подтверждена.');},'Повторить ту же запись');toast('Отправка подтверждена.');return render();}
 if(name==='publish'){const t=state.templates.find(t=>t.id===ident);return modal(t.target_id?'Изменить предложение':'Опубликовать новый лот',`<h3>${esc(t.title)}</h3><p class="muted">${t.target_id?'Будет изменён лот #'+t.target_id:'Будет создан новый лот.'} Цена: ${t.price}. Количество: ${t.amount}.</p>`+field('due','Дата и время; пусто = ближайший цикл','','datetime-local')+'<p class="muted tiny">Для выполнения очереди включите общий запуск. После неопределённой ошибки автоматического повтора не будет.</p>',async f=>{const key='fps-publish-'+ident;let request_id=localStorage.getItem(key)||id();localStorage.setItem(key,request_id);const due=f.get('due')?new Date(f.get('due')).getTime()/1000:null;await api('/templates/'+ident+'/publish','POST',{request_id,due});localStorage.removeItem(key);toast('Публикация добавлена в очередь.');},'Подтвердить публикацию');}
 if(name==='cancel-job'){await api('/jobs/'+encodeURIComponent(ident)+'/cancel','POST',{});toast('Публикация отменена.');return render();}
 if(name==='pair-code'){const p=await api('/pair-code','POST',{});$('#pair-result').innerHTML=`<div class="pair-code">${esc(p.code)}</div><p class="tiny muted">Действует 10 минут. Скопируйте в настройки расширения.</p>`;return;}
 if(name==='save-poll'){await api('/settings','PUT',{...state.status.settings,poll_seconds:+$('#poll-seconds').value});toast('Интервал сохранён.');return render();}
 if(name==='my-lots'){const lots=await api('/lots');return modal('Ваши лоты FunPay',lots.length?`<div class="table-wrap"><table><thead><tr><th>Лот</th><th>ID раздела</th><th>ID категории</th></tr></thead><tbody>${lots.map(l=>`<tr><td>${esc(l.title)}<small>ID лота: ${l.id}${l.native_auto?' · встроенная автовыдача':''}</small></td><td>${l.subcategory_id}</td><td>${l.category_id}<small>${esc(l.category)}</small></td></tr>`).join('')}</tbody></table></div>`:empty('Лоты не найдены','Создайте первый лот на FunPay, затем обновите список.'),null);}
}
document.addEventListener('click',async e=>{const b=e.target.closest('button[data-action]');if(!b||b.closest('#dialog')&&b.dataset.action==='import-fields')return;e.preventDefault();try{await action(b.dataset.action,b.dataset.id);}catch(err){toast(err.message,true);}});
$('#dialog-close').onclick=$('#dialog-cancel').onclick=()=>$('#dialog').close();
$('#global-toggle').onclick=async()=>{try{await api('/settings','PUT',{...state.status.settings,enabled:!state.status.settings.enabled});await render();}catch(e){toast(e.message,true);}};
$('#logout').onclick=async()=>{try{await api('/logout','POST',{});showLogin();}catch(e){toast(e.message,true);}};
$('#login-form').onsubmit=async e=>{e.preventDefault();const b=$('button',e.currentTarget);b.disabled=true;try{state.csrf=(await api('/login','POST',{password:new FormData(e.currentTarget).get('password')})).csrf;$('#login-form').reset();$('#login-error').textContent='';showShell();await render();}catch(err){$('#login-error').textContent=err.message;}finally{b.disabled=false;}};
window.addEventListener('hashchange',()=>{if(!$('#shell').classList.contains('hidden'))render();});
(async()=>{try{state.csrf=(await api('/session')).csrf;showShell();await render();}catch{showLogin();}})();
setInterval(async()=>{if($('#shell').classList.contains('hidden')||$('#dialog').open||document.hidden)return;try{state.status=await api('/status');header();}catch{}},15000);
''',
    'favicon.svg': r'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="18" fill="#3271ff"/><path d="M37 14H27c-8 0-12 5-12 13v23h9V35h13v-9H24v-1c0-3 1-4 4-4h9z" fill="white"/><circle cx="45" cy="44" r="8" fill="#83e9cc"/></svg>
''',
    'index.html': r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0b1018"><title>FunPay Studio — управление магазином</title><link rel="icon" href="/assets/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/style.css"><script src="/assets/app.js" defer></script></head>
<body>
<div id="login" class="login-screen"><section class="login-card"><div class="brand-mark">f<span>s</span></div><p class="eyebrow">ВАШ МАГАЗИН. ВАШИ ПРАВИЛА.</p><h1>FunPay Studio</h1><p class="muted">Войдите в свою панель управления.</p><form id="login-form"><label>Пароль администратора<input name="password" type="password" autocomplete="current-password" required placeholder="ADMIN_PASSWORD из настроек сервера"></label><button class="primary full">Войти в панель <span>↗</span></button></form><p id="login-error" role="alert"></p><a href="/install" class="text-link">Как установить и подключить расширение</a><div class="login-foot"><i class="dot"></i> Закрытая панель · один владелец</div></section></div>
<div id="shell" class="shell hidden">
<aside><a class="brand" href="#overview"><div class="brand-mark small">f<span>s</span></div><div>FunPay <b>Studio</b><small>SELLER WORKSPACE</small></div></a><div class="nav-label">МАГАЗИН</div><nav id="nav"></nav><div class="sidebar-bottom"><div class="help-box"><span>Начните с подключения</span><p>Аккаунт → товар → склад → автоматизация.</p><a href="/install" target="_blank" rel="noopener">Инструкция <span>↗</span></a></div><button id="logout" class="quiet full">Выйти из панели</button><small class="version">STUDIO 1.1 · НЕОФИЦИАЛЬНЫЙ ИНСТРУМЕНТ</small></div></aside>
<div class="workspace"><header><div class="breadcrumb">Рабочее пространство <span>/</span> <b id="crumb">Обзор</b></div><div class="header-actions"><span id="connection" class="badge">Нет подключения</span><button id="global-toggle" class="small-button">Запустить</button></div></header><main id="main" tabindex="-1"></main><footer>FunPay Studio <span>Ваши товары остаются под вашим контролем.</span><a href="/install" target="_blank" rel="noopener">Установка и помощь ↗</a></footer></div>
</div>
<dialog id="dialog"><form id="dialog-form"><div class="dialog-heading"><h2 id="dialog-title"></h2><button type="button" id="dialog-close" class="icon-button" aria-label="Закрыть">×</button></div><div id="dialog-body"></div><div class="dialog-actions"><button type="button" id="dialog-cancel">Отмена</button><button class="primary" id="dialog-submit">Сохранить</button></div></form></dialog>
<div id="toast" class="toast" role="status" aria-live="polite"></div>
</body></html>
''',
    'install.html': r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Установка — FunPay Studio</title><link rel="stylesheet" href="/assets/style.css"><link rel="icon" href="/assets/favicon.svg"></head><body><article class="install-page"><a class="brand" href="/"><div class="brand-mark small">f<span>s</span></div><div>FunPay <b>Studio</b><small>SELLER WORKSPACE</small></div></a><p class="eyebrow">ПОДКЛЮЧЕНИЕ ЗА НЕСКОЛЬКО ШАГОВ</p><h1>Ваш FunPay.<br>Теперь с вашими правилами.</h1><p class="lead muted">Расширение меняет оформление и уведомления. Сервер управляет складом, заказами, автоответами и поднятием предложений.</p><a class="primary-link" href="/download/extension.zip">Скачать расширение ZIP <span>↓</span></a><p class="tiny muted">Для настольных Chrome / Edge / Brave и совместимых Chromium-браузеров с Manifest V3 и Offscreen API. Установка требует вашего действия в браузере.</p>
<section class="install-step"><div class="step-num">01</div><div><h2>Установите расширение</h2><ol><li>Скачайте ZIP и распакуйте его в постоянную папку.</li><li>Chrome: откройте <code>chrome://extensions</code>. Edge: <code>edge://extensions</code>. Brave: <code>brave://extensions</code>.</li><li>Включите «Режим разработчика».</li><li>Нажмите «Загрузить распакованное расширение» и выберите папку <code>funpay-studio-extension</code>, в которой находится <code>manifest.json</code>.</li><li>Закрепите значок FunPay Studio на панели браузера.</li></ol><p>Firefox и мобильный Chrome этой сборкой не поддерживаются. В других Chromium-браузерах доступность звуков зависит от Offscreen API.</p></div></section>
<section class="install-step"><div class="step-num">02</div><div><h2>Свяжите расширение с панелью</h2><ol><li><a class="text-link" href="/">Войдите в панель</a> с паролем <code>ADMIN_PASSWORD</code>.</li><li>Откройте «Расширение» → «Получить код». Код действует 10 минут и применяется один раз.</li><li>Нажмите значок расширения → «Настроить».</li><li>Укажите адрес этой панели и код. Подтвердите доступ расширения только к вашему серверу.</li><li>Нажмите «Подключить». Пароль администратора расширению не нужен.</li></ol></div></section>
<section class="install-step"><div class="step-num">03</div><div><h2>Подключите свой FunPay</h2><ol><li>Войдите в FunPay в своём браузере.</li><li>Откройте инструменты разработчика (F12) → Application / Приложение → Cookies → <code>https://funpay.com</code>.</li><li>Скопируйте значение <code>golden_key</code>.</li><li>В защищённой HTTPS-панели: «Настройки» → вставьте ключ → «Проверить и подключить».</li></ol><p>Ключ даёт доступ к аккаунту. Он хранится зашифрованным на вашем сервере. Не отправляйте его в Telegram, GitHub, чужие панели или скриншоты.</p></div></section>
<section class="install-step"><div class="step-num">04</div><div><h2>Добавьте товары и включите автоматизацию</h2><ol><li>«Товары и склад» → «Добавить товар». Название для сопоставления и ID раздела должны совпадать с заказом. Кнопка «Мои лоты» показывает значения.</li><li>Укажите текст выдачи с <code>{items}</code>. Нажмите «Пополнить склад»: одна запись на строку или записи, разделённые пустой строкой.</li><li>Настройте автоответы. Для автоподнятия добавьте ID категории из списка своих лотов и интервал 241 минута.</li><li>В «Автоматизация» включите нужные функции, затем общий запуск. Прежние заказы при первом включении требуют ручной проверки.</li></ol><p>Не включайте одновременно две системы автовыдачи для одного товара. Лоты со встроенной автовыдачей FunPay должны использовать её собственный склад.</p></div></section>
<section class="install-step"><div class="step-num">05</div><div><h2>Настройте фон, звуки и шаблоны</h2><p>В настройках расширения загрузите фон и аудиофайл, настройте громкость и нажмите «Сохранить». Нажмите «Проверить звук». Фон виден только в вашем браузере.</p><p>На странице редактирования лота FunPay появятся кнопки Studio: сохранить заполненную форму как шаблон и вставить сохранённый шаблон. Сохранение шаблона не публикует лот. Публикация доступна в панели в разделе «Предложения».</p></div></section>
<section class="install-step"><div class="step-num">06</div><div><h2>Telegram и постоянная работа</h2><p>На Render задайте <code>TELEGRAM_BOT_TOKEN</code> из @BotFather и собственный числовой <code>TELEGRAM_OWNER_ID</code>. Отправьте своему боту <code>/start</code>. Появятся кнопки панели и установки. Команды: <code>/status</code>, <code>/pause</code>, <code>/resume</code>.</p><p>На Render данные хранятся в PostgreSQL: задайте <code>DATABASE_URL</code>, например из Neon Free. Платный диск не нужен. Render Free засыпает после 15 минут без входящих запросов; до пробуждения автоматизация остановлена. <a href="/help">Полная инструкция GitHub → Render → Neon</a>.</p></div></section>
<section class="card" style="margin-top:30px"><h2>Как работает бонусное время</h2><p class="muted">Бонус учитывается один раз после проверки отзыва в закрытом заказе. Срок в панели — учёт доступа. Для реального продления вашего сервиса подключите свой HTTPS-обработчик ACCESS_WEBHOOK_URL. Панель отдельно показывает, подтвердил ли он продление. Подписки сторонних сервисов, включая ChatGPT, от изменения счётчика автоматически не продлеваются.</p></section><a href="/" class="text-link">← Открыть панель управления</a></article></body></html>
''',
    'style.css': r''':root{color-scheme:dark;--bg:#0b1018;--panel:#121a26;--panel2:#172131;--border:#253144;--text:#edf2fa;--muted:#8f9db2;--blue:#4580ff;--mint:#78dfb8;--warn:#edc17c;--red:#f69c9c;font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}button,input,textarea,select{font:inherit}a{color:inherit;text-decoration:none}button,a,input,select,textarea{outline-offset:4px}button{cursor:pointer;background:#1c293b;border:1px solid #32415a;color:var(--text);border-radius:9px;padding:11px 17px;transition:background .15s,transform .15s}button:hover{background:#293b55}button:active{transform:translateY(1px)}button:disabled{opacity:.5;cursor:wait}button.primary,.primary-link{background:var(--blue);border-color:var(--blue);color:white}.primary:hover{background:#6193ff}.danger{color:var(--red)}.quiet{background:transparent;border-color:transparent;color:var(--muted)}.full{width:100%}.hidden{display:none!important}.muted{color:var(--muted);line-height:1.65}.tiny{font-size:12px}.eyebrow{font-size:10px;font-weight:700;letter-spacing:.19em;color:var(--muted)}h1,h2,h3,p{margin-top:0}h1{font-size:32px;letter-spacing:-1.2px;line-height:1.2;margin-bottom:13px}h2{font-size:19px;letter-spacing:-.4px}h3{font-size:15px}p{line-height:1.6}small{color:var(--muted)}.shell{display:grid;grid-template-columns:228px 1fr;min-height:100vh}aside{position:sticky;top:0;height:100vh;background:#0e1520;border-right:1px solid var(--border);padding:30px 18px 20px;display:flex;flex-direction:column}.brand{display:flex;align-items:center;gap:10px;font-size:17px;padding:0 8px 34px;white-space:nowrap}.brand b{font-weight:400;color:#a9c2f0}.brand small{display:block;font-size:8px;letter-spacing:.2em;margin-top:5px}.brand-mark{font-size:40px;background:linear-gradient(145deg,#528dff,#2457cd);height:76px;width:76px;border-radius:23px;display:grid;place-content:center;position:relative;font-weight:800;letter-spacing:-8px;padding-right:10px;box-shadow:0 10px 35px #255ac03b}.brand-mark span{display:contents;color:#a5ffdb}.brand-mark.small{height:38px;width:38px;border-radius:12px;font-size:23px;letter-spacing:-5px;padding-right:5px}.nav-label{font-size:9px;letter-spacing:.16em;color:#62738c;padding:0 13px 12px}nav{display:grid;gap:5px}nav a{display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:9px;color:#94a3b9;font-size:13px}nav a:hover{background:#162131}nav a.active{background:#1a2c4d;color:#b6d1ff}nav svg{width:18px;height:18px;stroke-width:1.6;stroke:currentColor;fill:none;flex-shrink:0}.sidebar-bottom{margin-top:auto;padding-top:25px}.help-box{border:1px solid var(--border);border-radius:12px;padding:15px;background:linear-gradient(140deg,#172133,#101923);font-size:11px}.help-box span{font-weight:650}.help-box p{color:var(--muted);margin:8px 0}.help-box a{color:#8cb4ff;display:flex;justify-content:space-between}.version{display:block;font-size:8px;text-align:center;letter-spacing:.05em;margin-top:15px;color:#5f7087}.workspace{min-width:0}header{height:80px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 36px;gap:14px}.breadcrumb{font-size:12px;color:var(--muted)}.breadcrumb span{padding:0 14px;color:#48566c}.breadcrumb b{color:#cbd5e5;font-weight:500}.header-actions{display:flex;align-items:center;gap:14px}.badge{display:inline-flex;align-items:center;gap:7px;border-radius:25px;border:1px solid #324157;background:#17202e;color:#b7c2d5;font-size:10px;padding:6px 10px;white-space:nowrap}.badge.good{background:#142b27;border-color:#23473d;color:var(--mint)}.badge.warn{background:#30291e;border-color:#514129;color:var(--warn)}.badge.bad{background:#321f28;border-color:#59323e;color:var(--red)}.dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor}.small-button{padding:8px 12px;font-size:11px}main{max-width:1470px;margin:auto;padding:36px;min-height:calc(100vh - 140px)}.page-head{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:24px}.page-head p{margin:0}.page-head h1{font-size:28px;margin-bottom:7px}.actions{display:flex;gap:10px;flex-wrap:wrap}.hero{position:relative;background:radial-gradient(ellipse at 95% 40%,#1e3f7338,transparent 60%),linear-gradient(110deg,#152239,#111d30);border:1px solid #294063;border-radius:16px;padding:30px;overflow:hidden;display:flex;gap:28px;justify-content:space-between;align-items:center;margin-bottom:22px}.hero-text{max-width:560px}.hero .eyebrow{color:#8dadde;margin-bottom:14px}.hero h1{font-size:31px}.hero p{color:#a0b1cc;font-size:13px;max-width:500px}.hero-art{position:relative;width:175px;height:145px;flex-shrink:0}.orb{position:absolute;width:100px;height:100px;border:1px solid #4779c4;transform:rotate(-22deg);border-radius:24px;background:linear-gradient(145deg,#214a83,#172944);left:20px;top:25px;box-shadow:16px 16px 0 #14253e,17px 17px 0 #294874;display:grid;place-content:center;color:#85b1ff;font-size:46px}.hero-chip{position:absolute;bottom:5px;right:0;background:#17372f;border:1px solid #2c6351;color:#95e5bd;font-size:10px;border-radius:8px;padding:9px 13px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:15px;margin-bottom:25px}.stat{border:1px solid var(--border);background:var(--panel);border-radius:13px;padding:21px}.stat-label{font-size:11px;color:var(--muted);display:flex;justify-content:space-between}.stat strong{display:block;font-size:32px;font-weight:550;letter-spacing:-1.5px;margin:15px 0 7px}.stat small{font-size:10px}.stat .spark{color:#7098d2}.grid-2{display:grid;grid-template-columns:1.35fr 1fr;gap:22px}.card{background:var(--panel);border:1px solid var(--border);border-radius:13px;padding:24px;margin-bottom:20px}.card-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:21px}.card-head h2{margin:0}.card-head .text-link{font-size:11px}.text-link{color:#89b3ff}.setup-item{display:flex;gap:14px;align-items:center;border-top:1px solid #253044;padding:16px 0}.setup-item:first-of-type{border:0}.step-num{border:1px solid #314057;display:grid;place-content:center;border-radius:9px;width:31px;height:31px;flex-shrink:0;color:#98b5e0;font-size:12px}.setup-item.done .step-num{background:#19382c;color:#a4e4bd;border-color:#2b5140}.setup-item h3{font-size:12px;margin:0 0 4px}.setup-item p{font-size:11px;color:var(--muted);margin:0}.setup-item .arrow{margin-left:auto;color:#91abd0}.switch-line{display:flex;justify-content:space-between;align-items:center;padding:15px 0;border-top:1px solid #253044;gap:15px}.switch-line:first-of-type{border:0}.switch-line h3{margin:0 0 4px;font-size:12px}.switch-line p{font-size:10px;color:var(--muted);margin:0}.switch{appearance:none;flex-shrink:0;width:34px!important;height:20px!important;border-radius:20px!important;background:#2a364a!important;border:0!important;padding:0!important;position:relative;cursor:pointer;margin:0!important}.switch:after{content:'';position:absolute;width:14px;height:14px;border-radius:50%;background:#8190a9;top:3px;left:3px;transition:left .18s}.switch:checked{background:#3777f7!important}.switch:checked:after{left:17px;background:white}.notice{border:1px solid #34415c;background:#172237;padding:15px 18px;border-radius:10px;font-size:12px;line-height:1.6;color:#b3c5e4;margin-bottom:20px}.notice.amber{border-color:#4f402c;background:#272219;color:#d6bf96}.empty{padding:38px 20px;text-align:center;color:var(--muted);font-size:12px}.empty-symbol{width:46px;height:46px;margin:0 auto 17px;display:grid;place-content:center;border-radius:12px;background:#1d293c;border:1px solid #31415b;font-size:23px;color:#8ba9d8}.empty h3{color:#cfdaec;font-size:14px}.empty p{max-width:400px;margin:8px auto 18px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px;text-align:left}th{color:#8395b0;font-weight:500;font-size:10px;white-space:nowrap;padding:12px 12px;border-bottom:1px solid var(--border)}td{padding:14px 12px;border-bottom:1px solid #222e40;vertical-align:middle}td .actions{flex-wrap:nowrap}td small{display:block;margin-top:5px}tr:last-child td{border-bottom:0}.product-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.product-card{background:var(--panel);border:1px solid var(--border);border-radius:13px;padding:22px;display:flex;flex-direction:column}.product-card h2{margin:18px 0 8px;font-size:18px;overflow-wrap:anywhere}.product-card .muted{font-size:11px;min-height:35px}.product-top{display:flex;justify-content:space-between;align-items:center}.product-icon{background:#22324f;color:#96baff;border-radius:10px;width:38px;height:38px;display:grid;place-content:center;font-weight:700}.stock-count{padding:16px 0;border-top:1px solid var(--border);margin-top:auto;display:flex;justify-content:space-between;color:var(--muted);font-size:12px}.stock-count b{color:white}.product-card .actions{display:grid;grid-template-columns:1fr auto}.link-button{background:transparent;padding:0;border:0;color:#8cb4ff;cursor:pointer}.grid-form{display:grid;grid-template-columns:1fr 1fr;gap:15px}.span-2{grid-column:1/-1}label{display:flex;flex-direction:column;gap:8px;margin-bottom:16px;color:#b9c6db;font-size:12px}input:not([type=checkbox]),textarea,select{width:100%;padding:12px 13px;background:#0d1522;border:1px solid #30415b;border-radius:8px;color:var(--text)}textarea{min-height:104px;resize:vertical;line-height:1.55}input::placeholder,textarea::placeholder{color:#60728d}input:focus,textarea:focus,select:focus{border-color:#6394f3;outline:2px solid #3069d529}label small{font-size:10px;line-height:1.5}label.check{flex-direction:row;align-items:center;gap:10px}input[type=checkbox]{accent-color:var(--blue)}.field-row{display:flex;gap:12px;align-items:end}.field-row>label{flex:1}.field-row>button{margin-bottom:16px;flex-shrink:0}.rule-row{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:20px 0;border-top:1px solid var(--border)}.rule-row:first-child{border:0}.rule-row h3{margin-bottom:6px}.rule-row p{font-size:12px;color:var(--muted);margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.rule-content{min-width:0}.rule-meta{font-size:10px;color:#8296b4;margin-top:8px}.event{display:flex;gap:13px;padding:13px 0;border-bottom:1px solid #233046;align-items:flex-start}.event:last-child{border-bottom:0}.event-dot{color:#79a1e0;width:29px;height:29px;display:grid;place-content:center;background:#20314b;border-radius:8px;flex-shrink:0}.event p{font-size:12px;margin:0 0 3px}.event small{font-size:10px}.event-time{margin-left:auto;white-space:nowrap;color:var(--muted);font-size:10px}.section-title{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin:32px 0 14px}footer{font-size:10px;color:#65758f;border-top:1px solid var(--border);padding:23px 36px;display:flex;justify-content:space-between;gap:20px}footer span{color:#52617a}.login-screen{min-height:100vh;display:grid;place-content:center;padding:26px;background:radial-gradient(ellipse at 50% 0,#1b3156,transparent 60%)}.login-card{width:450px;max-width:100%;background:#101a28;border:1px solid #2b3c56;border-radius:24px;padding:42px}.login-card .brand-mark{margin-bottom:32px}.login-card h1{font-size:34px}.login-card .primary{display:flex;justify-content:space-between;margin-top:23px}.login-card .text-link{font-size:11px}.login-foot{border-top:1px solid var(--border);margin-top:30px;padding-top:22px;font-size:10px;color:var(--muted)}.login-foot i{color:var(--mint);margin-right:6px}#login-error{color:var(--red);font-size:12px;margin-top:15px}dialog{background:#141e2d;color:var(--text);border:1px solid #344764;border-radius:16px;width:660px;max-width:calc(100vw - 26px);max-height:90vh;padding:26px;box-shadow:0 40px 120px #0008}dialog::backdrop{background:#030914bd;backdrop-filter:blur(4px)}.dialog-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.dialog-heading h2{margin:0}.icon-button{font-size:25px;padding:0 8px;background:transparent;border:0;color:var(--muted)}.dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px;padding-top:20px;border-top:1px solid var(--border)}.toast{position:fixed;bottom:24px;right:26px;background:#233d36;border:1px solid #3e6b5b;color:#d2f2e6;padding:15px 22px;border-radius:11px;box-shadow:0 8px 40px #0006;max-width:440px;z-index:100;opacity:0;transform:translateY(20px);transition:.2s;pointer-events:none;font-size:12px;line-height:1.5}.toast.visible{opacity:1;transform:translateY(0)}.toast.error{background:#422d33;border-color:#7c4b5a;color:#ffd7db}code{font-family:ui-monospace,Consolas,monospace;background:#0b1522;padding:3px 6px;border-radius:4px;color:#afcaff;font-size:.93em}.pair-code{font-size:24px;letter-spacing:3px;word-break:break-all;background:#0d1625;border:1px dashed #40618b;padding:20px;border-radius:12px;text-align:center;user-select:all}.install-page{max-width:920px;margin:0 auto;padding:60px 30px}.install-page .brand{padding-left:0}.install-page h1{font-size:42px;max-width:650px}.install-page .lead{font-size:16px;max-width:660px}.install-step{display:grid;grid-template-columns:40px 1fr;gap:20px;padding:26px 0;border-bottom:1px solid var(--border)}.install-step h2{margin-bottom:10px}.install-step li{font-size:13px;color:var(--muted);line-height:1.8}.install-step p{color:var(--muted);font-size:13px}.primary-link{display:inline-flex;align-items:center;gap:16px;padding:14px 24px;border-radius:10px;margin:10px 0}.table-link{color:#9cbfff}.searchbar{display:flex;gap:12px;align-items:center;margin-bottom:20px}.searchbar input{max-width:340px}.searchbar select{max-width:220px}@media(min-width:1500px){main{padding:42px 48px}}@media(max-width:1180px){.product-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.grid-2{grid-template-columns:1fr}.hero-art{display:none}.shell{grid-template-columns:210px 1fr}.stats{gap:10px}.stat{padding:17px}header,main{padding-left:24px;padding-right:24px}}@media(max-width:780px){.shell{display:block}aside{position:static;height:auto;padding:18px 15px 8px;border-right:0;border-bottom:1px solid var(--border)}.brand{padding:0 8px 18px}.nav-label,.sidebar-bottom{display:none}nav{display:flex;overflow:auto;padding-bottom:8px;gap:4px}nav a{white-space:nowrap;font-size:12px;padding:10px}nav svg{width:15px}header{height:65px;padding:0 18px}.breadcrumb{display:none}.header-actions{width:100%;justify-content:space-between}main{padding:24px 16px}.page-head{align-items:flex-start}.page-head h1{font-size:24px}.page-head p{font-size:12px}.hero{padding:24px}.hero h1{font-size:28px}.stats{grid-template-columns:1fr 1fr}.stat strong{font-size:29px}.product-grid{grid-template-columns:1fr}.grid-form{grid-template-columns:1fr}.span-2{grid-column:auto}.card{padding:19px}.rule-row{align-items:flex-start;flex-direction:column}.rule-row .actions{margin-left:auto}.searchbar{flex-wrap:wrap}.searchbar input,.searchbar select{max-width:100%}.toast{left:16px;right:16px;bottom:16px}.login-card{padding:30px}.login-card h1{font-size:30px}footer{padding:20px 18px}footer span{display:none}.install-page{padding:30px 20px}.install-page h1{font-size:32px}.install-step{gap:12px}.field-row{display:block}.field-row>button{margin-top:0}}
''',
}


# Embedded extension resources
EXTENSION_TEXT = {
    'background.js': r''''use strict';
const defaults={baseURL:'',token:'',themeEnabled:false,background:'',backgroundColor:'#dce5f1',overlay:15,accent:'#4580ff',soundEnabled:true,volume:0.65,messageSound:'',orderSound:'',muteOriginal:false,notifications:true};
let creating, polling, lastPoll=0;
async function settings(){return {...defaults,...(await chrome.storage.local.get('settings')).settings};}
async function init(){await chrome.storage.local.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'});const alarm=await chrome.alarms.get('fps-poll');if(!alarm)await chrome.alarms.create('fps-poll',{periodInMinutes:1});}
init().catch(()=>{});
chrome.runtime.onInstalled.addListener(()=>{init().then(()=>chrome.runtime.openOptionsPage());});
chrome.runtime.onStartup.addListener(()=>{init().then(poll).catch(()=>{});});
chrome.action.onClicked.addListener(async()=>{const cfg=await settings(); if(cfg.baseURL)await chrome.tabs.create({url:cfg.baseURL+'/#chats'});else await chrome.runtime.openOptionsPage();});
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='fps-poll')poll().catch(()=>{});});
function origin(value){const u=new URL(value);if(u.username||u.password||u.search||u.hash)throw Error('Укажите только адрес вашего сервера.');if(u.protocol!=='https:'&&!(u.protocol==='http:'&&['127.0.0.1','localhost'].includes(u.hostname)))throw Error('Для сервера нужен HTTPS.');return u.origin;}
async function api(path,options={}){
 const cfg=await settings();if(!cfg.baseURL||!cfg.token)throw Error('Подключите расширение в настройках.');
 const response=await fetch(cfg.baseURL+'/api'+path,{...options,headers:{Authorization:'Bearer '+cfg.token,'Content-Type':'application/json'},credentials:'omit',redirect:'error',signal:AbortSignal.timeout(20000)});
 if(!response.ok)throw Error(response.status===401?'Ключ подключения отозван. Получите новый код в панели.':'Сервер ответил '+response.status);
 return response.json();
}
async function play(kind='message',test=false){
 const cfg=await settings();if(!cfg.soundEnabled&&!test)return;
 const contexts=await chrome.runtime.getContexts({contextTypes:['OFFSCREEN_DOCUMENT'],documentUrls:[chrome.runtime.getURL('offscreen.html')]});
 if(!contexts.length){if(!creating)creating=chrome.offscreen.createDocument({url:'offscreen.html',reasons:['AUDIO_PLAYBACK'],justification:'Воспроизведение выбранных пользователем уведомлений FunPay Studio.'}).finally(()=>{creating=null;});await creating;}
 const custom=kind==='order'?cfg.orderSound:cfg.messageSound;
 const result=await chrome.runtime.sendMessage({target:'audio',url:custom||chrome.runtime.getURL(kind==='order'?'order.wav':'message.wav'),volume:cfg.volume});
 if(!result?.ok)throw Error(result?.error||'Не удалось воспроизвести звук.');
}
async function mute(tabId,enable){
 const tab=await chrome.tabs.get(tabId);const state=await chrome.storage.local.get('mutedTabs');const saved=state.mutedTabs||{};
 if(enable){if(!(tabId in saved)){saved[tabId]=Boolean(tab.mutedInfo?.muted);await chrome.storage.local.set({mutedTabs:saved});}await chrome.tabs.update(tabId,{muted:true});}
 else if(tabId in saved){await chrome.tabs.update(tabId,{muted:saved[tabId]});delete saved[tabId];await chrome.storage.local.set({mutedTabs:saved});}
}
async function updateTabs(healthy=true){const cfg=await settings();const tabs=await chrome.tabs.query({url:'https://funpay.com/*'});for(const t of tabs){try{await mute(t.id,Boolean(cfg.muteOriginal&&cfg.token&&healthy));await chrome.tabs.sendMessage(t.id,{type:'refresh-theme'});}catch{}}}
chrome.tabs.onRemoved.addListener(async tabId=>{const {mutedTabs={}}=await chrome.storage.local.get('mutedTabs');if(tabId in mutedTabs){delete mutedTabs[tabId];await chrome.storage.local.set({mutedTabs});}});
chrome.tabs.onUpdated.addListener((tabId,info)=>{if(info.url&&!info.url.startsWith('https://funpay.com/'))mute(tabId,false).catch(()=>{});});
async function poll(){
 if(polling)return polling;if(Date.now()-lastPoll<4500)return;lastPoll=Date.now();
 polling=(async()=>{
  const cfg=await settings();if(!cfg.token||!cfg.baseURL)return;
  try{
   const st=await chrome.storage.local.get('cursor');const after=Number.isInteger(st.cursor)?st.cursor:-1;
   const data=await api('/events?after='+after);
   await chrome.storage.local.set({cursor:data.cursor,lastSuccess:Date.now(),connectionError:''});
   for(const e of data.events){
    if(!['message','delivery','attention','bonus','error'].includes(e.kind))continue;
    if(cfg.notifications)await chrome.notifications.create('fps-'+e.seq,{type:'basic',iconUrl:'icons/128.png',title:'FunPay Studio · '+e.title,message:e.detail||'Откройте панель',silent:true});
   }
   if(data.events.some(e=>['delivery','attention','bonus'].includes(e.kind)))await play('order');
   else if(data.events.some(e=>e.kind==='message'))await play('message');
   await chrome.action.setBadgeText({text:''});await updateTabs(true);
  }catch(e){await chrome.storage.local.set({connectionError:e.message});await chrome.action.setBadgeText({text:'!'});await chrome.action.setBadgeBackgroundColor({color:'#b55a52'});await updateTabs(false);}
 })().finally(()=>{polling=null;});return polling;
}
chrome.notifications.onClicked.addListener(async()=>{const cfg=await settings();if(cfg.baseURL)await chrome.tabs.create({url:cfg.baseURL+'/#chats'});});
async function handle(msg,sender){
 const trusted=Boolean(sender.url?.startsWith(chrome.runtime.getURL('')));
 const funpay=Boolean(sender.tab&&sender.url?.startsWith('https://funpay.com/'));
 if(!trusted&&!funpay)throw Error('Недопустимый источник.');
 if(msg.type==='public-config'){const cfg=await settings();return {themeEnabled:cfg.themeEnabled,background:cfg.background,backgroundColor:cfg.backgroundColor,overlay:cfg.overlay,accent:cfg.accent};}
 if(msg.type==='open-panel'){const cfg=await settings();if(cfg.baseURL)return chrome.tabs.create({url:cfg.baseURL+'/#chats'});return chrome.runtime.openOptionsPage();}
 if(msg.type==='poll'){await poll();return {ok:true};}
 if(msg.type==='templates')return api('/templates');
 if(msg.type==='capture')return api('/templates/capture',{method:'POST',body:JSON.stringify(msg.template)});
 if(!trusted)throw Error('Откройте настройки расширения.');
 if(msg.type==='pair'){
  const url=origin(msg.baseURL);if(!await chrome.permissions.contains({origins:[url+'/*']}))throw Error('Сначала разрешите доступ к вашему серверу.');
  const response=await fetch(url+'/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:msg.code,name:'Chromium · '+new Date().toLocaleDateString('ru-RU')}),credentials:'omit',redirect:'error',signal:AbortSignal.timeout(20000)});
  const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Неверный код.');
  const cfg=await settings();cfg.baseURL=url;cfg.token=result.token;await chrome.storage.local.set({settings:cfg});await chrome.storage.local.remove('cursor');lastPoll=0;await poll();return {ok:true};
 }
 if(msg.type==='save'){
  const cfg=await settings();const keys=['themeEnabled','background','backgroundColor','overlay','accent','soundEnabled','volume','messageSound','orderSound','muteOriginal','notifications'];for(const k of keys)if(k in msg.settings)cfg[k]=msg.settings[k];await chrome.storage.local.set({settings:cfg});await updateTabs();return {ok:true};
 }
 if(msg.type==='test-sound'){await play(msg.kind,true);return {ok:true};}
 if(msg.type==='status'){const local=await chrome.storage.local.get(['lastSuccess','connectionError']);let server=null;try{server=await api('/status');}catch(e){local.connectionError=e.message;}return {...local,server};}
 if(msg.type==='disconnect'){const cfg=await settings();cfg.token='';await chrome.storage.local.set({settings:cfg});await chrome.storage.local.remove('cursor');await updateTabs(false);return {ok:true};}
 throw Error('Неизвестная команда.');
}
chrome.runtime.onMessage.addListener((msg,sender,reply)=>{if(msg.target==='audio')return false;handle(msg,sender).then(result=>reply({ok:true,result})).catch(e=>reply({ok:false,error:e.message}));return true;});
''',
    'content.js': r'''(() => {
 'use strict';
 if(window.__funpayStudioLoaded)return;window.__funpayStudioLoaded=true;
 const send=async message=>{const r=await chrome.runtime.sendMessage(message);if(!r?.ok)throw Error(r?.error||'Откройте настройки расширения.');return r.result;};
 let cfg, templates=[];
 const host=document.createElement('div');host.id='funpay-studio-controls';host.style.cssText='position:fixed;right:20px;bottom:20px;z-index:2147483646';document.documentElement.append(host);
 const root=host.attachShadow({mode:'closed'});
 root.innerHTML=`<style>:host{font-family:Arial,sans-serif}button,select{font:13px Arial,sans-serif;cursor:pointer;border:1px solid #3d5272;border-radius:9px;padding:11px 14px;background:#172338;color:#e5efff}button:hover{background:#233e66}.main{background:#3978ff;border:0;box-shadow:0 6px 25px #00163355;font-weight:700}.panel{display:none;box-sizing:border-box;width:290px;background:#111d2e;border:1px solid #344968;border-radius:14px;padding:16px;margin-bottom:10px;color:#d5e6ff;box-shadow:0 10px 35px #00163366}.panel.open{display:block}h3{font-size:15px;margin:0 0 12px}p{font-size:11px;line-height:1.6;color:#a8bdd8}button,select{width:100%;margin-top:8px}small{display:block;color:#86a5ce;font-size:10px;margin-top:12px;line-height:1.5}.msg{font-size:11px;line-height:1.5;color:#a7dfc7;overflow-wrap:anywhere}</style><div class="panel"><h3>FunPay Studio</h3><button class="open">Открыть панель ↗</button><div class="form-tools"><button class="capture">Сохранить форму как шаблон</button><button class="load">Загрузить мои шаблоны</button><select class="templates" aria-label="Шаблон"><option value="">Выберите шаблон</option></select><button class="fill">Вставить в форму</button><small>Вставка не публикует предложение. Проверьте форму и сохраните её на FunPay.</small></div><p class="no-form">На странице редактирования предложения здесь появятся инструменты шаблонов.</p><p class="msg" role="status"></p></div><button class="main">◈ Studio</button>`;
 const panel=root.querySelector('.panel'), notice=root.querySelector('.msg');
 const form=()=>document.querySelector('form.form-offer-editor');
 const message=t=>{notice.textContent=t;};
 const action=fn=>async()=>{try{await fn();}catch(e){message(e.message);}};
 root.querySelector('.main').onclick=()=>{panel.classList.toggle('open');root.querySelector('.form-tools').style.display=form()?'block':'none';root.querySelector('.no-form').style.display=form()?'none':'block';};
 root.querySelector('.open').onclick=action(()=>send({type:'open-panel'}));
 function capture(){
  const f=form();if(!f)throw Error('Откройте редактирование предложения.');const fields={};
  for(const [k,v] of new FormData(f)){if(typeof v==='string'&&(k.startsWith('fields[')||['node_id','server_id','side_id','offer_id','amount','price','active','deactivate_after_sale'].includes(k)))fields[k]=v;}
  const title=fields['fields[summary][ru]']||fields['fields[summary][en]'];const price=Number(String(fields.price||'').replace(',','.'));
  if(!title||!price)throw Error('Заполните заголовок и цену в форме.');
  return {id:crypto.randomUUID(),name:title.slice(0,150),title,description:fields['fields[desc][ru]']||'',price,amount:Number(fields.amount)||1,active:fields.active==='on',target_id:0,fields};
 }
 root.querySelector('.capture').onclick=action(async()=>{await send({type:'capture',template:capture()});message('Шаблон сохранён в панели.');});
 root.querySelector('.load').onclick=action(async()=>{templates=await send({type:'templates'});const select=root.querySelector('.templates');select.replaceChildren(new Option('Выберите шаблон',''));for(const t of templates)select.add(new Option(t.name,t.id));message('Загружено шаблонов: '+templates.length);});
 root.querySelector('.fill').onclick=action(async()=>{
  const t=templates.find(x=>x.id===root.querySelector('.templates').value),f=form();if(!t||!f)throw Error('Выберите шаблон и откройте редактор предложения.');
  const current=f.elements.namedItem('node_id')?.value;if(current&&t.fields.node_id&&String(current)!==String(t.fields.node_id))throw Error('Шаблон относится к другому разделу FunPay.');
  const values={...t.fields,'fields[summary][ru]':t.title,'fields[desc][ru]':t.description,price:String(t.price),amount:String(t.amount),active:t.active?'on':''};let n=0;
  for(const el of f.elements){if(!el.name||!(el.name in values)||['offer_id','node_id','csrf_token','secrets','auto_delivery','deleted'].includes(el.name))continue;
   if(el.type==='checkbox')el.checked=['on','1','true'].includes(values[el.name]);else if(el.type==='radio')el.checked=el.value===values[el.name];else if(el.tagName==='SELECT'){if(![...el.options].some(o=>o.value===values[el.name]))continue;el.value=values[el.name];}else if(['INPUT','TEXTAREA'].includes(el.tagName)&&el.type!=='file')el.value=values[el.name];else continue;
   el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));n++;
  }message('Заполнено полей: '+n+'. Проверьте предложение перед сохранением.');
 });
 const style=document.createElement('style');style.id='funpay-studio-theme';document.head.append(style);
 async function theme(){cfg=await send({type:'public-config'});if(!cfg.themeEnabled){style.textContent='';return;}
  const color=/^#[a-f0-9]{6}$/i.test(cfg.backgroundColor)?cfg.backgroundColor:'#dce5f1';const accent=/^#[a-f0-9]{6}$/i.test(cfg.accent)?cfg.accent:'#4580ff';
  const image=/^data:image\/(png|jpeg|webp);base64,[a-z0-9+/=]+$/i.test(cfg.background||'')?`url("${cfg.background}")`:'none';const shade=Math.min(80,Math.max(0,Number(cfg.overlay)||0))/100;
  style.textContent=`body{background-color:${color}!important;background-image:linear-gradient(rgba(0,0,0,${shade}),rgba(0,0,0,${shade})),${image}!important;background-size:cover!important;background-position:center!important;background-attachment:fixed!important}body>.wrapper{background:transparent!important}body .btn-primary{background-color:${accent}!important;border-color:${accent}!important}`;
 }
 theme().catch(()=>{});chrome.runtime.onMessage.addListener(m=>{if(m.type==='refresh-theme')theme().catch(()=>{});});
 const timer=setInterval(()=>send({type:'poll'}).catch(()=>{}),5000);window.addEventListener('pagehide',()=>clearInterval(timer),{once:true});send({type:'poll'}).catch(()=>{});
})();
''',
    'manifest.json': r'''{
  "manifest_version": 3,
  "name": "FunPay Studio",
  "version": "1.1.0",
  "description": "Ваш фон, звуки, шаблоны предложений и подключение к личной панели FunPay Studio.",
  "minimum_chrome_version": "116",
  "permissions": [
    "storage",
    "unlimitedStorage",
    "alarms",
    "notifications",
    "offscreen"
  ],
  "host_permissions": [
    "https://funpay.com/*"
  ],
  "optional_host_permissions": [
    "https://*/*",
    "http://127.0.0.1/*",
    "http://localhost/*"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "action": {
    "default_title": "nezzx helper"
  },
  "options_page": "options.html",
  "icons": {
    "16": "icons/16.png",
    "32": "icons/32.png",
    "48": "icons/48.png",
    "128": "icons/128.png"
  },
  "content_scripts": [
    {
      "matches": [
        "https://funpay.com/*"
      ],
      "js": [
        "content.js"
      ],
      "run_at": "document_idle"
    }
  ],
  "content_security_policy": {
    "extension_pages": "script-src 'self'; object-src 'none'"
  }
}
''',
    'offscreen.html': r'''<!doctype html><html><head><meta charset="utf-8"><title>FunPay Studio Audio</title></head><body><script src="offscreen.js"></script></body></html>
''',
    'offscreen.js': r'''let audio;
chrome.runtime.onMessage.addListener((m,sender,reply)=>{
 if(m.target!=='audio')return false;
 if(!m.url?.startsWith(chrome.runtime.getURL(''))&&!/^data:audio\/[a-z0-9.+-]+;base64,/i.test(m.url)){reply({ok:false,error:'Неверный аудиофайл.'});return false;}
 if(audio)audio.pause();audio=new Audio(m.url);audio.volume=Math.max(0,Math.min(1,Number(m.volume)||0));
 audio.play().then(()=>reply({ok:true})).catch(()=>reply({ok:false,error:'Браузер заблокировал звук или файл не поддерживается. Нажмите «Проверить звук» в настройках.'}));return true;
});
''',
    'options.html': r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FunPay Studio — расширение</title><link rel="stylesheet" href="ui.css"><script src="options.js" defer></script></head><body><main><div class="brand"><img src="icons/128.png" alt=""><div><div class="eyebrow">ВАШЕ РАБОЧЕЕ МЕСТО</div><h1>FunPay Studio</h1></div></div><p>Настройки этого браузера. Фон и аудиофайлы сохраняются на устройстве.</p><div id="message" class="message" role="status"></div><section class="card"><h2>Подключение к вашей панели</h2><label>HTTPS-адрес сервера Render<input id="baseURL" type="url" placeholder="https://your-studio.onrender.com" autocomplete="off"></label><label>Одноразовый код из панели → Расширение<input id="pairCode" placeholder="Код действует 10 минут" autocomplete="off"></label><div class="row"><button id="pair" class="primary">Подключить</button><button id="panel">Открыть панель ↗</button><button id="disconnect">Отключить</button></div><p id="connection" class="note">Проверяем подключение…</p></section><div class="grid"><section class="card"><h2>Ваш фон FunPay</h2><div class="preview" id="preview"><span>Предпросмотр фона</span></div><label><input type="checkbox" id="themeEnabled">Изменить фон и цвет кнопок</label><label>Фон — PNG, JPEG или WebP, до 4 МБ<input id="backgroundFile" type="file" accept="image/png,image/jpeg,image/webp"></label><button id="clearBackground">Убрать картинку</button><div class="row" style="margin-top:20px"><label>Цвет фона<input type="color" id="backgroundColor" value="#dce5f1"></label><label>Акцент<input type="color" id="accent" value="#4580ff"></label></div><label>Затемнение <output id="overlayValue"></output><input id="overlay" type="range" min="0" max="80" value="15"></label><p class="note">Изменение видно только вам. Аватар и профиль продавца остаются настройками FunPay.</p></section><section class="card"><h2>Звуки и уведомления</h2><label><input type="checkbox" id="soundEnabled">Воспроизводить свои звуки</label><label><input type="checkbox" id="notifications">Показывать уведомления браузера</label><label><input type="checkbox" id="muteOriginal">Отключить оригинальные звуки вкладки FunPay</label><p class="note">Отключение действует на весь звук вкладки. При потере связи с сервером исходный звук возвращается.</p><label>Новое сообщение — аудио до 2 МБ<input id="messageFile" type="file" accept="audio/*"></label><small id="messageFileInfo"></small><label style="margin-top:18px">Заказ / важное событие — аудио до 2 МБ<input id="orderFile" type="file" accept="audio/*"></label><small id="orderFileInfo"></small><label style="margin-top:18px">Громкость <output id="volumeValue"></output><input type="range" id="volume" min="0" max="100" value="65"></label><div class="row"><button id="testMessage">Проверить сообщение</button><button id="testOrder">Проверить заказ</button></div><button id="clearSounds" style="margin-top:12px">Вернуть стандартные звуки Studio</button></section></div><div class="sticky"><button id="save" class="primary full">Сохранить оформление и звуки</button><p class="note">С открытым FunPay обновления проверяются примерно каждые 5 секунд. При закрытой вкладке — примерно раз в минуту, пока работает браузер. Серверная автоматизация от этого не зависит.</p></div></main></body></html>
''',
    'options.js': r''''use strict';
const $=s=>document.querySelector(s);let cfg={};
const send=async m=>{const r=await chrome.runtime.sendMessage(m);if(!r?.ok)throw Error(r?.error||'Операция не выполнена.');return r.result;};
function notice(t,error=false){$('#message').textContent=t;$('#message').className='message visible'+(error?' error':'');}
function collect(){for(const k of ['themeEnabled','soundEnabled','notifications','muteOriginal'])cfg[k]=$('#'+k).checked;for(const k of ['backgroundColor','accent'])cfg[k]=$('#'+k).value;cfg.overlay=+$('#overlay').value;cfg.volume=+$('#volume').value/100;return cfg;}
function preview(){collect();$('#overlayValue').textContent=cfg.overlay+'%';$('#volumeValue').textContent=Math.round(cfg.volume*100)+'%';$('#preview').style.backgroundColor=cfg.backgroundColor;$('#preview').style.backgroundImage=cfg.background?`linear-gradient(rgba(0,0,0,${cfg.overlay/100}),rgba(0,0,0,${cfg.overlay/100})),url("${cfg.background}")`:'none';$('#messageFileInfo').textContent=cfg.messageSound?'Загружен свой звук':'Стандартный звук Studio';$('#orderFileInfo').textContent=cfg.orderSound?'Загружен свой звук':'Стандартный звук Studio';}
async function file(input,type,key,limit){const f=input.files[0];if(!f)return;if(!type.test(f.type)||f.size>limit)throw Error('Неверный формат или превышен размер файла.');cfg[key]=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(f);});preview();}
async function save(){collect();await send({type:'save',settings:cfg});}
function guarded(fn){return async()=>{try{await fn();}catch(e){notice(e.message,true);}};}
$('#backgroundFile').onchange=guarded(()=>file($('#backgroundFile'),/^image\/(png|jpeg|webp)$/,'background',4*1024*1024));
$('#messageFile').onchange=guarded(()=>file($('#messageFile'),/^audio\//,'messageSound',2*1024*1024));
$('#orderFile').onchange=guarded(()=>file($('#orderFile'),/^audio\//,'orderSound',2*1024*1024));
$('#clearBackground').onclick=()=>{cfg.background='';$('#backgroundFile').value='';preview();};
$('#clearSounds').onclick=()=>{cfg.messageSound='';cfg.orderSound='';$('#messageFile').value='';$('#orderFile').value='';preview();};
for(const key of ['overlay','volume','backgroundColor','accent'])$('#'+key).oninput=preview;
$('#save').onclick=guarded(async()=>{await save();notice('Настройки сохранены. Обновите FunPay, если страница была открыта до установки расширения.');});
$('#testMessage').onclick=guarded(async()=>{await save();await send({type:'test-sound',kind:'message'});notice('Звук сообщения воспроизведён.');});
$('#testOrder').onclick=guarded(async()=>{await save();await send({type:'test-sound',kind:'order'});notice('Звук заказа воспроизведён.');});
$('#panel').onclick=guarded(()=>send({type:'open-panel'}));
$('#disconnect').onclick=guarded(async()=>{await send({type:'disconnect'});notice('Расширение отключено. Для отзыва ключа удалите устройство в панели.');$('#connection').textContent='Не подключено';});
$('#pair').onclick=guarded(async()=>{
 const u=new URL($('#baseURL').value.trim());if(u.username||u.password||u.search||u.hash)throw Error('Укажите адрес без логина, пароля и параметров.');if(u.protocol!=='https:'&&!(u.protocol==='http:'&&['127.0.0.1','localhost'].includes(u.hostname)))throw Error('Нужен HTTPS-адрес вашего сервера.');
 // Permission request must stay directly in this user gesture.
 const granted=await chrome.permissions.request({origins:[u.origin+'/*']});if(!granted)throw Error('Браузер не предоставил доступ к этому серверу.');
 await send({type:'pair',baseURL:u.origin,code:$('#pairCode').value.trim()});$('#pairCode').value='';notice('Подключено.');$('#connection').textContent='Подключено к '+u.origin;
});
(async()=>{cfg={themeEnabled:false,background:'',backgroundColor:'#dce5f1',overlay:15,accent:'#4580ff',soundEnabled:true,notifications:true,muteOriginal:false,volume:.65,...(await chrome.storage.local.get('settings')).settings};for(const k of ['themeEnabled','soundEnabled','notifications','muteOriginal'])$('#'+k).checked=cfg[k];for(const k of ['baseURL','backgroundColor','accent','overlay'])$('#'+k).value=cfg[k]??'';$('#volume').value=cfg.volume*100;preview();const r=await send({type:'status'});$('#connection').textContent=r.server?'Подключено · '+(r.server.account?.username||'аккаунт FunPay ещё не подключён'):r.connectionError||'Не подключено';})().catch(e=>notice(e.message,true));
''',
    'popup.html': r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><link rel="stylesheet" href="ui.css"><script src="popup.js" defer></script></head><body class="popup"><div class="brand"><img src="icons/48.png" alt=""><div><b>FunPay Studio</b><p style="margin:3px 0 0">Ваш магазин, всегда рядом.</p></div></div><div class="status" id="status">Проверяем подключение…</div><button class="primary" id="panel">Открыть панель ↗</button><button id="settings">Настроить фон и звуки</button><button id="funpay">Открыть FunPay</button><p class="note">Заказы выполняет ваш сервер. Настройки внешнего вида действуют в этом браузере.</p></body></html>
''',
    'popup.js': r'''document.getElementById('settings').onclick=()=>chrome.runtime.openOptionsPage();
document.getElementById('funpay').onclick=()=>chrome.tabs.create({url:'https://funpay.com/'});
document.getElementById('panel').onclick=()=>chrome.runtime.sendMessage({type:'open-panel'});
chrome.runtime.sendMessage({type:'status'}).then(r=>{const s=r.result?.server;document.getElementById('status').textContent=s?`${s.connected?'● FunPay подключён':'○ FunPay не подключён'} · Склад: ${s.counts.stock} · Выдано: ${s.counts.delivered}`:r.result?.connectionError||r.error||'Подключите расширение в настройках.';}).catch(()=>{document.getElementById('status').textContent='Откройте настройки расширения.';});
''',
    'ui.css': r''':root{color-scheme:dark;font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#e6efff;background:#0c1421}*{box-sizing:border-box}body{margin:0}body.popup{width:330px;padding:24px}main{max-width:850px;padding:40px 24px;margin:auto}h1{font-size:30px;letter-spacing:-1px;margin:10px 0}h2{font-size:17px;margin:0 0 20px}p{line-height:1.6;color:#9cb0cc;font-size:12px}small{color:#8ca3c4;font-size:11px}.eyebrow{letter-spacing:.2em;font-size:10px;color:#91b5ee}.brand{display:flex;align-items:center;gap:13px;margin-bottom:22px}.brand img{width:45px;height:45px}.brand b{font-size:18px}.card{background:#131f31;border:1px solid #2c3d57;border-radius:14px;padding:25px;margin-top:20px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}.row{display:flex;gap:10px;align-items:center}.row>*{flex:1}label{display:block;font-size:12px;color:#b8cae4;margin-bottom:18px}input,select{font:inherit}input:not([type=checkbox]):not([type=range]){display:block;width:100%;border:1px solid #354c6e;border-radius:8px;background:#0c1627;color:#e4efff;padding:12px;margin-top:8px}input[type=color]{height:45px;padding:4px!important}input[type=range]{width:100%;accent-color:#528cff;margin-top:12px}input[type=checkbox]{accent-color:#528cff;margin-right:8px}input[type=file]{font-size:11px}button,a.button{font:inherit;background:#243854;border:1px solid #3a5377;color:#e4efff;border-radius:9px;padding:11px 16px;cursor:pointer;text-decoration:none;text-align:center;display:inline-block}button:hover{background:#34527b}button.primary{background:#437ffa;border-color:#437ffa}.full{width:100%;margin-top:10px}.message{padding:14px;border-radius:10px;background:#18362e;color:#b6ebd2;font-size:12px;line-height:1.5;margin:20px 0;display:none}.message.visible{display:block}.message.error{background:#422c31;color:#ffd4d4}.status{border:1px solid #30486b;border-radius:10px;padding:14px;margin:20px 0;font-size:12px;color:#abc8ef}.sticky{position:sticky;bottom:0;background:#0c1421ed;backdrop-filter:blur(10px);padding:20px 0;border-top:1px solid #2c3d57;margin-top:25px}.note{font-size:11px;line-height:1.6;color:#8ca3c4}.preview{height:150px;border:1px solid #38506f;background:#dce5f1;border-radius:12px;display:grid;place-content:center;color:#1b2d46;margin-bottom:20px;background-size:cover;background-position:center}.preview span{background:#ffffffdf;padding:10px 18px;border-radius:7px;font-size:12px}.popup .button,.popup button{width:100%;margin-top:9px}.popup .note{margin-bottom:0}@media(max-width:600px){.grid{grid-template-columns:1fr}main{padding:24px 16px}.card{padding:20px}.row{flex-wrap:wrap}}
''',
}


# Embedded extension resources
EXTENSION_BINARY = {
    'icons/128.png': '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',
    'icons/16.png': 'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACVklEQVR4nHWTvWsVQRTFf3d2dt++92JiFH2g4kdjIX4UAY1okcommEKwtfAvsBHzHxhIJfaWFlorgoURJSKYQklhwCIRlZgPE/S97L7dmbkWm5cPiQcuDDNzzz3nMCMAl8Y7Q3GSTvrSXRXUogjCNhS0txJxUWzflkV+9/1Ec0YujRdDUSTPjLWt0M3Vq4gquyACkYFqXzWqpRKc++m9jlpR/9DYtOXzzLlgbDOFxO4mKBx0cogtqIq4LHO2Xm8Flz+0gg6HItfCG3uqJTy4HdNXF1SVnpJ2DncelcwvKbUYMMb6PFdBhy1iRKSacuygcORAz/x2CPubcPKwMPdDSZNNKyICwpZYEShddajA12Xly2IgscJ6R/m0EGj0mndgl1uRzQI+zQcev/H0N6BbQjvbGeR/CHoICmMXI8YuRgA4D9fvd1nvKJERQti+a/Yi+BcKFF1Dp21xTpAdb2RPBSLwfMbz9J1noCG0O4ZDx9ucv9rh88cBVpZSkiSgugdBz+PCsjI1G+hLIk6f/cXNW9+I48C5oTWePDrF2kpCZMNuC1shCtQT2NeA/jpcGVkDYH01oX+w5MyFdYquwQgYNCiqKlKF9SerKisqNc4Lq8sJtVogrgWMUdZWE4wJCiDD97Jpm6aXyyxzsTW2UavUdEvoOtAgpHXPtRvfOXpig9kPg7x+cdilzZp13Wxq6zNF1rbKPNegVcYiYDbteCco0Gg62r+tpn2JBOcWKf2IeT+RzHhfjKqGV8YYZyPUmqq5F2pkFRsp2YbVWl1cCOVLyo2R6cl07i/hXhwCfxoSKAAAAABJRU5ErkJggg==',
    'icons/32.png': 'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAEsklEQVR4nL2XTWxUVRTHf+fe+97M9BNqLQSDH8GFLgwKYrAYWJCAERfGj0SjsmNr4sKgbqobEtwYdWfciDFqlJiYsFDDSklBMZGEiCRiFI1RDAU6bed93HePizfT0um0TCvhJHcyM+++e/7nf//nnHsFgDE1vC4B4MFX8z0iZi/4bQRdp6qACCsyVREBI3+BO6YaDo0fiI5c7VNaX7a+eHnI1nrfxphnxRiCL9DgV+a3zcQ4jLNoCBDCh0Vj+oXjb66aYEyNjI2pOZoyqDb7ylbj+7OpJIAiIrLyyNtNVVUVhLivaookOylFvGtnhSsCsHV/46O4v/p0NplkYiReEEGXMFS7mBM0iweqcVZPPj5+sPaMjL7c2CWu+mWRp4UIdp7j5ocvugNgTQn2WkBUKWxUseqT3S6o2ecMCtpyOevcBwgKQ33XXBARmEogzaESXQuEIgYt1OxzELYH70UEM38KGIFXnnDsvMcStPy9FIBf/wns/yDn0hQ4uzgIEUzwXiBsN8a4kVLtczttDMyksPEOw2MPWPprMNgD/bXOY6D5bOPthqdGHVOJLgq2BUGDxxg34lS9tstMgCLA+mEhaBnJ0guW843pTogtL6pe3WIaD6rcMiQYgaINYhE6AFBwArErt69LEOKWeryY+q1Z/L/vfwnETroGsSSATtxkHj4bL0iyOcpbW/Tjb4FjPwd6KxA6sLRsAFdbS+mNDA5+njNRh8iVaSqUtNciuKm/1E231jWAlsUW9myyTCVgW6nWLCGTM8pPfyjWdK+DrgG0tqNWgQPPRR3nnD4feP6tnN5K99mwbAaABRSHZgom2fLX6qDn62RdNrAVMdBelEyzhVUjEFFEhOAFEcVaRVUW3ZJlZ8FMCq99kjOdMCu2uWakUFjyXOnp83hvmK474jhgrHYEsWwGfIDxs4FL0/MbjhElTy33brnC5tGLDKzO8Lnh/Llevv16DcmMxUULQSwbgAB9tRJIC4ARSBqO+7ZN8PCTfxKC4HOhWivYtO0iw2tSDr9/G0VuEKvzcnRFIiyPds2hkKbCqpsTdjzyN3lmSBOLqlAUQv1yxK0bphjdeYEsMwu0+b+zwIiS54Y776rT0+vxucGYMkQRsFZJGpYNd9fp7c8JQeZlyJINVLUt2rB4ja/UinKlDumnCs4pURTavKk6ESeqfsGbqhBHZYExV/HUV2ufKYhA/XLU8TyolCU7aRiShptlpzx5O3Eh+AvGuhENcwcT1bLRnP9X+eFcKBtOk7lGVgqwVZpDgLgSOHt6kM0PXWT1cErSsKWjZv5XKp5TJ4ZIGpZqT4EGVTFOQuEvyNb96aeuEj/h0ySIiJ2LC/KiHPPjhUo8nwURyFLD2vUNHt/7O/2DOT4XxICLAie/GeboF+twUWiyq4WrVI1Ps8NLH8ulc0Xt1G7FKFliGRpJ2bhlguG1CWliOXdmgDOnBjFGEVPuiULhoooNebK7q4tJtyYCPhd8brBOCQFUhUqtmM191ZDHfbUorafvnXijuu+6X83Kt8r6X64DoRR/QJG4t2qKRnYyieMdj0Jywy6n4ixoQEN4109eeem7d4YnUW1GeMOu5/mh8QPVI61HiOh/aLN6q4aLbKAAAAAASUVORK5CYII=',
    'icons/48.png': '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',
    'message.wav': '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AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA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AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
    'order.wav': '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AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA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AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA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3v6U/m3+bv6W/uH+SP8AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=',
}


_CLI_ARGS = early_cli() if __name__ == '__main__' else None
sys.path.insert(0, str(VENDOR_DIR))


def extension_archive():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in EXTENSION_TEXT.items():
            archive.writestr('funpay-studio-extension/' + name, value.encode('utf-8'))
        for name, value in EXTENSION_BINARY.items():
            archive.writestr('funpay-studio-extension/' + name, base64.b64decode(value, validate=True))
    return output.getvalue()


# ======================================================================
# STORE
# ======================================================================
"""Single-owner durable store. Secrets never appear in event payloads."""
import base64
import hashlib
import hmac
import json
import sqlite3
import threading
import re
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet


DEFAULTS = {"enabled": False, "auto_delivery": False, "auto_reply": False,
            "review_bonus": False, "auto_raise": False, "poll_seconds": 8}


class Store:
    def __init__(self, path, master_key, database_url=None):
        self.path = str(path)
        self.database_url = database_url
        self.postgres = bool(database_url)
        self._mutex = threading.RLock()
        self._leader_mutex = threading.RLock()
        self._connection = None
        self._leader_connection = None
        self.leadership_required = False
        self.worker_ready = not self.postgres
        self.shutting_down = False
        if self.postgres:
            from urllib.parse import urlparse
            endpoint = urlparse(database_url)
            if endpoint.scheme not in ('postgres', 'postgresql'):
                raise ValueError('DATABASE_URL must start with postgresql://')
            if '-pooler' in (endpoint.hostname or ''):
                raise ValueError('Use the direct Neon connection: disable Connection pooling.')
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.key = hashlib.sha256(master_key.encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(self.key))
        with self.tx() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(kind TEXT, id TEXT, data TEXT NOT NULL,
                PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS stock(id INTEGER PRIMARY KEY, product_id TEXT NOT NULL,
                secret TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, state TEXT NOT NULL DEFAULT 'available',
                order_id TEXT, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS stock_available ON stock(product_id,state);
            CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, product_id TEXT, buyer_id INTEGER,
                buyer TEXT, chat_id TEXT, quantity INTEGER, status TEXT NOT NULL, fp_status TEXT,
                created REAL, delivered REAL, expiry REAL, review_applied INTEGER DEFAULT 0,
                bonus_hours INTEGER DEFAULT 0, message_secret TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,title TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY,at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,csrf TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS pairs(hash TEXT PRIMARY KEY,expires REAL);
            CREATE TABLE IF NOT EXISTS devices(hash TEXT PRIMARY KEY,name TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,status TEXT,
                next_run REAL,attempts INTEGER DEFAULT 0, result TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS boosts(category_id INTEGER PRIMARY KEY,name TEXT,
                interval_minutes INTEGER DEFAULT 241,next_run REAL,enabled INTEGER DEFAULT 1);
            CREATE TABLE IF NOT EXISTS order_inbox(id TEXT PRIMARY KEY,next_check REAL);
            ''')
        if not self.postgres:
            self.recover_inflight()
        with self.tx() as c:
            existing = c.execute("SELECT value FROM kv WHERE key='key_check'").fetchone()
            if existing:
                if self.decrypt(json.loads(existing['value'])) != 'FunPay Studio':
                    raise RuntimeError('MASTER_KEY does not match the existing database')
            else:
                c.execute('INSERT INTO kv VALUES(?,?)',
                          ('key_check', json.dumps(self.encrypt('FunPay Studio'))))

    def _connect_postgres(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.database_url, autocommit=True, row_factory=dict_row,
                               connect_timeout=15, prepare_threshold=None,
                               application_name='funpay-studio', keepalives=1,
                               keepalives_idle=20, keepalives_interval=10, keepalives_count=3)

    @contextmanager
    def tx(self):
        if self.postgres:
            with self._mutex:
                if self._connection is None or self._connection.closed or self._connection.broken:
                    self._connection = self._connect_postgres()
                connection = self._connection
                # Serialize state transitions across processes; never retry an uncertain commit.
                with connection.transaction():
                    connection.execute("SET LOCAL statement_timeout = '20000'")
                    connection.execute('SELECT pg_advisory_xact_lock(728196001)')
                    yield PostgresConnection(connection)
            return
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def recover_inflight(self):
        with self.tx() as c:
            c.execute("UPDATE orders SET status='uncertain' WHERE status='sending'")
            c.execute("UPDATE jobs SET status=CASE WHEN kind='provider' THEN 'pending' ELSE 'uncertain' END WHERE status='running'")

    def try_leadership(self):
        if not self.postgres:
            self.worker_ready = not self.shutting_down
            return self.worker_ready
        with self._leader_mutex:
            if self.shutting_down:
                return False
            if self._leader_connection is not None:
                try:
                    self._leader_connection.execute('SELECT 1')
                    return self.worker_ready
                except Exception:
                    self._leader_connection.close()
                    self._leader_connection = None
                    self.worker_ready = False
                    raise
            connection = self._connect_postgres()
            try:
                acquired = connection.execute('SELECT pg_try_advisory_lock(728196002) AS ok').fetchone()['ok']
                if not acquired:
                    connection.close()
                    return False
                self._leader_connection = connection
                self.recover_inflight()
                self.worker_ready = True
                return True
            except BaseException:
                connection.close()
                self._leader_connection = None
                self.worker_ready = False
                raise

    def require_leader(self):
        if not self.leadership_required:
            return
        if self.shutting_down or not self.worker_ready:
            raise RuntimeError('Automation worker is not active')
        if self.postgres:
            with self._leader_mutex:
                try:
                    self._leader_connection.execute('SELECT 1')
                except Exception:
                    self.worker_ready = False
                    raise RuntimeError('Database leadership lost; operation stopped') from None

    def close(self):
        self.worker_ready = False
        for attr in ('_connection', '_leader_connection'):
            connection = getattr(self, attr)
            if connection is not None:
                connection.close()
                setattr(self, attr, None)

    def query(self, sql, args=()):
        with self.tx() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.tx() as c:
            return c.execute(sql, args).rowcount

    def get(self, key, default=None):
        rows = self.query('SELECT value FROM kv WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set(self, key, value):
        self.execute('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return DEFAULTS | self.get('settings', {})

    def documents(self, kind):
        return [json.loads(r['data']) for r in self.query('SELECT data FROM documents WHERE kind=? ORDER BY id', (kind,))]

    def document(self, kind, ident):
        rows = self.query('SELECT data FROM documents WHERE kind=? AND id=?', (kind, ident))
        return json.loads(rows[0]['data']) if rows else None

    def save_document(self, kind, data):
        self.execute('INSERT INTO documents VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data',
                     (kind, data['id'], json.dumps(data, ensure_ascii=False)))

    def encrypt(self, text):
        return self.cipher.encrypt(text.encode()).decode()

    def decrypt(self, text):
        return self.cipher.decrypt(text.encode()).decode()

    def add_stock(self, product_id, items):
        added = 0
        with self.tx() as c:
            for item in items:
                item = item.strip()
                if not item:
                    continue
                fingerprint = hmac.new(self.key, item.encode(), hashlib.sha256).hexdigest()
                cur = c.execute('INSERT OR IGNORE INTO stock(product_id,secret,fingerprint,created) VALUES(?,?,?,?)',
                                (product_id, self.encrypt(item), fingerprint, time.time()))
                added += cur.rowcount
        return added

    def event(self, kind, title, detail=''):
        self.execute('INSERT INTO events(kind,title,detail,at) VALUES(?,?,?,?)',
                     (kind, title, str(detail)[:1500], time.time()))

    def once(self, key):
        return self.execute('INSERT OR IGNORE INTO seen VALUES(?,?)', (key, time.time())) == 1

    def job(self, ident, kind, payload, due=None):
        return self.execute('INSERT OR IGNORE INTO jobs(id,kind,payload,status,next_run) VALUES(?,?,?,\'pending\',?)',
                            (ident, kind, json.dumps(payload, ensure_ascii=False), due or time.time()))

    def backup_data(self):
        # Consistent logical export; all secret columns remain encrypted.
        with self.tx() as c:
            tables = {table: [dict(row) for row in c.execute('SELECT * FROM ' + table).fetchall()]
                      for table in BACKUP_TABLES}
        return json.dumps({'format': 'funpay-studio-backup', 'version': 1,
                           'created': time.time(), 'tables': tables}, ensure_ascii=False).encode()

    def backup(self, destination):
        Path(destination).write_bytes(self.backup_data())

    def restore_data(self, payload):
        backup = json.loads(payload)
        if backup.get('format') != 'funpay-studio-backup' or backup.get('version') != 1:
            raise ValueError('Unsupported backup format')
        tables = backup.get('tables', {})
        if set(tables) != set(BACKUP_TABLES):
            raise ValueError('Incomplete backup')
        check = next((json.loads(row['value']) for row in tables['kv'] if row['key'] == 'key_check'), '')
        if not check or self.decrypt(check) != 'FunPay Studio':
            raise ValueError('Use the original MASTER_KEY for this backup')
        for row in tables['stock']:
            self.decrypt(row['secret'])
        with self.tx() as c:
            for table in BACKUP_TABLES:
                rows = c.execute('SELECT * FROM ' + table).fetchall()
                if rows and (table != 'kv' or any(row['key'] != 'key_check' for row in rows)):
                    raise ValueError('Restore requires an empty database. Existing data was not changed.')
            c.execute('DELETE FROM kv')
            for table in BACKUP_TABLES:
                if table in ('sessions', 'pairs', 'devices'):
                    continue  # Do not resurrect old admin sessions or extension access.
                for row in tables[table]:
                    if not isinstance(row, dict) or set(row) != set(BACKUP_TABLES[table]):
                        raise ValueError('Invalid backup columns for ' + table)
                    columns = BACKUP_TABLES[table]
                    c.execute('INSERT INTO ' + table + '(' + ','.join(columns) + ') VALUES(' +
                              ','.join('?' for _ in columns) + ')', tuple(row[k] for k in columns))
            if self.postgres:
                for table, column in (('stock', 'id'), ('events', 'seq')):
                    c.execute("SELECT setval(pg_get_serial_sequence('" + table + "','" + column + "'), "
                              "coalesce((SELECT max(" + column + ") FROM " + table + "),0)+1, false)")
            found = c.execute("SELECT value FROM kv WHERE key='settings'").fetchone()
            settings = json.loads(found['value']) if found else dict(DEFAULTS)
            settings['enabled'] = False
            c.execute('INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                      ('settings', json.dumps(settings)))
            # Require a deliberate reconfiguration before any external side effects.
            c.execute("UPDATE jobs SET status='uncertain' WHERE status IN ('pending','running')")
            c.execute("UPDATE orders SET status='uncertain' WHERE status='sending'")
        return sum(len(rows) for rows in tables.values())


BACKUP_TABLES = {
    'kv': ('key','value'),
    'documents': ('kind','id','data'),
    'stock': ('id','product_id','secret','fingerprint','state','order_id','created'),
    'orders': ('id','product_id','buyer_id','buyer','chat_id','quantity','status','fp_status','created','delivered','expiry','review_applied','bonus_hours','message_secret'),
    'events': ('seq','kind','title','detail','at'),
    'seen': ('key','at'),
    'sessions': ('hash','csrf','expires'),
    'pairs': ('hash','expires'),
    'devices': ('hash','name','created'),
    'jobs': ('id','kind','payload','status','next_run','attempts','result'),
    'boosts': ('category_id','name','interval_minutes','next_run','enabled'),
    'order_inbox': ('id','next_check'),
}


class PostgresConnection:
    """Translate only the small, static SQL subset used by this application."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, args=()):
        if sql.lstrip().upper().startswith('INSERT OR IGNORE INTO '):
            sql = re.sub('INSERT OR IGNORE INTO', 'INSERT INTO', sql, count=1, flags=re.I)
            sql = sql.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        # Keep SQLite's integer flag convention without psycopg boolean comparisons.
        values = tuple(int(v) if isinstance(v, bool) else v for v in args)
        if not values:
            return self.connection.execute(sql)
        sql = sql.replace('%', '%%').replace('?', '%s')
        return self.connection.execute(sql, values)

    def executescript(self, sql):
        for statement in sql.split(';'):
            statement = statement.strip()
            if not statement or statement.startswith('PRAGMA'):
                continue
            statement = statement.replace('id INTEGER PRIMARY KEY, product_id', 'id BIGSERIAL PRIMARY KEY, product_id')
            statement = statement.replace('seq INTEGER PRIMARY KEY AUTOINCREMENT', 'seq BIGSERIAL PRIMARY KEY')
            statement = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', statement)
            statement = statement.replace('buyer_id INTEGER', 'buyer_id BIGINT')
            self.connection.execute(statement)


# ======================================================================
# MODELS
# ======================================================================
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Product(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=150)
    match_title: str = Field(min_length=1, max_length=1000)
    subcategory_id: int = Field(gt=0)
    enabled: bool = True
    access_hours: int = Field(default=0, ge=0, le=87600)
    bonus_hours: int = Field(default=0, ge=0, le=8760)
    delivery_text: str = Field(default='Спасибо за заказ, {buyer}!\n\n{items}', min_length=1, max_length=5000)

    @model_validator(mode='after')
    def includes_items(self):
        if '{items}' not in self.delivery_text:
            raise ValueError('Текст выдачи должен содержать {items}.')
        if self.bonus_hours and not self.access_hours:
            raise ValueError('Для бонуса задайте основной срок доступа.')
        return self


class Rule(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=150)
    enabled: bool = True
    mode: Literal['exact', 'contains', 'any'] = 'contains'
    keywords: list[str] = Field(default_factory=list, max_length=50)
    reply: str = Field(min_length=1, max_length=4000)
    cooldown_seconds: int = Field(default=300, ge=60, le=86400)


class Template(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=150)
    title: str = Field(min_length=1, max_length=1000)
    description: str = Field(default='', max_length=20000)
    price: float = Field(gt=0, le=10000000, allow_inf_nan=False)
    amount: int = Field(default=1, ge=1, le=1000000)
    active: bool = True
    target_id: int = Field(default=0, ge=0)
    fields: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def clean_fields(self):
        if len(self.fields) > 150 or sum(len(k) + len(v) for k, v in self.fields.items()) > 70000:
            raise ValueError('Слишком большой шаблон.')
        allowed = ('fields[',)
        scalars = {'node_id', 'server_id', 'side_id', 'offer_id', 'amount', 'price', 'active', 'deactivate_after_sale'}
        self.fields = {k: v for k, v in self.fields.items() if k in scalars or k.startswith(allowed)}
        return self


class Settings(Strict):
    enabled: bool
    auto_delivery: bool
    auto_reply: bool
    review_bonus: bool
    auto_raise: bool
    poll_seconds: int = Field(default=8, ge=6, le=60)


class Boost(Strict):
    category_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    interval_minutes: int = Field(default=241, ge=240, le=10080)
    enabled: bool = True


class StockInput(Strict):
    items: list[str] = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def limits(self):
        if any(not i.strip() or len(i) > 10000 for i in self.items) or sum(map(len, self.items)) > 2000000:
            raise ValueError('Лимит: 2000 записей, 10000 символов на запись, 2 МБ всего.')
        return self


class Login(Strict):
    password: str = Field(min_length=1, max_length=500)


class Credentials(Strict):
    golden_key: str = Field(min_length=16, max_length=500)
    user_agent: str = Field(default='', max_length=1000)


class Pair(Strict):
    code: str = Field(min_length=1, max_length=100)
    name: str = Field(default='Chrome', max_length=60)


class Publish(Strict):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,80}$')
    due: float | None = Field(default=None, allow_inf_nan=False)


class Reconcile(Strict):
    resend: bool = False


# ======================================================================
# ADAPTER
# ======================================================================
"""Adapter for the exact upstream snapshot in scripts/install_funpay.py.

Never infer payment or reviews from chat text. Mutating HTTP requests do not
retry at the transport layer, so an ambiguous timeout reaches the operator.
"""
import sys
import threading
from pathlib import Path




class FunPayAdapter:
    def __init__(self):
        self.lock = threading.RLock()
        self.account = None
        self.runner = None

    def connect(self, key, user_agent):
        from FunPayAPI import Account, Runner
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        with self.lock:
            account = Account(key, user_agent=user_agent or None, requests_timeout=20, locale='ru')
            # Upstream retries POST by default. Disable ALL automatic HTTP retries.
            account.session.mount('https://', HTTPAdapter(max_retries=Retry(total=0)))
            account.get()
            self.account = account
            self.runner = Runner(account)
            return {'id': account.id, 'username': account.username}

    @property
    def user_id(self):
        return self.account.id if self.account else None

    def poll(self):
        with self.lock:
            raw = self.runner.get_updates()
            if not isinstance(raw, dict) or not isinstance(raw.get('objects'), list):
                raise RuntimeError('FunPay event format changed')
            return self.runner.parse_updates(raw['objects'])

    def order(self, ident):
        with self.lock:
            o = self.account.get_order(ident)
            return dict(id=o.id, status=o.status.name, seller_id=o.seller_id,
                        buyer_id=o.buyer_id, buyer=o.buyer_username or str(o.buyer_id),
                        chat_id=str(o.chat_id), quantity=o.amount,
                        title=o.short_description or '',
                        subcategory_id=o.subcategory.id if o.subcategory else None,
                        native_delivered=bool(o.order_secrets), review=bool(o.review and o.review.stars))

    def send(self, chat_id, text):
        with self.lock:
            cid = int(chat_id) if str(chat_id).isdigit() else chat_id
            return self.account.send_message(cid, text)

    def was_sent(self, chat_id, text):
        with self.lock:
            cid = int(chat_id) if str(chat_id).isdigit() else chat_id
            messages = self.account.get_chat_history(cid)
            return any(m.author_id == self.user_id and (m.text or '').strip() == text.strip() for m in messages)

    def lots(self):
        with self.lock:
            return [dict(id=int(o.id),title=o.description or '',price=o.price,
                         subcategory_id=o.subcategory.id,category_id=o.subcategory.category.id,
                         category=o.subcategory.category.name, native_auto=bool(o.auto_delivery))
                    for o in self.account.get_user(self.user_id).get_common_lots()]

    def lot_fields(self, source_id):
        with self.lock:
            fields = dict(self.account.get_lot_fields(int(source_id)).fields)
            for key in ('csrf_token', 'secrets', 'auto_delivery', 'deleted'):
                fields.pop(key, None)
            return {k: str(v) for k, v in fields.items() if v is not None}

    def publish(self, data):
        from FunPayAPI import types
        with self.lock:
            fields = dict(data['fields'])
            for k in ('deleted', 'secrets', 'csrf_token', 'auto_delivery'):
                fields.pop(k, None)
            target = int(data.get('target_id') or 0)
            fields.update({'fields[summary][ru]': data['title'],
                           'fields[desc][ru]': data['description'], 'price': str(data['price']),
                           'amount': str(data['amount']), 'active': 'on' if data['active'] else '',
                           'offer_id': str(target), 'auto_delivery': '', 'secrets': '',
                           'csrf_token': self.account.csrf_token})
            if not fields.get('node_id'):
                raise ValueError('Import a native offer template with node_id first')
            lot = types.LotFields(target, fields)
            self.account.save_lot(lot)

    def raise_lots(self, category_id):
        with self.lock:
            return self.account.raise_lots(int(category_id))


# ======================================================================
# ENGINE
# ======================================================================
import json
import threading
import time
from datetime import datetime, timezone


def normalized(text):
    return ' '.join(text.casefold().split())


def expand(template, values):
    # No str.format: inventory/password braces must stay literal.
    import re
    return re.sub(r'\{([a-z_]+)\}', lambda m: str(values.get(m[1], m[0])), template)


class Engine:
    def __init__(self, store, adapter):
        self.s = store
        self.a = adapter
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.connected = False
        self.account = None
        self.last_poll = None
        self.error = None
        self.last_review = 0
        self.review_cursor = ''
        self.pending_cursor = ''
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='funpay-engine', daemon=True)
        self.thread.start()

    def connect(self):
        credential = self.s.get('funpay_key')
        if not credential:
            raise ValueError('Сначала подключите аккаунт FunPay в настройках.')
        self.account = self.a.connect(self.s.decrypt(credential), self.s.get('user_agent', ''))
        old_owner = self.s.get('owner_id')
        if old_owner and old_owner != self.account['id']:
            self.connected = False
            raise ValueError('Эта база закреплена за другим аккаунтом FunPay.')
        self.s.set('owner_id', self.account['id'])
        self.connected = True
        self.error = None

    def run(self):
        failures = 0
        while not self.stop.is_set():
            delay = 15
            try:
                cfg = self.s.settings()
                delay = cfg['poll_seconds']
                if cfg['enabled']:
                    with self.lock:
                        if not self.connected:
                            self.connect()
                        baseline_done = self.s.get('baseline_done', False)
                        for event in self.a.poll():
                            if self.stop.is_set():
                                break
                            try:
                                name = event.type.name
                                if name in ('NEW_ORDER', 'ORDER_STATUS_CHANGED'):
                                    self.accept_order(event.order.id)
                                elif name == 'INITIAL_ORDER':
                                    # First installation does not deliver historical paid orders.
                                    # The operator can explicitly process one from the panel.
                                    old = self.s.query('SELECT status FROM orders WHERE id=?', (event.order.id,))
                                    historical = self.s.query('SELECT key FROM seen WHERE key=?', ('historical:' + event.order.id,))
                                    if not old and baseline_done and not historical:
                                        self.accept_order(event.order.id)
                                    elif not old and self.s.once('historical:' + event.order.id):
                                        self.s.event('attention', 'Найден прежний заказ', event.order.id + ': проверьте вручную в разделе «Заказы».')
                                    elif old and old[0]['status'] in ('out_of_stock', 'pending'):
                                        self.accept_order(event.order.id)
                                elif name == 'NEW_MESSAGE':
                                    self.process_message(event.message)
                            except Exception as exc:
                                self.s.event('error', 'Событие требует проверки', type(exc).__name__)
                        if self.stop.is_set():
                            break
                        self.s.set('baseline_done', True)
                        self.tick()
                        self.last_poll = time.time()
                        self.error = None
                    failures = 0
            except Exception as exc:
                failures += 1
                self.error = type(exc).__name__
                self.connected = False
                delay = min(300, 15 * (2 ** min(failures, 5)))
                if failures == 1 or failures % 10 == 0:
                    try:
                        self.s.event('error', 'Нет связи с FunPay или базой', self.error + '. Проверьте подключение и журнал. Повтор с задержкой.')
                    except Exception:
                        pass
            self.stop.wait(delay)

    def accept_order(self, ident):
        # Persist the event before the network read. A failed read is retried.
        self.s.execute('INSERT OR IGNORE INTO order_inbox VALUES(?,?)', (ident, time.time()))
        self.process_order(ident)
        self.s.execute('DELETE FROM order_inbox WHERE id=?', (ident,))

    def process_order(self, ident):
        with self.lock:
            o = self.a.order(ident)
            if o['seller_id'] != self.a.user_id:
                raise ValueError('Not a sale of this account')
            existing = self.s.query('SELECT * FROM orders WHERE id=?', (ident,))
            if existing:
                self.s.execute('UPDATE orders SET fp_status=? WHERE id=?', (o['status'], ident))
                if o['status'] in ('REFUNDED', 'PARTIALLY_REFUNDED'):
                    # Issued or reserved credentials must never return to available stock.
                    self.s.execute("UPDATE orders SET status='refunded' WHERE id=?", (ident,))
                    if self.s.once('refund:' + ident):
                        self.s.event('attention', 'Возврат: проверьте доступ', ident)
                    return
                if existing[0]['status'] not in ('pending', 'out_of_stock'):
                    return
            if o['status'] != 'PAID' or o['native_delivered']:
                return
            matches = [p for p in self.s.documents('products') if p['enabled'] and
                       p['subcategory_id'] == o['subcategory_id'] and
                       normalized(p['match_title']) == normalized(o['title'])]
            if len(matches) != 1:
                if self.s.once('unmatched:' + ident):
                    self.s.event('attention', 'Заказ без однозначного правила', ident + ': ' + o['title'])
                return
            p = matches[0]
            quantity = o['quantity']
            if not isinstance(quantity, int) or not 1 <= quantity <= 100:
                raise ValueError('Unknown or excessive order quantity')
            now = time.time()
            with self.s.tx() as c:
                c.execute('INSERT OR IGNORE INTO orders(id,product_id,buyer_id,buyer,chat_id,quantity,status,fp_status,created) VALUES(?,?,?,?,?,?,\'pending\',?,?)',
                          (ident, p['id'], o['buyer_id'], o['buyer'], o['chat_id'], quantity, o['status'], now))
            if not self.s.settings()['auto_delivery']:
                return
            # Atomic all-or-nothing reservation, including N-item orders.
            with self.s.tx() as c:
                state = c.execute('SELECT status FROM orders WHERE id=?', (ident,)).fetchone()['status']
                if state not in ('pending', 'out_of_stock'):
                    return
                rows = c.execute("SELECT * FROM stock WHERE product_id=? AND state='available' ORDER BY id LIMIT ?", (p['id'], quantity)).fetchall()
                if len(rows) != quantity:
                    c.execute("UPDATE orders SET status='out_of_stock' WHERE id=?", (ident,))
                    no_stock = True
                else:
                    no_stock = False
                    items = '\n\n'.join(self.s.decrypt(r['secret']) for r in rows)
                    message = expand(p['delivery_text'], dict(items=items, buyer=o['buyer'], order_id=ident,
                                                            product=p['name'], quantity=quantity))
                    message += '\n\nВыдача №' + ident
                    if len(message) > 18000:
                        raise ValueError('Delivery too long; use smaller stock records')
                    for r in rows:
                        c.execute("UPDATE stock SET state='reserved',order_id=? WHERE id=?", (ident, r['id']))
                    c.execute("UPDATE orders SET status='sending',message_secret=? WHERE id=?", (self.s.encrypt(message), ident))
            if no_stock:
                if self.s.once('empty:' + ident):
                    self.s.event('attention', 'Закончился товар', p['name'] + ' · заказ ' + ident)
                return
            try:
                self.a.send(o['chat_id'], message)
            except Exception:
                self.s.execute("UPDATE orders SET status='uncertain' WHERE id=?", (ident,))
                self.s.event('attention', 'Проверьте отправку заказа', ident + ': товар зарезервирован; автоматического повтора не будет.')
                return
            self.finish_delivery(ident, p)

    def finish_delivery(self, ident, product=None):
        now = time.time()
        saved = self.s.query('SELECT * FROM orders WHERE id=?', (ident,))[0]
        p = product or self.s.document('products', saved['product_id'])
        with self.s.tx() as c:
            o = c.execute('SELECT * FROM orders WHERE id=?', (ident,)).fetchone()
            if o['status'] == 'delivered':
                return
            expiry = now + p['access_hours'] * 3600 if p and p['access_hours'] else None
            c.execute("UPDATE orders SET status='delivered',delivered=?,expiry=? WHERE id=?", (now, expiry, ident))
            c.execute("UPDATE stock SET state='delivered' WHERE order_id=?", (ident,))
        self.s.event('delivery', 'Заказ выдан', ident + ' · ' + (p['name'] if p else 'Товар'))
        if expiry:
            self.s.job('access-start:' + ident, 'provider', dict(event_id='access-start:' + ident,
                       event='access.started', order_id=ident, buyer_id=o['buyer_id'], product_id=o['product_id'], expires_at=expiry))

    def reconcile(self, ident, resend=False):
        with self.lock:
            rows = self.s.query('SELECT * FROM orders WHERE id=?', (ident,))
            if not rows or rows[0]['status'] != 'uncertain':
                raise ValueError('Заказ не ожидает проверки.')
            row = rows[0]
            o = self.a.order(ident)
            if o['seller_id'] != self.a.user_id or o['status'] in ('REFUNDED', 'PARTIALLY_REFUNDED') or o['chat_id'] != row['chat_id']:
                raise ValueError('Заказ изменился; требуется ручная проверка на FunPay.')
            message = self.s.decrypt(row['message_secret'])
            if self.a.was_sent(row['chat_id'], message):
                self.finish_delivery(ident)
                return 'delivered'
            if not resend:
                return 'not_found'
            if o['status'] != 'PAID':
                raise ValueError('Повтор разрешён только для оплаченного открытого заказа.')
            # Only the operator can request this. Reuse EXACTLY the same reserved stock.
            self.a.send(row['chat_id'], message)
            self.finish_delivery(ident)
            return 'delivered'

    def process_message(self, m):
        if not m.author_id or m.author_id == self.a.user_id or not m.text:
            return
        if not self.s.once('message:' + str(m.chat_id) + ':' + str(m.id)):
            return
        self.s.event('message', 'Новое сообщение', m.chat_name or 'Чат FunPay')
        if not self.s.settings()['auto_reply']:
            return
        for rule in self.s.documents('rules'):
            if not rule['enabled']:
                continue
            text = normalized(m.text)
            matched = rule['mode'] == 'any' or any(
                normalized(w) == text if rule['mode'] == 'exact' else normalized(w) in text
                for w in rule['keywords'] if w.strip())
            if not matched:
                continue
            key = 'cooldown:' + rule['id'] + ':' + str(m.chat_id)
            last = self.s.get(key, 0)
            if time.time() - last < rule['cooldown_seconds']:
                continue
            # Persist before sending: a crash cannot create a reply loop.
            self.s.set(key, time.time())
            self.a.send(str(m.chat_id), expand(rule['reply'], {'buyer': m.chat_name or 'покупатель'}))
            break

    def review(self, ident):
        if not self.s.settings()['review_bonus']:
            return
        rows = self.s.query("SELECT * FROM orders WHERE id=? AND status='delivered' AND review_applied=0", (ident,))
        if not rows:
            return
        row = rows[0]
        p = self.s.document('products', row['product_id'])
        if not p or not p['bonus_hours'] or not row['expiry']:
            return
        o = self.a.order(ident)
        if o['seller_id'] != self.a.user_id or o['status'] != 'CLOSED' or not o['review']:
            return
        with self.s.tx() as c:
            fresh = c.execute('SELECT * FROM orders WHERE id=?', (ident,)).fetchone()
            if fresh['review_applied']:
                return
            expiry = max(fresh['expiry'], time.time()) + p['bonus_hours'] * 3600
            c.execute('UPDATE orders SET expiry=?,review_applied=1,bonus_hours=? WHERE id=?', (expiry, p['bonus_hours'], ident))
            payload = dict(event_id='review:' + ident, event='access.extended', order_id=ident,
                           buyer_id=row['buyer_id'], product_id=p['id'], hours=p['bonus_hours'], expires_at=expiry)
            c.execute('INSERT OR IGNORE INTO jobs(id,kind,payload,status,next_run) VALUES(?,?,?,\'pending\',?)',
                      ('review:' + ident, 'provider', json.dumps(payload), time.time()))
        self.s.event('bonus', 'Бонус за отзыв учтён', ident + ': +' + str(p['bonus_hours']) + ' ч. Статус применения — в разделе «Бонусы».')

    def tick(self):
        now = time.time()
        cfg = self.s.settings()
        for row in self.s.query('SELECT id FROM order_inbox WHERE next_check<=? ORDER BY next_check LIMIT 5', (now,)):
            try:
                self.process_order(row['id'])
                self.s.execute('DELETE FROM order_inbox WHERE id=?', (row['id'],))
            except Exception:
                self.s.execute('UPDATE order_inbox SET next_check=? WHERE id=?', (now + 60, row['id']))
        if cfg['auto_delivery']:
            pending = self.s.query("SELECT id FROM orders WHERE status IN ('pending','out_of_stock') AND id>? ORDER BY id LIMIT 10", (self.pending_cursor,))
            self.pending_cursor = pending[-1]['id'] if pending else ''
            for r in pending:
                self.process_order(r['id'])
        if cfg['review_bonus'] and now - self.last_review > 90:
            # Cursor avoids starving older orders when the seller has many sales.
            rows = self.s.query("SELECT id FROM orders WHERE status='delivered' AND review_applied=0 AND expiry IS NOT NULL AND id>? ORDER BY id LIMIT 5", (self.review_cursor,))
            self.review_cursor = rows[-1]['id'] if rows else ''
            for r in rows:
                self.review(r['id'])
            self.last_review = now
        if cfg['auto_raise']:
            for b in self.s.query('SELECT * FROM boosts WHERE enabled=1 AND next_run<=? ORDER BY next_run LIMIT 1', (now,)):
                try:
                    self.a.raise_lots(b['category_id'])
                    delay = b['interval_minutes'] * 60
                    self.s.event('raise', 'Предложения подняты', b['name'])
                except Exception as exc:
                    delay = max(60, int(getattr(exc, 'wait_time', 0) or 900)) + 5
                    self.s.event('attention', 'Поднятие отложено', b['name'] + ' · ' + str(delay) + ' сек. · ' + type(exc).__name__)
                self.s.execute('UPDATE boosts SET next_run=? WHERE category_id=?', (time.time() + delay, b['category_id']))
        for job in self.s.query("SELECT * FROM jobs WHERE kind='publish' AND status='pending' AND next_run<=? LIMIT 1", (now,)):
            self.s.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
            try:
                self.a.publish(json.loads(job['payload']))
                self.s.execute("UPDATE jobs SET status='done',result='FunPay принял сохранение. Проверьте список лотов.' WHERE id=?", (job['id'],))
                self.s.event('offer', 'Предложение опубликовано', json.loads(job['payload'])['title'])
            except Exception as exc:
                self.s.execute("UPDATE jobs SET status='uncertain',result=? WHERE id=?", (type(exc).__name__, job['id']))
                self.s.event('attention', 'Проверьте публикацию', 'Запрос не повторяется автоматически: ' + type(exc).__name__)


# ======================================================================
# INTEGRATIONS
# ======================================================================
import hashlib
import hmac
import json
import os
import threading
import time

import httpx


class Integrations:
    def __init__(self, store, public_url):
        self.s = store
        self.url = public_url.rstrip('/')
        self.stop = threading.Event()
        self.token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.owner = os.getenv('TELEGRAM_OWNER_ID', '')
        self.provider_url = os.getenv('ACCESS_WEBHOOK_URL', '')
        self.provider_secret = os.getenv('ACCESS_WEBHOOK_SECRET', '')
        self.client = httpx.Client(timeout=25, follow_redirects=False)
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='integrations', daemon=True)
        self.thread.start()

    def telegram(self, method, payload):
        self.s.require_leader()
        r = self.client.post('https://api.telegram.org/bot' + self.token + '/' + method, json=payload)
        r.raise_for_status()
        result = r.json()
        if not result.get('ok'):
            raise RuntimeError('Telegram rejected request')
        return result['result']

    def notify(self, text):
        self.telegram('sendMessage', {'chat_id': int(self.owner), 'text': text,
            'reply_markup': {'inline_keyboard': [
                [{'text': 'Открыть панель', 'url': self.url}],
                [{'text': 'Установить расширение', 'url': self.url + '/install'}]]}})

    def telegram_tick(self):
        if not self.token or not self.owner:
            return
        # Do not re-execute stale commands on first launch.
        offset = self.s.get('telegram_offset')
        initial = offset is None
        updates = self.telegram('getUpdates', {'offset': offset if offset is not None else 0,
                                             'timeout': 0, 'allowed_updates': ['message','callback_query','pre_checkout_query']})
        for item in updates:
            self.s.set('telegram_offset', item['update_id'] + 1)
            m = item.get('message', {})
            if getattr(self, 'nezzx', None) is not None and self.nezzx.telegram_update(self, item):
                continue
            if initial and time.time() - m.get('date', 0) > 60:
                continue
            if str(m.get('from', {}).get('id')) != self.owner or str(m.get('chat', {}).get('id')) != self.owner:
                continue
            cmd = (m.get('text') or '').split('@')[0].strip()
            cfg = self.s.settings()
            if cmd == '/pause':
                cfg['enabled'] = False
                self.s.set('settings', cfg)
                self.notify('FunPay Studio: автоматизация приостановлена.')
            elif cmd == '/resume':
                cfg['enabled'] = True
                self.s.set('settings', cfg)
                self.notify('FunPay Studio: автоматизация включена с сохранёнными правилами.')
            elif cmd in ('/start', '/panel', '/install', '/status'):
                count = self.s.query("SELECT count(*) AS n FROM stock WHERE state='available'")[0]['n']
                self.notify('FunPay Studio\nАвтоматизация: ' + ('включена' if cfg['enabled'] else 'пауза') +
                            '\nНа складе: ' + str(count) + '\nКоманды: /status /panel /install /pause /resume')
        if initial and not updates:
            self.s.set('telegram_offset', 0)
        cursor = self.s.get('telegram_event_cursor')
        if cursor is None:
            cursor = self.s.query('SELECT coalesce(max(seq),0) n FROM events')[0]['n']
            self.s.set('telegram_event_cursor', cursor)
        for ev in self.s.query('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 10', (cursor,)):
            # Persist before send: Telegram has no idempotency key. Prefer no duplicate notices.
            self.s.set('telegram_event_cursor', ev['seq'])
            if ev['kind'] in ('delivery', 'attention', 'bonus', 'message', 'error'):
                self.notify(ev['title'] + '\n' + ev['detail'])

    def provider_tick(self):
        if not self.provider_url or not self.provider_secret:
            return
        if not self.provider_url.startswith('https://'):
            return
        rows = self.s.query("SELECT * FROM jobs WHERE kind='provider' AND status='pending' AND next_run<=? ORDER BY next_run LIMIT 1", (time.time(),))
        for job in rows:
            payload = json.loads(job['payload'])
            order = self.s.query('SELECT expiry,status FROM orders WHERE id=?', (payload['order_id'],))
            if not order or order[0]['status'] == 'refunded':
                self.s.execute("UPDATE jobs SET status='cancelled',result='Заказ возвращён' WHERE id=?", (job['id'],))
                continue
            # A delayed start/bonus event never restores an older expiry.
            payload['expires_at'] = order[0]['expiry']
            data = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()
            timestamp = str(int(time.time()))
            signature = hmac.new(self.provider_secret.encode(), timestamp.encode() + b'.' + data, hashlib.sha256).hexdigest()
            self.s.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
            try:
                self.s.require_leader()
                response = self.client.post(self.provider_url, content=data, headers={
                    'Content-Type': 'application/json', 'X-FPS-Timestamp': timestamp,
                    'X-FPS-Signature': signature, 'Idempotency-Key': payload['event_id']})
                response.raise_for_status()
                ack = response.json()
                if ack.get('applied') is not True or ack.get('event_id') != payload['event_id']:
                    raise ValueError('Provider did not confirm application')
                self.s.execute("UPDATE jobs SET status='done',result='Провайдер подтвердил применение' WHERE id=?", (job['id'],))
                self.s.event('provider', 'Доступ обновлён провайдером', payload['order_id'])
            except Exception as exc:
                attempts = job['attempts'] + 1
                self.s.execute("UPDATE jobs SET status='pending',attempts=?,next_run=?,result=? WHERE id=?",
                               (attempts, time.time() + min(3600, 30 * 2 ** min(attempts, 7)), type(exc).__name__, job['id']))

    def run(self):
        while not self.stop.is_set():
            try:
                self.telegram_tick()
            except Exception as exc:
                bucket = str(int(time.time() // 3600))
                try:
                    if self.s.once('telegram-error:' + bucket):
                        self.s.event('integration', 'Telegram недоступен', type(exc).__name__)
                except Exception:
                    pass  # A database outage must not kill the notification worker.
            try:
                self.provider_tick()
            except Exception:
                pass
            self.stop.wait(3)


# ======================================================================
# WORKER
# ======================================================================
"""One active automation worker across Render's overlapping deployments."""
import threading


class GuardedAdapter:
    def __init__(self, adapter, store):
        self.adapter, self.store = adapter, store

    def __getattr__(self, name):
        value = getattr(self.adapter, name)
        if not callable(value):
            return value

        def guarded(*args, **kwargs):
            self.store.require_leader()
            return value(*args, **kwargs)
        return guarded


class WorkerController:
    def __init__(self, store, engine, integrations):
        self.store, self.engine, self.integrations = store, engine, integrations
        self.stop = threading.Event()
        self.thread = None
        self.started = False

    def start(self):
        self.store.leadership_required = True
        self.thread = threading.Thread(target=self.run, name='worker-supervisor', daemon=True)
        self.thread.start()

    def workers_stopped(self):
        threads = [self.engine.thread, self.integrations.thread]
        return all(t is None or not t.is_alive() for t in threads)

    def stop_workers(self):
        self.engine.stop.set()
        self.integrations.stop.set()

    def run(self):
        while not self.stop.is_set():
            try:
                if self.started:
                    self.store.require_leader()
                    if not self.engine.thread.is_alive() or not self.integrations.thread.is_alive():
                        raise RuntimeError('Background worker stopped')
                elif self.workers_stopped() and self.store.try_leadership():
                    self.engine.connected = False
                    self.engine.stop.clear()
                    self.integrations.stop.clear()
                    self.engine.start()
                    self.integrations.start()
                    self.started = True
            except Exception:
                self.store.worker_ready = False
                self.engine.error = 'Ожидание подключения к базе данных'
                self.stop_workers()
                if self.workers_stopped():
                    self.store.close()
                    self.started = False
            self.stop.wait(3)

    def shutdown(self):
        self.store.shutting_down = True
        self.stop.set()
        self.stop_workers()
        if self.thread:
            self.thread.join(timeout=5)
        for thread in (self.engine.thread, self.integrations.thread):
            if thread:
                thread.join(timeout=30)
        if self.workers_stopped():
            self.store.close()
            self.integrations.client.close()


# ======================================================================
# MAIN
# ======================================================================
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse


ROOT = BASE_DIR


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create_app(data_dir=None, master_key=None, password=None, background=True, adapter=None, database_url=None):
    password = password or os.getenv('ADMIN_PASSWORD', '')
    master_key = master_key or os.getenv('MASTER_KEY', '')
    if len(password) < 16 or len(master_key) < 32 or password.startswith('replace-with-') or master_key.startswith('replace-with-'):
        raise RuntimeError('Set ADMIN_PASSWORD (16+ chars) and MASTER_KEY (32+ chars). Run: python bot.py --help')
    data_dir = Path(data_dir or os.getenv('DATA_DIR', str(BASE_DIR / 'data')))
    public_url = (os.getenv('PUBLIC_URL') or os.getenv('RENDER_EXTERNAL_URL') or 'http://127.0.0.1:8000').rstrip('/')
    database_url = database_url or os.getenv('DATABASE_URL')
    if os.getenv('RENDER') and not database_url:
        raise RuntimeError('DATABASE_URL is required on Render. Use a direct PostgreSQL connection, e.g. Neon Free.')
    store = Store(data_dir / 'studio.sqlite3', master_key, database_url=database_url)
    selected_adapter = adapter or FunPayAdapter()
    engine = Engine(store, GuardedAdapter(selected_adapter, store) if background else selected_adapter)
    integrations = Integrations(store, public_url)
    controller = WorkerController(store, engine, integrations)
    nezzx = NezzxService(store, public_url)
    integrations.nezzx = nezzx
    limiter = {}
    limiter_lock = threading.Lock()
    # The upstream library may log full chats at DEBUG. Suppress its internal logger.
    logging.getLogger('FunPayAPI').setLevel(logging.CRITICAL)

    @asynccontextmanager
    async def lifespan(app):
        if background:
            controller.start()
        try:
            yield
        finally:
            if background:
                await asyncio.to_thread(controller.shutdown)
            else:
                store.close()
                integrations.client.close()

    app = FastAPI(title='FunPay Studio', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.engine = store, engine

    @app.middleware('http')
    async def protect(request, call_next):
        if int(request.headers.get('content-length', '0') or 0) > (6500000 if request.url.path == '/api/nx/music' else 2500000):
            return JSONResponse({'detail': 'Слишком большой запрос.'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') else 'no-cache'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def rate(request, name, limit=10):
        key = (request.client.host if request.client else 'local', name)
        with limiter_lock:
            now = time.time()
            entries = [n for n in limiter.get(key, []) if now - n < 600]
            if len(entries) >= limit:
                raise HTTPException(429, 'Слишком много попыток. Подождите 10 минут.')
            limiter[key] = entries + [now]
            if len(limiter) > 10000:
                for k in list(limiter):
                    if not limiter[k] or now - limiter[k][-1] > 600:
                        limiter.pop(k, None)

    def auth(request: Request):
        authorization = request.headers.get('authorization', '')
        if authorization.startswith('Bearer '):
            token = authorization[7:]
            if not store.query('SELECT hash FROM devices WHERE hash=?', (digest(token),)):
                raise HTTPException(401, 'Переподключите расширение.')
            allowed = request.method == 'GET' and request.url.path in ('/api/status', '/api/events', '/api/templates')
            allowed = allowed or request.method == 'POST' and request.url.path == '/api/templates/capture'
            if not allowed:
                raise HTTPException(403, 'Эта операция доступна только в панели.')
            return 'device'
        token = request.cookies.get('fps_session', '')
        sessions = store.query('SELECT * FROM sessions WHERE hash=? AND expires>?', (digest(token), time.time()))
        if not sessions:
            raise HTTPException(401, 'Войдите в панель.')
        session = sessions[0]
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            csrf = request.headers.get('x-csrf-token', '')
            if not hmac.compare_digest(csrf, session['csrf']):
                raise HTTPException(403, 'Обновите страницу: CSRF-токен устарел.')
            if background and not store.worker_ready:
                raise HTTPException(503, 'Сервер ожидает подключения к базе или завершения предыдущего запуска. Повторите позже.')
        return session

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({'detail': str(exc)[:500]}, status_code=400)

    @app.get('/healthz')
    def health():
        return {'ok': True, 'version': '1.1.0', 'worker_ready': store.worker_ready}

    @app.post('/api/login')
    def login(data: Login, request: Request, response: Response):
        rate(request, 'login')
        if not hmac.compare_digest(digest(data.password), digest(password)):
            raise HTTPException(401, 'Неверный пароль.')
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        store.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        store.execute('INSERT INTO sessions VALUES(?,?,?)', (digest(token), csrf, time.time() + 86400))
        response.set_cookie('fps_session', token, max_age=86400, httponly=True,
                            secure=public_url.startswith('https://'), samesite='strict', path='/')
        return {'csrf': csrf}

    @app.post('/api/pair')
    def pair(data: Pair, request: Request):
        rate(request, 'pair', 12)
        token = secrets.token_urlsafe(32)
        with store.tx() as c:
            found = c.execute('SELECT hash FROM pairs WHERE hash=? AND expires>?', (digest(data.code), time.time())).fetchone()
            if not found:
                raise HTTPException(401, 'Код неверный или истёк.')
            c.execute('DELETE FROM pairs WHERE hash=?', (digest(data.code),))
            c.execute('INSERT INTO devices VALUES(?,?,?)', (digest(token), data.name, time.time()))
        return {'token': token}

    api = APIRouter(prefix='/api', dependencies=[Depends(auth)])

    @api.get('/session')
    def session(request: Request):
        return {'csrf': auth(request)['csrf']}

    @api.post('/logout')
    def logout(request: Request, response: Response):
        store.execute('DELETE FROM sessions WHERE hash=?', (digest(request.cookies.get('fps_session', '')),))
        response.delete_cookie('fps_session')
        return {'ok': True}

    @api.get('/status')
    def status():
        counts = {'stock': store.query("SELECT count(*) n FROM stock WHERE state='available'")[0]['n'],
                  'delivered': store.query("SELECT count(*) n FROM orders WHERE status='delivered'")[0]['n'],
                  'attention': store.query("SELECT count(*) n FROM orders WHERE status IN ('uncertain','out_of_stock','pending')")[0]['n'],
                  'products': len(store.documents('products'))}
        return {'counts': counts, 'settings': store.settings(), 'connected': engine.connected,
                'account': engine.account, 'last_poll': engine.last_poll, 'error': engine.error,
                'has_key': bool(store.get('funpay_key')), 'telegram': bool(integrations.token and integrations.owner),
                'provider': bool(integrations.provider_url and integrations.provider_secret),
                'public_url': public_url, 'version': '1.1.0', 'worker_ready': store.worker_ready,
                'storage': 'postgresql' if store.postgres else 'sqlite'}

    @api.get('/settings')
    def settings():
        return store.settings()

    @api.put('/settings')
    def update_settings(data: Settings):
        store.set('settings', data.model_dump())
        return {'ok': True}

    @api.post('/connect')
    def connect(data: Credentials):
        with engine.lock:
            # Test first; a bad replacement cannot destroy the working saved key.
            account = engine.a.connect(data.golden_key, data.user_agent)
            owner = store.get('owner_id')
            if owner and owner != account['id']:
                engine.connected = False
                raise HTTPException(409, 'База закреплена за другим аккаунтом. Используйте отдельную установку.')
            store.set('funpay_key', store.encrypt(data.golden_key))
            store.set('user_agent', data.user_agent)
            store.set('owner_id', account['id'])
            engine.account, engine.connected, engine.error = account, True, None
        store.event('system', 'Аккаунт подключён', account['username'])
        return account

    def ensure_connected():
        if not engine.connected:
            engine.connect()

    @api.get('/lots')
    def lots():
        with engine.lock:
            ensure_connected()
            return engine.a.lots()

    @api.get('/products')
    def products():
        result = store.documents('products')
        counts = store.query('SELECT product_id,state,count(*) n FROM stock GROUP BY product_id,state')
        for p in result:
            p['stock'] = {r['state']: r['n'] for r in counts if r['product_id'] == p['id']}
        return result

    @api.post('/products')
    def save_product(data: Product):
        for p in store.documents('products'):
            if p['id'] != data.id and p['subcategory_id'] == data.subcategory_id and normalized(p['match_title']) == normalized(data.match_title):
                raise HTTPException(409, 'Для этого названия и раздела уже есть товар.')
        store.save_document('products', data.model_dump())
        return {'ok': True}

    @api.post('/products/{ident}/stock')
    def stock(ident: str, data: StockInput):
        if not store.document('products', ident):
            raise HTTPException(404, 'Товар не найден.')
        n = store.add_stock(ident, data.items)
        store.event('stock', 'Склад пополнен', str(n) + ' новых позиций')
        return {'added': n, 'duplicates': len(data.items) - n}

    @api.get('/products/{ident}/stock')
    def stock_list(ident: str):
        return store.query('SELECT id,state,order_id,created FROM stock WHERE product_id=? ORDER BY id DESC LIMIT 1000', (ident,))

    @api.delete('/stock/{ident}')
    def delete_stock(ident: int):
        # Keep the fingerprint so a delivered credential cannot be imported again.
        n = store.execute("UPDATE stock SET state='disabled' WHERE id=? AND state='available'", (ident,))
        if not n:
            raise HTTPException(409, 'Можно убрать только свободную позицию.')
        return {'ok': True}

    @api.get('/orders')
    def orders():
        return store.query('SELECT id,product_id,buyer_id,buyer,quantity,status,fp_status,created,delivered,expiry,review_applied,bonus_hours FROM orders ORDER BY created DESC LIMIT 1000')

    @api.post('/orders/{ident}/process')
    def process(ident: str):
        if not re.fullmatch(r'[A-Za-z0-9-]{4,40}', ident):
            raise HTTPException(400, 'Неверный номер заказа.')
        with engine.lock:
            ensure_connected()
            engine.process_order(ident)
        return {'ok': True}

    @api.post('/orders/{ident}/reconcile')
    def reconcile(ident: str, data: Reconcile):
        with engine.lock:
            ensure_connected()
            return {'status': engine.reconcile(ident, data.resend)}

    @api.get('/rules')
    def rules():
        return store.documents('rules')

    @api.post('/rules')
    def save_rule(data: Rule):
        store.save_document('rules', data.model_dump())
        return {'ok': True}

    @api.delete('/rules/{ident}')
    def delete_rule(ident: str):
        store.execute("DELETE FROM documents WHERE kind='rules' AND id=?", (ident,))
        return {'ok': True}

    @api.get('/templates')
    def templates():
        return store.documents('templates')

    @api.post('/templates')
    @api.post('/templates/capture')
    def save_template(data: Template):
        store.save_document('templates', data.model_dump())
        return {'ok': True}

    @api.get('/lots/{ident}/fields')
    def import_lot(ident: int):
        if ident <= 0:
            raise HTTPException(400, 'Укажите ID своего лота.')
        with engine.lock:
            ensure_connected()
            return engine.a.lot_fields(ident)

    @api.post('/templates/{ident}/publish')
    def publish(ident: str, data: Publish):
        template = store.document('templates', ident)
        if not template:
            raise HTTPException(404, 'Шаблон не найден.')
        if not template['fields'].get('node_id'):
            raise HTTPException(400, 'Сначала импортируйте поля своего лота или сохраните форму через расширение.')
        # Request ID persists on the client after an ambiguous response.
        # Repeating the same click/request does not enqueue a second publication.
        store.job('publish:' + data.request_id, 'publish', template, data.due)
        return {'ok': True, 'job_id': 'publish:' + data.request_id}

    @api.get('/jobs')
    def jobs():
        return store.query('SELECT id,kind,status,next_run,attempts,result FROM jobs ORDER BY next_run DESC LIMIT 500')

    @api.post('/jobs/{ident}/cancel')
    def cancel_job(ident: str):
        n = store.execute("UPDATE jobs SET status='cancelled' WHERE id=? AND kind='publish' AND status='pending'", (ident,))
        if not n:
            raise HTTPException(409, 'Задание уже выполняется или завершено.')
        return {'ok': True}

    @api.get('/boosts')
    def boosts():
        return store.query('SELECT * FROM boosts ORDER BY name')

    @api.post('/boosts')
    def save_boost(data: Boost):
        store.execute('INSERT INTO boosts(category_id,name,interval_minutes,next_run,enabled) VALUES(?,?,?,?,?) ON CONFLICT(category_id) DO UPDATE SET name=excluded.name,interval_minutes=excluded.interval_minutes,enabled=excluded.enabled',
                      (data.category_id, data.name, data.interval_minutes, time.time() + 10, int(data.enabled)))
        return {'ok': True}

    @api.delete('/boosts/{ident}')
    def delete_boost(ident: int):
        store.execute('DELETE FROM boosts WHERE category_id=?', (ident,))
        return {'ok': True}

    @api.get('/events')
    def events(after: int = 0):
        if after < 0:
            return {'events': [], 'cursor': store.query('SELECT coalesce(max(seq),0) n FROM events')[0]['n']}
        rows = store.query('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 200', (after,))
        return {'events': rows, 'cursor': rows[-1]['seq'] if rows else after}

    @api.post('/pair-code')
    def pair_code():
        code = secrets.token_urlsafe(9)
        store.execute('DELETE FROM pairs')
        store.execute('INSERT INTO pairs VALUES(?,?)', (digest(code), time.time() + 600))
        return {'code': code, 'expires_in': 600}

    @api.get('/devices')
    def devices():
        return store.query('SELECT hash AS id,name,created FROM devices')

    @api.delete('/devices/{ident}')
    def revoke(ident: str):
        store.execute('DELETE FROM devices WHERE hash=?', (ident,))
        return {'ok': True}

    @api.get('/backup')
    def backup():
        return Response(store.backup_data(), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="funpay-studio-backup.json"'})

    app.include_router(api)

    @app.get('/download/extension.zip')
    def extension():
        return Response(extension_archive(), media_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename="funpay-studio-extension.zip"'})

    @app.get('/assets/{name}')
    def asset(name: str):
        if name not in WEB_ASSETS or name.endswith('.html'):
            raise HTTPException(404, 'Not found')
        mime = {'.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml'}.get(Path(name).suffix, 'text/plain')
        return Response(WEB_ASSETS[name], media_type=mime)

    @app.get('/install')
    def install():
        return Response(WEB_ASSETS['install.html'], media_type='text/html')

    @app.get('/help')
    def help_page():
        import html
        body = '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        body += '<title>Инструкция FunPay Studio</title><link rel="stylesheet" href="/assets/style.css">'
        body += '<article class="install-page"><a class="text-link" href="/">← Панель</a> · <a class="text-link" href="/install">Установка расширения</a>'
        body += '<pre style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.7">' + html.escape(USER_GUIDE) + '</pre></article></html>'
        return Response(body, media_type='text/html')

    @app.get('/guide.txt')
    def guide_download():
        return Response(USER_GUIDE, media_type='text/plain')

    @app.get('/')
    def index():
        return Response(WEB_ASSETS['index.html'], media_type='text/html')

    @app.get('/admin')
    def admin_page():
        return Response(WEB_ASSETS['admin.html'], media_type='text/html')

    attach_nezzx_routes(app, nezzx, store, password, public_url)
    return app



# ======================================================================
# NEZZX HELPER: LICENSED AND ISOLATED WORKSPACES
# ======================================================================
from typing import Optional
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

NX_PLANS = {
    'd1': ('1 день', 5, 86400),
    'd2': ('2 дня', 15, 2*86400),
    'd5': ('5 дней', 50, 5*86400),
    'd7': ('7 дней', 100, 7*86400),
    'm1': ('30 дней', 450, 30*86400),
    'forever': ('Навсегда', 750, 0),
}

class NezzxService:
    # Sessions and FunPay connections are separate for each paid key.

    # Legacy owner automation is admin-only; paid users have isolated chats and music.
    def __init__(self, store, public_url):
        self.s, self.public_url = store, public_url
        self._adapters = {}
        self._adapter_mutex = threading.RLock()
        with self.s.tx() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS nx_users(
                id TEXT PRIMARY KEY, nickname TEXT NOT NULL, role TEXT NOT NULL,
                expiry REAL NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS nx_licenses(
                hash TEXT PRIMARY KEY, key_cipher TEXT NOT NULL,
                plan TEXT NOT NULL, stars INTEGER NOT NULL,
                telegram_id TEXT NOT NULL, used_by TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS nx_sessions(
                hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL,
                expiry REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS nx_invoices(
                payload TEXT PRIMARY KEY, telegram_id TEXT NOT NULL,
                plan TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS nx_payments(
                charge_id TEXT PRIMARY KEY, license_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nx_funpay(
                user_id TEXT PRIMARY KEY, credential TEXT NOT NULL,
                agent TEXT NOT NULL, account_id TEXT NOT NULL, username TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nx_tracks(
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                filename TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                content TEXT NOT NULL, created REAL NOT NULL);
            ''')

    def issue(self, charge_id, tg_id, plan):
        # Issue once, even for repeated successful_payment updates.
        with self.s.tx() as c:
            existing = c.execute('SELECT license_hash FROM nx_payments WHERE charge_id=?',(charge_id,)).fetchone()
            if existing:
                row = c.execute('SELECT key_cipher FROM nx_licenses WHERE hash=?',(existing['license_hash'],)).fetchone()
                return self.s.decrypt(row['key_cipher'])
            code = 'NZX-' + '-'.join(secrets.token_hex(3).upper() for _ in range(4))
            h = digest(code)
            c.execute('INSERT INTO nx_licenses(hash,key_cipher,plan,stars,telegram_id,used_by,created) VALUES(?,?,?,?,?,?,?)',
                      (h,self.s.encrypt(code),plan,NX_PLANS[plan][1],str(tg_id),None,time.time()))
            c.execute('INSERT INTO nx_payments(charge_id,license_hash) VALUES(?,?)',(charge_id,h))
            return code

    def telegram_update(self, bot, item):
        callback = item.get('callback_query')
        if callback and str(callback.get('data','')).startswith('nx_buy:'):
            pid = callback.get('data','').split(':',1)[1]
            who = callback.get('from',{}).get('id')
            bot.telegram('answerCallbackQuery', {'callback_query_id': callback['id']})
            if pid not in NX_PLANS or not who: return True
            name, stars, _ = NX_PLANS[pid]
            invoice = 'nx_'+pid+'_'+secrets.token_hex(12)
            self.s.execute('INSERT INTO nx_invoices VALUES(?,?,?,?)', (invoice,str(who),pid,time.time()))
            bot.telegram('sendInvoice', {
                'chat_id':who, 'title':'nezzx helper · '+name,
                'description':'Личный ключ доступа nezzx helper: '+name,
                'payload':invoice, 'provider_token':'', 'currency':'XTR',
                'prices':[{'label':name,'amount':stars}],
                'start_parameter':'nezzx-key'})
            return True
        checkout = item.get('pre_checkout_query')
        if checkout:
            data = self.s.query('SELECT * FROM nx_invoices WHERE payload=?',(checkout.get('invoice_payload',''),))
            good = bool(data and data[0]['telegram_id']==str(checkout.get('from',{}).get('id'))
                        and checkout.get('currency')=='XTR'
                        and checkout.get('total_amount')==NX_PLANS.get(data[0]['plan'],('',-1,-1))[1]
                        and time.time()-data[0]['created']<86400)
            bot.telegram('answerPreCheckoutQuery', {'pre_checkout_query_id':checkout['id'], 'ok':good,
                            **({} if good else {'error_message':'Счёт недействителен. Создайте новый через /start.'})})
            return True
        msg = item.get('message',{})
        paid = msg.get('successful_payment')
        if paid:
            invoice = self.s.query('SELECT * FROM nx_invoices WHERE payload=?',(paid.get('invoice_payload',''),))
            if not invoice or invoice[0]['telegram_id'] != str(msg.get('from',{}).get('id')) or paid.get('currency')!='XTR':
                return True
            pid = invoice[0]['plan']
            if pid not in NX_PLANS or paid.get('total_amount')!=NX_PLANS[pid][1]:
                return True
            charge = paid.get('telegram_payment_charge_id','')
            if not charge: return True
            code = self.issue(charge,msg['from']['id'],pid)
            bot.telegram('sendMessage', {'chat_id':msg['chat']['id'],
                'text':'✅ Оплата подтверждена!\n\nВаш ключ nezzx helper:\n'+code+
                       '\n\nОткройте '+self.public_url+' и введите ключ и свой ник. '
                       'Время доступа начнёт отсчитываться при первом использовании.'})
            return True
        cmd = str(msg.get('text','')).split('@')[0].strip()
        if cmd in ('/mykeys','/keys') and msg.get('chat',{}).get('type')=='private':
            rows=self.s.query('SELECT key_cipher,plan FROM nx_licenses WHERE telegram_id=? ORDER BY created DESC LIMIT 15',
                              (str(msg.get('from',{}).get('id')),))
            answer='Ваши ключи nezzx helper:\n\n' + '\n'.join(
                NX_PLANS[r['plan']][0] + ': ' + self.s.decrypt(r['key_cipher']) for r in rows)
            bot.telegram('sendMessage',{'chat_id':msg['chat']['id'],
                         'text':answer if rows else 'У вас пока нет купленных ключей. Откройте /start.'})
            return True
        if cmd=='/paysupport' and msg.get('chat',{}).get('type')=='private':
            contact=os.getenv('PAYMENT_SUPPORT','').strip()
            bot.telegram('sendMessage',{'chat_id':msg['chat']['id'],
                'text':'Поддержка покупок nezzx helper: '+(contact or 'обратитесь к владельцу сервиса.')})
            return True
        if cmd in ('/start','/buy','/prices') and msg.get('chat',{}).get('type')=='private':
            buttons = [[{'text':f'{n} · {p} ⭐','callback_data':'nx_buy:'+code}]
                       for code,(n,p,seconds) in NX_PLANS.items()]
            bot.telegram('sendMessage',{'chat_id':msg['chat']['id'],
                'text':'nezzx helper ✦\nВыберите срок доступа. После оплаты ключ придёт в этот чат.',
                'reply_markup':{'inline_keyboard':buttons}})
            return cmd != '/start' or str(msg['from']['id']) != str(bot.owner)
        return False

    def login(self, nickname, key):
        nickname=nickname.strip()
        if not 2<=len(nickname)<=32 or not re.fullmatch(r'[\w .-]+', nickname, re.UNICODE):
            raise HTTPException(400,'Ник: 2–32 символа, буквы, цифры, пробел, точка или дефис.')
        admin_key = os.getenv('NEZZX_ADMIN_KEY','')
        is_admin = len(admin_key)>=16 and hmac.compare_digest(key,admin_key)
        if is_admin:
            with self.s.tx() as c:
                c.execute('INSERT INTO nx_users(id,nickname,role,expiry,created) VALUES(?,?,?,?,?) '
                          'ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname',
                          ('nezzx-admin',nickname,'admin',0,time.time()))
            uid='nezzx-admin'
        else:
            h=digest(key)
            with self.s.tx() as c:
                lic=c.execute('SELECT * FROM nx_licenses WHERE hash=?',(h,)).fetchone()
                if not lic: raise HTTPException(401,'Ключ не найден. Купите его в Telegram-боте.')
                if lic['used_by']:
                    uid=lic['used_by']
                    user=c.execute('SELECT * FROM nx_users WHERE id=?',(uid,)).fetchone()
                    if not user or (user['role']!='admin' and user['expiry'] and user['expiry']<time.time()):
                        raise HTTPException(403,'Срок доступа закончился. Купите продление.')
                else:
                    uid=secrets.token_hex(16)
                    duration=NX_PLANS[lic['plan']][2]
                    expiry=time.time()+duration if duration else 0
                    c.execute('INSERT INTO nx_users VALUES(?,?,?,?,?)',(uid,nickname,'member',expiry,time.time()))
                    c.execute('UPDATE nx_licenses SET used_by=? WHERE hash=? AND used_by IS NULL',(uid,h))
        token=secrets.token_urlsafe(36)
        csrf=secrets.token_urlsafe(24)
        self.s.execute('DELETE FROM nx_sessions WHERE expiry<?',(time.time(),))
        self.s.execute('INSERT INTO nx_sessions VALUES(?,?,?,?)', (digest(token),uid,csrf,time.time()+30*86400))
        return uid,token,csrf,is_admin

    def session(self,token):
        if not token: raise HTTPException(401,'Введите ключ доступа.')
        rows=self.s.query('SELECT u.*, s.csrf FROM nx_sessions s JOIN nx_users u ON u.id=s.user_id '
                          'WHERE s.hash=? AND s.expiry>?',(digest(token),time.time()))
        if not rows: raise HTTPException(401,'Сессия истекла. Войдите снова.')
        user=rows[0]
        if user['role']!='admin' and user['expiry'] and user['expiry']<=time.time():
            raise HTTPException(403,'Срок доступа закончился. Продлите подписку.')
        return user

    def fp_adapter(self, uid):
        with self._adapter_mutex:
            if uid in self._adapters: return self._adapters[uid]
            cred=self.s.query('SELECT * FROM nx_funpay WHERE user_id=?',(uid,))
            if not cred: raise HTTPException(409,'Подключите собственный аккаунт FunPay в настройках.')
            a=FunPayAdapter()
            try: a.connect(self.s.decrypt(cred[0]['credential']),cred[0]['agent'])
            except Exception: raise HTTPException(502,'FunPay не ответил. Проверьте ключ и доступность сервиса.') from None
            self._adapters[uid]=a
            return a

    def chat_list(self,uid):
        adapter=self.fp_adapter(uid)
        try:
            with adapter.lock: chats=adapter.account.request_chats()
        except Exception: raise HTTPException(502,'Не удалось получить список чатов FunPay.') from None
        result=[]
        for x in chats[:150]:
            result.append({'id':str(getattr(x,'id','')),'name':str(getattr(x,'name','Собеседник'))[:80],
                           'last':str(getattr(x,'last_message_text','') or '')[:240],
                           'unread':bool(getattr(x,'unread',False))})
        return result

    def chat_history(self,uid,chat_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',chat_id):
            raise HTTPException(400,'Неверный ID чата.')
        a=self.fp_adapter(uid)
        try:
            with a.lock: data=a.account.get_chat_history(int(chat_id) if chat_id.isdigit() else chat_id)
        except Exception: raise HTTPException(502,'Не удалось загрузить сообщения.') from None
        out=[]
        for x in data[-100:]:
            stamp=getattr(x,'date',None) or getattr(x,'created',None)
            if hasattr(stamp,'timestamp'): stamp=stamp.timestamp()
            if not isinstance(stamp,(int,float)): stamp=0
            out.append({'id':str(getattr(x,'id','')),'author':str(getattr(x,'author',''))[:80],
                        'mine':str(getattr(x,'author_id',''))==str(a.user_id),
                        'text':str(getattr(x,'text','') or '')[:10000], 'at':stamp})
        return out

class NxLogin(BaseModel):
    nickname:str=Field(min_length=2,max_length=32)
    key:str=Field(min_length=8,max_length=160)

class NxText(BaseModel):
    text:str=Field(min_length=1,max_length=4000)

class NxConnect(BaseModel):
    golden_key:str=Field(min_length=16,max_length=1024)
    user_agent:str=Field(default='',max_length=1000)

class NxSong(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    filename:str=Field(min_length=1,max_length=200)
    mime:str=Field(min_length=1,max_length=70)
    base64_data:str=Field(min_length=1,max_length=8000000)

class NxRedeem(BaseModel):
    key:str=Field(min_length=8,max_length=160)


def attach_nezzx_routes(app, nx, store, admin_password, public_url):
    login_attempts={}
    attempt_lock=threading.Lock()

    def valid(request:Request):
        user=nx.session(request.cookies.get('nx_session',''))
        if request.method not in ('GET','HEAD','OPTIONS'):
            if not hmac.compare_digest(request.headers.get('X-NX-CSRF',''),user['csrf']):
                raise HTTPException(403,'Обновите страницу.')
        return user

    def set_session_cookie(response,token):
        response.set_cookie('nx_session',token,max_age=30*86400,httponly=True,
                            samesite='lax',secure=public_url.startswith('https://'),path='/')

    @app.post('/api/nx/login')
    def nx_login(data:NxLogin, request:Request, response:Response):
        ip=request.client.host if request.client else 'local'
        now=time.time()
        with attempt_lock:
            attempts=[x for x in login_attempts.get(ip,[]) if now-x<600]
            if len(attempts)>=15: raise HTTPException(429,'Слишком много попыток входа. Повторите через 10 минут.')
            login_attempts[ip]=attempts+[now]
            if len(login_attempts)>5000:
                for oldip in list(login_attempts):
                    if now-login_attempts[oldip][-1]>600:login_attempts.pop(oldip,None)
        uid,token,csrf,admin=nx.login(data.nickname,data.key)
        set_session_cookie(response,token)
        if not admin:
            response.delete_cookie('fps_session',path='/')
        if admin:
            admin_token=secrets.token_urlsafe(32)
            admin_csrf=secrets.token_urlsafe(24)
            store.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(admin_token),admin_csrf,time.time()+86400))
            response.set_cookie('fps_session',admin_token,max_age=86400,httponly=True,
                                secure=public_url.startswith('https://'),samesite='strict',path='/')
        return {'ok':True,'admin':admin,'csrf':csrf}

    @app.get('/api/nx/me')
    def nx_me(user=Depends(valid)):
        connected=store.query('SELECT username FROM nx_funpay WHERE user_id=?',(user['id'],))
        return {'nickname':user['nickname'],'admin':user['role']=='admin','expiry':user['expiry'],
                'csrf':user['csrf'],'funpay':connected[0]['username'] if connected else None}

    @app.get('/api/nx/config')
    def nx_config():
        username=os.getenv('TELEGRAM_BOT_USERNAME','').strip().lstrip('@')
        return {'bot_url':'https://t.me/'+username if re.fullmatch(r'[A-Za-z0-9_]{5,32}',username) else None,
                'plans':[{'id':k,'name':v[0],'stars':v[1]} for k,v in NX_PLANS.items()]}

    @app.post('/api/nx/extend')
    def nx_extend(data:dict, response:Response):
        old_key=str(data.get('old_key',''))
        new_key=str(data.get('new_key',''))
        if not (8<=len(old_key)<=160 and 8<=len(new_key)<=160):
            raise HTTPException(400,'Введите прежний и новый ключи.')
        with store.tx() as c:
            old=c.execute('SELECT used_by FROM nx_licenses WHERE hash=?',(digest(old_key),)).fetchone()
            new=c.execute('SELECT * FROM nx_licenses WHERE hash=?',(digest(new_key),)).fetchone()
            if not old or not old['used_by'] or not new or new['used_by']:
                raise HTTPException(403,'Не удалось подтвердить оба ключа.')
            user=c.execute('SELECT * FROM nx_users WHERE id=?',(old['used_by'],)).fetchone()
            if not user or user['role']!='member':raise HTTPException(403,'Кабинет не найден.')
            duration=NX_PLANS[new['plan']][2]
            expiry=0 if duration==0 or user['expiry']==0 else max(time.time(),user['expiry'])+duration
            c.execute('UPDATE nx_users SET expiry=? WHERE id=?',(expiry,user['id']))
            c.execute('UPDATE nx_licenses SET used_by=? WHERE hash=?',(user['id'],new['hash']))
        token=secrets.token_urlsafe(36)
        csrf=secrets.token_urlsafe(24)
        store.execute('INSERT INTO nx_sessions VALUES(?,?,?,?)',(digest(token),user['id'],csrf,time.time()+30*86400))
        set_session_cookie(response,token)
        return {'ok':True,'csrf':csrf}

    @app.post('/api/nx/logout')
    def nx_logout(request:Request,response:Response,user=Depends(valid)):
        store.execute('DELETE FROM nx_sessions WHERE hash=?',(digest(request.cookies.get('nx_session','')),))
        response.delete_cookie('nx_session',path='/')
        response.delete_cookie('fps_session',path='/')
        return {'ok':True}

    @app.post('/api/nx/redeem')
    def nx_redeem(data:NxRedeem,user=Depends(valid)):
        if user['role']=='admin':return {'ok':True,'expiry':0}
        with store.tx() as c:
            lic=c.execute('SELECT * FROM nx_licenses WHERE hash=?',(digest(data.key),)).fetchone()
            if not lic or lic['used_by'] and lic['used_by']!=user['id']:
                raise HTTPException(400,'Этот ключ недействителен или привязан к другому аккаунту.')
            if lic['used_by']==user['id']:
                raise HTTPException(409,'Этот ключ уже применён.')
            duration=NX_PLANS[lic['plan']][2]
            expiry=0 if duration==0 or user['expiry']==0 else max(time.time(),user['expiry'])+duration
            c.execute('UPDATE nx_users SET expiry=? WHERE id=?',(expiry,user['id']))
            c.execute('UPDATE nx_licenses SET used_by=? WHERE hash=?',(user['id'],lic['hash']))
        return {'ok':True,'expiry':expiry}

    @app.post('/api/nx/funpay')
    def nx_funpay_connect(data:NxConnect,user=Depends(valid)):
        a=FunPayAdapter()
        try: profile=a.connect(data.golden_key,data.user_agent)
        except Exception: raise HTTPException(400,'Не удалось подключить FunPay. Проверьте golden_key и User-Agent.') from None
        with store.tx() as c:
            c.execute('INSERT INTO nx_funpay VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET '
                      'credential=excluded.credential,agent=excluded.agent,account_id=excluded.account_id,username=excluded.username',
                      (user['id'],store.encrypt(data.golden_key),data.user_agent,str(profile['id']),profile['username']))
        with nx._adapter_mutex: nx._adapters[user['id']]=a
        return {'ok':True,'username':profile['username']}

    @app.get('/api/nx/chats')
    def nx_chats(user=Depends(valid)):
        return nx.chat_list(user['id'])

    @app.get('/api/nx/chats/{chat_id}')
    def nx_messages(chat_id:str,user=Depends(valid)):
        return nx.chat_history(user['id'],chat_id)

    @app.post('/api/nx/chats/{chat_id}')
    def nx_send(chat_id:str,data:NxText,user=Depends(valid)):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',chat_id):
            raise HTTPException(400,'Неверный ID чата.')
        try:
            nx.fp_adapter(user['id']).send(chat_id,data.text)
        except Exception: raise HTTPException(502,'Отправка не подтверждена. Проверьте чат перед повтором.') from None
        return {'ok':True}

    @app.get('/api/nx/music')
    def nx_music(user=Depends(valid)):
        return store.query('SELECT id,name,filename,mime,size,created FROM nx_tracks WHERE user_id=? ORDER BY created DESC LIMIT 200',(user['id'],))

    @app.post('/api/nx/music')
    def nx_music_add(data:NxSong,user=Depends(valid)):
        import binascii
        if data.mime not in ('audio/mpeg','audio/mp3','audio/ogg','audio/wav','audio/x-wav','audio/mp4','audio/x-m4a','audio/webm','audio/wave'):
            raise HTTPException(400,'Поддерживаются MP3, OGG, WAV, M4A, WebM.')
        try: body=base64.b64decode(data.base64_data,validate=True)
        except binascii.Error:raise HTTPException(400,'Повреждённый аудиофайл.') from None
        if not (50<=len(body)<=4*1024*1024):raise HTTPException(413,'Максимум 4 МБ на песню.')
        magic=(body.startswith(b'ID3') or body.startswith(b'OggS') or body.startswith(b'RIFF')
               or b'ftyp' in body[:16] or (len(body)>2 and body[0]==255 and (body[1]&224)==224) or body.startswith(b'\x1a\x45\xdf\xa3'))
        if not magic:raise HTTPException(400,'Содержимое не похоже на аудиофайл.')
        if len(store.query('SELECT id FROM nx_tracks WHERE user_id=?',(user['id'],)))>=100:
            raise HTTPException(409,'Лимит: 100 песен на кабинет.')
        track_id=secrets.token_hex(14)
        store.execute('INSERT INTO nx_tracks VALUES(?,?,?,?,?,?,?,?)',
                      (track_id,user['id'],data.name,data.filename,data.mime,len(body),data.base64_data,time.time()))
        return {'ok':True,'id':track_id}

    @app.get('/api/nx/music/{track_id}/file')
    def nx_music_file(track_id:str,user=Depends(valid)):
        rows=store.query('SELECT content,mime FROM nx_tracks WHERE id=? AND user_id=?',(track_id,user['id']))
        if not rows: raise HTTPException(404,'Трек не найден.')
        return Response(base64.b64decode(rows[0]['content']),media_type=rows[0]['mime'],
                        headers={'Cache-Control':'private, max-age=600','X-Content-Type-Options':'nosniff'})

    @app.delete('/api/nx/music/{track_id}')
    def nx_music_delete(track_id:str,user=Depends(valid)):
        store.execute('DELETE FROM nx_tracks WHERE id=? AND user_id=?',(track_id,user['id']))
        return {'ok':True}


# The new light, translucent app is independent of the old admin dashboard.
NEZZX_INDEX = r'''<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#e8eafb"><title>nezzx helper</title><link rel="icon" href="/assets/favicon.svg"><link rel="stylesheet" href="/assets/desk.css"><script src="/assets/desk.js" defer></script></head><body>
<div class="ambient ambient-a"></div><div class="ambient ambient-b"></div>
<section id="gate" class="gate"><div class="gate-layout"><div class="gate-story glass"><div class="topline"><span class="brand-glyph">n<span>✦</span></span><span class="wordmark">nezzx <b>helper</b></span><span class="v-tag">WORKSPACE / 01</span></div><div class="gate-scene"><div class="scene-ring ring-a"></div><div class="scene-ring ring-b"></div><div class="scene-main"><span>n.</span></div><div class="scene-chip one">✦ your space</div><div class="scene-chip two">● connected</div></div><div class="story-copy"><span class="eyebrow">YOUR PERSONAL CONTROL CENTER</span><h1>Make your space<br><em>feel like yours.</em></h1><p>Чаты, музыка и управление в одном спокойном пространстве.</p></div><div class="story-foot"><span>✳ PRIVATE WORKSPACE</span><span>EST. 2026</span></div></div><div class="gate-aside glass"><div class="corner-badge">✦  NEZZX ACCESS</div><h2>hello, <span id="hello-name">friend</span><span class="waving">✳</span></h2><p class="intro-sub">start your key?</p><form id="login-form" autocomplete="off"><label>Ваш никнейм<input name="nickname" id="nick" maxlength="32" minlength="2" placeholder="Например, nezzx" required></label><label>Ключ доступа<input name="key" id="key" type="password" maxlength="160" minlength="8" placeholder="NZX-XXXXXX-XXXXXX-XXXXXX-XXXXXX" required></label><button class="cta" type="submit"><span>Продолжить</span><span class="arrow-disk">↗</span></button></form><p class="error" id="login-error" role="alert"></p><details class="renew-details"><summary>Продлить старый кабинет</summary><form id="expired-renew"><input id="old-license" type="password" placeholder="Старый ключ" required><input id="new-license" type="password" placeholder="Новый оплаченный ключ" required><button class="small-btn" type="submit">Продлить без потери данных</button></form></details><div class="pay-intro"><span>Ещё нет ключа?</span><span>Оплата через Telegram Stars ✦</span></div><div id="plans" class="plans"></div><a class="tg-btn" id="telegram-link" target="_blank" rel="noopener">Открыть Telegram-бота <span>↗</span></a><div class="gate-bottom">Защищённый вход · Без публичного доступа к вашим данным</div></div></div></section>
<div id="app" class="app hidden"><aside class="sidebar glass"><a class="brand" href="#home"><span class="logo">n✦</span><span>nezzx <b>helper</b><small>YOUR WORKSPACE</small></span></a><div class="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div><nav id="navigation"><button data-tab="home"><span class="nav-icon">◈</span> Главная</button><button data-tab="chats"><span class="nav-icon">▤</span> Чаты</button><button data-tab="music"><span class="nav-icon">♫</span> Музыка</button><button data-tab="settings"><span class="nav-icon">⚙</span> Настройки</button></nav><div class="side-end"><a id="admin-link" class="admin-link hidden" href="/admin">◉ Управление магазином <span>↗</span></a><div class="membership"><div class="online-dot"></div><div><strong>Ваш доступ</strong><small id="membership-time">Активен</small></div><span class="membership-icon">✦</span></div><button id="logout" class="exit">Выйти из аккаунта <span>↗</span></button></div></aside><main class="main"><header class="header glass"><div class="breadcrumbs"><span>nezzx helper</span><span class="slash">/</span><strong id="crumb">Главная</strong></div><div class="header-right"><span class="status-pill"><i></i> Workspace active</span><div id="avatar" class="avatar">N</div></div></header><div id="content" class="content"></div><footer class="footer">NEZZX HELPER <span>✦</span> YOUR SPACE, YOUR RULES</footer></main></div><div id="toast" class="toast" role="status" aria-live="polite"></div><audio id="music-player" preload="metadata"></audio>
</body></html>'''
NEZZX_CSS = r''':root{font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#252a46;font-size:14px;--ink:#202642;--muted:#8186a2;--violet:#7568eb;--line:rgba(255,255,255,.75);--glass:rgba(255,255,255,.64);--shadow:0 22px 72px rgba(66,62,131,.13)}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;background:linear-gradient(132deg,#f5f5ff 0%,#edeefa 45%,#e8e9f8 100%);overflow-x:hidden}button,input,textarea{font:inherit}button{cursor:pointer}a{color:inherit;text-decoration:none}.hidden{display:none!important}.ambient{pointer-events:none;position:fixed;filter:blur(20px);z-index:-1;border-radius:50%;opacity:.7}.ambient-a{width:570px;height:570px;background:radial-gradient(circle,#d4c8ff,transparent 70%);top:-270px;right:-160px}.ambient-b{width:600px;height:600px;background:radial-gradient(circle,#badfe7,transparent 69%);bottom:-350px;left:-190px}.glass{background:var(--glass);border:1px solid var(--line);backdrop-filter:blur(32px) saturate(165%);-webkit-backdrop-filter:blur(32px) saturate(165%);box-shadow:var(--shadow)}.gate{min-height:100vh;display:grid;place-items:center;padding:26px}.gate-layout{width:min(1120px,100%);min-height:680px;display:grid;grid-template-columns:1.02fr .98fr;gap:16px}.gate-story,.gate-aside{border-radius:28px;overflow:hidden}.gate-story{position:relative;padding:30px 38px;display:flex;flex-direction:column;background:linear-gradient(140deg,rgba(253,253,255,.8),rgba(235,231,255,.55));min-height:650px}.topline{display:flex;align-items:center;gap:11px}.brand-glyph,.logo{width:35px;height:35px;display:grid;place-items:center;background:linear-gradient(145deg,#9784fe,#655fdf);color:white;border-radius:12px;font-size:21px;font-weight:900;box-shadow:0 9px 20px #7972ef47}.brand-glyph span{font-size:10px;position:absolute;transform:translate(14px,-11px)}.wordmark{font-weight:800;letter-spacing:-.65px;font-size:19px}.wordmark b,.brand b{font-weight:450}.v-tag{margin-left:auto;font-size:10px;letter-spacing:.1em;color:#9192aa}.gate-scene{flex:1;min-height:320px;position:relative;display:grid;place-items:center}.scene-ring{position:absolute;border:1px solid #a4a5df91;border-radius:50%;transform:rotate(-28deg)}.ring-a{width:310px;height:170px}.ring-b{width:215px;height:310px;transform:rotate(43deg)}.scene-main{width:197px;height:197px;border-radius:54px;display:grid;place-items:center;background:linear-gradient(140deg,#e9e2ff 3%,#9686ec 47%,#6f6ac9 95%);color:#fff;box-shadow:inset 8px 12px 18px #ffffff7b,inset -12px -15px 35px #3c2ea64a,0 28px 60px #6d62c969;transform:rotate(-15deg)}.scene-main span{font-size:91px;letter-spacing:-12px;font-weight:800;transform:rotate(15deg);padding-right:12px}.scene-chip{position:absolute;padding:13px 17px;border:1px solid #fff;border-radius:18px;background:#ffffffb8;box-shadow:0 12px 40px #7166af26;color:#6364a7;font-size:12px}.scene-chip.one{left:2%;top:24%;transform:rotate(-7deg)}.scene-chip.two{right:-1%;bottom:26%;transform:rotate(6deg)}.story-copy{padding-bottom:26px}.eyebrow,.corner-badge{letter-spacing:.16em;font-size:10px;font-weight:800;color:#857ce8}.story-copy h1{font-size:clamp(37px,4vw,55px);letter-spacing:-2.2px;line-height:1.05;margin:18px 0}.story-copy h1 em{font-style:normal;color:#8276e8}.story-copy p{line-height:1.7;color:#8086a0;max-width:370px}.story-foot{display:flex;justify-content:space-between;font-size:9px;color:#a3a5be;letter-spacing:.15em;border-top:1px solid #d5d6ea;padding-top:19px}.gate-aside{padding:39px 44px;display:flex;flex-direction:column;justify-content:center;background:#ffffffbb}.gate-aside h2{font-size:42px;line-height:1.1;letter-spacing:-1.9px;margin:24px 0 4px}.gate-aside h2 span:first-child{color:#7b6fdd}.waving{font-size:25px;color:#edbd73;display:inline-block;margin-left:9px}.intro-sub{font-size:19px;color:#9294a9;margin:0 0 30px}.gate-aside label{display:block;margin:0 0 14px;color:#676b83;font-size:12px;font-weight:700}.gate-aside input,.form-control{width:100%;height:48px;border:1px solid #dedff1;background:#fafaffa8;border-radius:13px;padding:0 16px;color:#303452;margin-top:7px;outline:none;transition:.25s}.gate-aside input:focus,.form-control:focus{border-color:#9386fa;box-shadow:0 0 0 4px #8671ed1c}.cta{width:100%;display:flex;align-items:center;justify-content:space-between;height:52px;border:0;border-radius:15px;background:linear-gradient(130deg,#897af5,#6c66d8);color:white;box-shadow:0 13px 25px #776ce84f;padding:0 12px 0 22px;margin:9px 0 6px;font-weight:700}.cta:hover{transform:translateY(-1px);filter:brightness(1.04)}.arrow-disk{background:#ffffff35;border:1px solid #ffffff36;width:34px;height:34px;border-radius:10px;display:grid;place-items:center;font-size:21px}.error{color:#c84664;font-size:12px;min-height:16px;margin:6px 0}.pay-intro{display:flex;justify-content:space-between;gap:6px;font-size:11px;margin:14px 0;color:#979ab0}.pay-intro span:first-child{font-weight:700;color:#6d708b}.plans{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.plan{background:#f2f0fdcc;border:1px solid #dfdcfa;border-radius:12px;padding:11px 5px;text-align:center}.plan strong{display:block;font-size:11px}.plan small{display:block;font-size:10px;color:#7b72d9;margin-top:5px}.tg-btn{display:flex;align-items:center;justify-content:space-between;font-size:12px;font-weight:750;margin-top:17px;color:#6c63dc}.tg-btn:hover{text-decoration:underline}.renew-details{font-size:11px;color:#7c73cb;margin:3px 0 10px}.renew-details summary{cursor:pointer}.renew-details input{height:37px;margin:7px 0}.renew-details button{width:100%;margin-top:7px}.gate-bottom{font-size:10px;color:#adafc3;margin-top:26px;text-align:center}.app{display:grid;grid-template-columns:238px minmax(0,1fr);min-height:100vh;gap:17px;padding:15px;max-width:1640px;margin:auto}.sidebar{height:calc(100vh - 30px);position:sticky;top:15px;border-radius:24px;padding:29px 14px 17px;display:flex;flex-direction:column}.brand{display:flex;align-items:center;gap:10px;padding:0 12px;font-size:18px;font-weight:800;letter-spacing:-.8px;margin-bottom:55px}.brand small{display:block;font-size:9px;font-weight:600;color:#a0a3bc;letter-spacing:.17em;margin-top:5px}.logo{width:42px;height:42px;border-radius:14px;font-size:24px}.nav-label{font-size:10px;color:#b0b0c4;font-weight:800;letter-spacing:.13em;padding:0 18px;margin-bottom:15px}nav{display:grid;gap:6px}nav button{background:transparent;border:0;text-align:left;color:#7f849f;border-radius:13px;padding:12px 14px;display:flex;align-items:center;gap:13px;font-weight:700;transition:.25s}nav button:hover{background:#e5e3fb70;color:#6b61cd}nav button.active{background:linear-gradient(95deg,#8275ef,#716add);color:white;box-shadow:0 9px 23px #766fe840}.nav-icon{font-size:19px;width:22px;text-align:center}.side-end{margin-top:auto}.admin-link{display:flex;align-items:center;justify-content:space-between;color:#6765ce;background:#eae6ff;border-radius:13px;padding:14px 12px;font-size:11px;font-weight:800;margin-bottom:16px}.membership{display:flex;align-items:center;gap:9px;background:#fff9;border:1px solid #fff;border-radius:16px;padding:14px 12px}.online-dot{width:9px;height:9px;background:#66c7a6;border-radius:100%;box-shadow:0 0 0 5px #68c6a721}.membership strong{display:block;font-size:11px}.membership small{display:block;color:#8c90aa;font-size:10px;margin-top:4px}.membership-icon{margin-left:auto;font-size:20px;color:#8a7ae9}.exit{background:transparent;border:0;color:#999ab0;padding:18px 13px;text-align:left;font-size:12px;width:100%;display:flex;justify-content:space-between}.main{min-width:0;display:flex;flex-direction:column}.header{border-radius:20px;min-height:69px;padding:0 25px;display:flex;align-items:center;justify-content:space-between}.breadcrumbs{display:flex;gap:12px;color:#9c9db4;font-size:12px;align-items:center}.breadcrumbs strong{color:#4c4c6c}.slash{color:#d0cee2}.header-right{display:flex;align-items:center;gap:22px}.status-pill{font-size:11px;color:#728093}.status-pill i{display:inline-block;width:7px;height:7px;background:#62c6a4;border-radius:50%;margin-right:8px}.avatar{background:linear-gradient(130deg,#887ae9,#b4a5fd);border-radius:11px;width:36px;height:36px;display:grid;place-items:center;color:white;font-weight:900}.content{width:100%;max-width:1300px;margin:0 auto;flex:1;padding:30px 14px}.page-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 24px}.page-top h1{margin:0 0 5px;font-size:31px;letter-spacing:-1.2px}.subline{margin:0;color:#9296ad;font-size:13px;line-height:1.6}.eyebrow{display:block;margin-bottom:13px}.small-btn{border:1px solid #dfdcf2;background:#ffffffad;color:#7469c7;font-size:12px;padding:11px 14px;border-radius:11px;font-weight:750}.small-btn:hover{background:#fff}.welcome{position:relative;overflow:hidden;min-height:270px;border-radius:24px;padding:38px;background:linear-gradient(120deg,#8580e6,#b5a4fa 72%,#c3abf4);color:white;box-shadow:0 18px 45px #897dd74b;margin-bottom:20px}.welcome:before{content:"";position:absolute;width:310px;height:310px;border:1px solid #ffffff55;border-radius:50%;right:14%;top:-40%}.welcome:after{content:"";position:absolute;width:190px;height:190px;border:1px solid #ffffff90;border-radius:50%;right:8%;bottom:-80px}.welcome-kicker{font-size:10px;letter-spacing:.18em;font-weight:800;color:#efeaff}.welcome h2{font-size:43px;letter-spacing:-1.8px;margin:24px 0 8px}.welcome p{max-width:430px;color:#f8f5ffdb;font-size:13px;line-height:1.65}.welcome-art{position:absolute;right:10%;top:48px;width:165px;height:165px;background:#ffffff23;border:1px solid #ffffff5e;backdrop-filter:blur(15px);border-radius:48px;transform:rotate(-17deg);display:grid;place-items:center;font-weight:900;font-size:88px;box-shadow:0 20px 55px #584aa633}.tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}.tile{border-radius:20px;padding:23px;min-height:150px}.tile-icon{width:41px;height:41px;border-radius:13px;background:#e9e5ff;color:#796fdd;display:grid;place-items:center;font-size:23px;margin-bottom:20px}.tile h3{font-size:15px;letter-spacing:-.4px;margin:0 0 6px}.tile p{font-size:12px;color:#9497ad;margin:0}.tile a{color:#7b6ce2;font-size:11px;display:inline-block;margin-top:13px;font-weight:800}.panel{border-radius:20px;padding:23px}.chat-layout{display:grid;grid-template-columns:310px minmax(0,1fr);height:min(670px,calc(100vh - 220px));min-height:440px;border-radius:22px;overflow:hidden}.chat-list{border-right:1px solid #e9e7f7;display:flex;flex-direction:column;overflow:hidden}.search{margin:17px 14px 9px;border:1px solid #e4e1f2;background:#fff9;border-radius:12px;width:calc(100% - 28px);padding:12px 13px;outline:none}.chat-items{overflow:auto;flex:1;padding:8px}.chat-row{width:100%;display:flex;gap:11px;padding:13px 11px;text-align:left;align-items:center;border:0;background:transparent;border-radius:13px}.chat-row:hover{background:#eeedff8a}.chat-row.active{background:#e8e5ff}.mini-avatar{flex-shrink:0;background:linear-gradient(135deg,#cdc6f9,#a4a0ed);color:white;width:39px;height:39px;border-radius:13px;display:grid;place-items:center;font-weight:900}.chat-text{min-width:0;flex:1}.chat-text strong{display:block;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.chat-text small{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:#a0a0b9;margin-top:5px}.unread{width:8px;height:8px;border-radius:50%;background:#8574eb}.thread{display:flex;flex-direction:column;min-width:0}.thread-title{padding:17px 20px;border-bottom:1px solid #e9e7f7;display:flex;gap:12px;align-items:center}.thread-title strong{font-size:13px}.thread-title small{display:block;color:#aaa9bd;font-size:11px;margin-top:4px}.messages{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:12px;padding:22px}.bubble{max-width:75%;align-self:flex-start;background:#fff;border-radius:17px 17px 17px 4px;padding:13px 16px;box-shadow:0 6px 20px #8077b40e;font-size:12px;line-height:1.65;overflow-wrap:anywhere;white-space:pre-wrap}.bubble.mine{align-self:flex-end;border-radius:17px 17px 4px 17px;background:#8073e5;color:white}.bubble small{display:block;font-size:9px;opacity:.7;margin-top:6px}.composer{border-top:1px solid #e6e4f4;display:flex;padding:14px;gap:9px}.composer textarea{height:49px;max-height:110px;resize:vertical;min-width:0;flex:1;border:1px solid #e5e3f4;border-radius:12px;background:#fff9;padding:14px;outline:0}.send-btn{border:0;border-radius:12px;background:#8173e5;color:white;padding:0 21px;font-size:18px}.empty{display:grid;place-content:center;text-align:center;flex:1;color:#9595ad;padding:36px}.empty .symbol{font-size:43px;color:#a09af1;margin-bottom:14px}.empty strong{font-size:15px;color:#595b7d;display:block;margin-bottom:7px}.empty p{font-size:12px;line-height:1.6}.track-list{display:grid;gap:10px}.track{display:flex;gap:15px;align-items:center;padding:14px;background:#ffffff80;border:1px solid #fff;border-radius:15px}.track .mini-avatar{background:#ede9fc;color:#8175da;font-size:20px}.track-info{flex:1;min-width:0}.track-info strong{display:block;text-overflow:ellipsis;overflow:hidden;white-space:nowrap}.track-info small{color:#a2a2b9;font-size:10px}.play{background:#8177e1;color:white;border:0;border-radius:10px;padding:10px 13px}.track-del{background:transparent;border:0;color:#b2abc2;font-size:18px;padding:10px}.player-bar{display:flex;gap:13px;align-items:center;margin-top:18px}.player-bar audio{width:100%;height:38px}.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.settings-grid .panel{min-height:230px}.settings-grid h3{font-size:16px}.settings-grid p{color:#9296ad;font-size:12px;line-height:1.7}.form-control{margin:9px 0;width:100%}.purple{background:#8375e9;border:0;color:white;padding:13px 21px;border-radius:12px;font-weight:800}.foot-note{font-size:11px;color:#aaa9be}.footer{display:flex;justify-content:space-between;color:#aaaec1;font-size:9px;letter-spacing:.19em;padding:24px 15px}.toast{position:fixed;bottom:22px;right:22px;background:#2f3155;color:#fff;z-index:1000;border-radius:14px;padding:14px 20px;max-width:340px;box-shadow:0 15px 40px #32306237;opacity:0;transform:translateY(15px);transition:.25s;pointer-events:none}.toast.show{opacity:1;transform:none}.toast.bad{background:#ac4567}.upload-label{border:1px dashed #c5bff2;border-radius:13px;background:#f6f5ff;padding:17px;display:block;text-align:center;color:#766add;font-weight:700;font-size:12px;cursor:pointer}.upload-label input{display:none}.hint{color:#a3a3b9;font-size:12px;line-height:1.5}.mobile-menu{display:none}@media(max-width:1050px){.app{grid-template-columns:200px minmax(0,1fr);gap:10px;padding:10px}.sidebar{height:calc(100vh - 20px);top:10px}.chat-layout{grid-template-columns:240px minmax(0,1fr)}.content{padding:22px 6px}.welcome-art{right:4%;opacity:.7}.gate-layout{grid-template-columns:1fr 1fr}.gate-aside{padding:32px 25px}.gate-story{padding:28px}.story-copy h1{font-size:38px}}@media(max-width:740px){.gate{padding:12px}.gate-layout{grid-template-columns:1fr;gap:0}.gate-story{min-height:250px;padding:20px 24px;border-radius:22px 22px 0 0}.topline .v-tag{display:none}.gate-scene{display:none}.story-copy{padding:25px 0 5px}.story-copy h1{font-size:34px;margin:12px 0}.story-foot{display:none}.gate-aside{border-radius:0 0 22px 22px;padding:28px 24px}.gate-aside h2{font-size:36px;margin-top:13px}.app{grid-template-columns:1fr;display:block;padding:9px}.sidebar{position:fixed;z-index:5;bottom:8px;top:auto;height:65px;left:9px;right:9px;border-radius:20px;padding:7px;display:flex;flex-direction:row;align-items:center}.sidebar .brand,.nav-label,.side-end{display:none}.sidebar nav{display:flex;width:100%;justify-content:space-around;gap:4px}.sidebar nav button{flex:1;justify-content:center;flex-direction:column;gap:3px;font-size:9px;padding:7px 3px;border-radius:13px}.sidebar nav .nav-icon{font-size:21px}.header{padding:0 16px;height:58px;min-height:58px}.status-pill{display:none}.content{padding:22px 3px 90px}.page-top h1{font-size:27px}.welcome{padding:27px;min-height:230px}.welcome h2{font-size:32px}.welcome-art{height:110px;width:110px;border-radius:31px;font-size:62px;opacity:.35;right:7%;top:60px}.tiles{grid-template-columns:1fr}.tile{min-height:115px}.chat-layout{grid-template-columns:1fr;height:calc(100dvh - 190px);min-height:430px}.chat-layout.thread-open .chat-list{display:none}.chat-layout:not(.thread-open) .thread{display:none}.chat-list{border-right:0}.thread-title:before{content:'‹';font-size:28px;color:#7869df;cursor:pointer}.settings-grid{grid-template-columns:1fr}.footer{display:none}.bubble{max-width:86%}}
'''
NEZZX_JS = r'''"use strict";
const $=s=>document.querySelector(s);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let me=null,csrf='',current=null,chats=[],tracks=[],selectedTrack=null;
const player=$('#music-player');
const tabs=['home','chats','music','settings'];
function notify(msg,bad=false){const t=$('#toast');t.textContent=msg;t.className='toast show'+(bad?' bad':'');setTimeout(()=>t.className='toast',3700)}
async function req(path,method='GET',payload){const opt={method,credentials:'same-origin',headers:{}};if(method!=='GET')opt.headers['X-NX-CSRF']=csrf;if(payload!==undefined){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(payload)}let res;try{res=await fetch('/api/nx'+path,opt)}catch{throw Error('Сервис временно недоступен.')}const data=await res.json().catch(()=>({}));if(!res.ok)throw Error(typeof data.detail==='string'?data.detail:'Не удалось выполнить действие.');return data;}
function asDate(n){return n?new Date(n*1000).toLocaleDateString('ru-RU',{day:'numeric',month:'short',year:'numeric'}):'Без ограничений'}
function section(title,sub,action=''){return `<div class="page-top"><div><h1>${title}</h1><p class="subline">${sub}</p></div>${action}</div>`}
function empty(symbol,title,desc){return `<div class="empty"><div class="symbol">${symbol}</div><strong>${title}</strong><p>${desc}</p></div>`}
function setTab(tab){if(!tabs.includes(tab))tab='home';$('#crumb').textContent={home:'Главная',chats:'Чаты',music:'Музыка',settings:'Настройки'}[tab];document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));location.hash=tab;if(tab==='home')home();if(tab==='chats')loadChats();if(tab==='music')loadTracks();if(tab==='settings')settings();}
function main(){me&&($('#gate').classList.add('hidden'),$('#app').classList.remove('hidden'));$('#avatar').textContent=(me.nickname||'N')[0].toUpperCase();$('#membership-time').textContent=me.admin?'Администратор · ∞':me.expiry?'До '+asDate(me.expiry):'Без ограничений';$('#admin-link').classList.toggle('hidden',!me.admin);setTab(location.hash.substring(1)||'home')}
function home(){const title=esc(me.nickname);$('#content').innerHTML=section('Добро пожаловать, '+title+' ✳','Ваше личное рабочее пространство.')+`<div class="welcome"><span class="welcome-kicker">NEZZX HELPER / PERSONAL SPACE</span><h2>hello, ${title}.</h2><p>Всё, что нужно для работы, теперь рядом. Выбирайте диалог, отвечайте покупателям и слушайте свою музыку.</p><div class="welcome-art">n.</div></div><div class="tiles"><div class="tile glass"><div class="tile-icon">▤</div><h3>Сообщения FunPay</h3><p>Ваши чаты в одном месте, с отправкой ответов.</p><a href="#chats">Открыть чаты ↗</a></div><div class="tile glass"><div class="tile-icon">♫</div><h3>Музыка</h3><p>Личная коллекция треков и встроенный плеер.</p><a href="#music">Перейти к музыке ↗</a></div><div class="tile glass"><div class="tile-icon">✦</div><h3>${me.admin?'Администратор':'Активная подписка'}</h3><p>${me.admin?'Ваша лицензия бессрочная. Полный доступ к панели магазина.':'Доступ активен до '+(me.expiry?asDate(me.expiry):'бессрочно')+'.'}</p><a href="#settings">Управление ↗</a></div></div>`}
async function loadChats(){const c=$('#content');c.innerHTML=section('Чаты','Ваши личные диалоги FunPay.',`<button class="small-btn" id="refresh-chats">↻ Обновить</button>`)+`<div class="chat-layout glass" id="chat-layout"><div class="chat-list"><input id="chat-search" class="search" placeholder="⌕  Найти диалог"><div class="chat-items" id="chat-items">${empty('◌','Загружаем диалоги','Подключаем FunPay…')}</div></div><div class="thread" id="thread">${empty('▤','Выберите диалог','Сообщения и ответы появятся здесь.')}</div></div>`;$('#refresh-chats').onclick=loadChats;$('#chat-search').oninput=showChats;try{chats=await req('/chats');showChats();if(current&&chats.some(x=>x.id===current))openChat(current)}catch(e){$('#chat-items').innerHTML=empty('◌','Нет подключения',esc(e.message)+'<br><a href="#settings" style="color:#7466d5">Открыть настройки ↗</a>');}}
function showChats(){const node=$('#chat-items');if(!node)return;const term=($('#chat-search')?.value||'').toLowerCase();const rows=chats.filter(c=>(c.name+' '+c.last).toLowerCase().includes(term));node.innerHTML=rows.length?rows.map(c=>`<button class="chat-row ${c.id===current?'active':''}" data-chat="${esc(c.id)}"><span class="mini-avatar">${esc(c.name.slice(0,1).toUpperCase())}</span><span class="chat-text"><strong>${esc(c.name)}</strong><small>${esc(c.last||'Откройте переписку')}</small></span>${c.unread?'<i class="unread"></i>':''}</button>`).join(''):empty('▤','Диалогов нет','Входящие чаты FunPay появятся здесь.');node.querySelectorAll('[data-chat]').forEach(b=>b.onclick=()=>openChat(b.dataset.chat));}
async function openChat(cid){current=cid;showChats();$('#chat-layout')?.classList.add('thread-open');const pane=$('#thread');if(!pane)return;const item=chats.find(x=>x.id===cid)||{name:'Диалог'};pane.innerHTML=`<div class="thread-title" id="thread-back"><span class="mini-avatar">${esc(item.name[0]||'?')}</span><div><strong>${esc(item.name)}</strong><small>FunPay · Личный диалог</small></div><button class="small-btn" id="thread-refresh" style="margin-left:auto">↻</button></div><div class="messages" id="messages">${empty('◌','Загружаем сообщения','')}</div><form id="composer" class="composer"><textarea id="message-input" placeholder="Написать сообщение…" maxlength="4000" required></textarea><button class="send-btn" title="Отправить">➤</button></form>`;$('#thread-refresh').onclick=()=>openChat(cid);$('#thread-back').onclick=e=>{if(e.target.id==='thread-back'||e.target.classList.contains('mini-avatar'))$('#chat-layout').classList.remove('thread-open')};$('#composer').onsubmit=async e=>{e.preventDefault();const input=$('#message-input');const msg=input.value.trim();if(!msg)return;const button=$('.send-btn');button.disabled=true;try{await req('/chats/'+encodeURIComponent(cid),'POST',{text:msg});input.value='';await showMessages(cid);notify('Сообщение отправлено')}catch(e){notify(e.message,true)}finally{button.disabled=false}};$('#message-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#composer').requestSubmit()}};await showMessages(cid)}
async function showMessages(cid){try{const ms=await req('/chats/'+encodeURIComponent(cid));if(current!==cid)return;const n=$('#messages');n.innerHTML=ms.length?ms.map(m=>`<div class="bubble ${m.mine?'mine':''}">${esc(m.text)}<small>${esc(m.author)} ${m.at?asDate(m.at):''}</small></div>`).join(''):empty('✦','Сообщений пока нет','Начните переписку прямо отсюда.');n.scrollTop=n.scrollHeight}catch(e){const n=$('#messages');if(n)n.innerHTML=empty('◌','Ошибка загрузки',esc(e.message))}}
async function loadTracks(){const c=$('#content');c.innerHTML=section('Моя музыка','Личная коллекция, которая хранится вместе с вашим аккаунтом.')+`<div class="panel glass"><div class="page-top"><div><h3 style="margin:0 0 6px">Мои треки</h3><p class="subline">MP3, OGG, WAV, M4A, WebM. До 4 МБ на файл.</p></div><label class="upload-label">+ Добавить песню<input type="file" id="song-file" accept=".mp3,.ogg,.wav,.m4a,.webm,audio/*"></label></div><div id="tracks" class="track-list">${empty('♫','Загружаем музыку','')}</div><div id="player-bar" class="player-bar hidden"><span style="color:#877bdb">♫</span><strong id="track-now" style="min-width:100px;max-width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Трек</strong><div style="flex:1"><audio id="inline-player" controls style="width:100%"></audio></div></div></div>`;$('#song-file').onchange=uploadTrack;try{tracks=await req('/music');showTracks()}catch(e){notify(e.message,true)}}
function showTracks(){const root=$('#tracks');if(!root)return;root.innerHTML=tracks.length?tracks.map((s,i)=>`<div class="track"><div class="mini-avatar">♫</div><div class="track-info"><strong>${esc(s.name)}</strong><small>${esc(s.filename)} · ${(s.size/1024/1024).toFixed(2)} МБ</small></div><button class="play" data-play="${esc(s.id)}">▶</button><button class="track-del" data-remove="${esc(s.id)}" title="Удалить">×</button></div>`).join(''):empty('♫','Здесь будут ваши песни','Нажмите «Добавить песню», чтобы создать коллекцию.');root.querySelectorAll('[data-play]').forEach(b=>b.onclick=()=>playTrack(b.dataset.play));root.querySelectorAll('[data-remove]').forEach(b=>b.onclick=()=>deleteTrack(b.dataset.remove));}
function playTrack(id){const song=tracks.find(x=>x.id===id);if(!song)return;selectedTrack=id;$('#player-bar').classList.remove('hidden');const a=$('#inline-player');a.src='/api/nx/music/'+encodeURIComponent(id)+'/file';$('#track-now').textContent=song.name;a.play().catch(()=>notify('Нажмите Play в плеере для запуска.',true))}
async function deleteTrack(id){if(!confirm('Удалить этот трек?'))return;try{await req('/music/'+encodeURIComponent(id),'DELETE');if(selectedTrack===id){$('#inline-player')?.pause();$('#player-bar')?.classList.add('hidden')}await loadTracks();notify('Трек удалён')}catch(e){notify(e.message,true)}}
async function uploadTrack(e){const f=e.target.files?.[0];if(!f)return;if(f.size>4*1024*1024){notify('Максимальный размер трека: 4 МБ.',true);return}const name=f.name.replace(/\.[^.]+$/,'').slice(0,100);const btn=$('.upload-label');btn.textContent='Сохраняем трек…';try{const contents=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(f)});let mime=f.type;if(!mime){const ext=f.name.split('.').pop().toLowerCase();mime=({mp3:'audio/mpeg',wav:'audio/wav',m4a:'audio/mp4',ogg:'audio/ogg',webm:'audio/webm'})[ext]||''}await req('/music','POST',{name,filename:f.name,mime,base64_data:contents});notify('Песня добавлена');await loadTracks()}catch(err){notify(err.message,true);await loadTracks()}}
function settings(){$('#content').innerHTML=section('Настройки','Безопасное подключение и управление доступом.')+`<div class="settings-grid"><div class="panel glass"><div class="tile-icon">▤</div><h3>FunPay аккаунт</h3><p>Подключите свой FunPay, чтобы читать чаты и отвечать покупателям прямо из nezzx helper. Ваш ключ не виден другим пользователям.</p><form id="fp-form"><input class="form-control" name="golden_key" type="password" minlength="16" placeholder="Ваш golden_key" required autocomplete="off"><button class="purple">Подключить FunPay ↗</button></form><p class="foot-note">${me.funpay?'Подключено: '+esc(me.funpay):'Сейчас не подключено'}</p></div><div class="panel glass"><div class="tile-icon">✦</div><h3>Лицензия</h3><p>${me.admin?'Вы используете бессрочный администраторский доступ.':'Ваш доступ действует '+(me.expiry?'до '+asDate(me.expiry):'без ограничений')+'.'} При покупке нового ключа его можно добавить здесь.</p><form id="renew-form"><input class="form-control" type="text" name="key" placeholder="Новый ключ NZX-…" required><button class="purple">Продлить доступ ↗</button></form><p class="foot-note">После продления все ваши песни и переписки останутся в кабинете.</p></div></div>`;$('#fp-form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);try{const result=await req('/funpay','POST',{golden_key:f.get('golden_key'),user_agent:navigator.userAgent});me.funpay=result.username;notify('FunPay подключён');settings()}catch(err){notify(err.message,true)}};$('#renew-form').onsubmit=async e=>{e.preventDefault();try{await req('/redeem','POST',{key:new FormData(e.target).get('key')});me=await req('/me');notify('Подписка продлена');settings()}catch(err){notify(err.message,true)}}}
$('#login-form').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.target);try{const data=await req('/login','POST',{nickname:form.get('nickname'),key:form.get('key')});csrf=data.csrf;me=await req('/me');$('#login-error').textContent='';main()}catch(err){$('#login-error').textContent=err.message}};
$('#expired-renew').onsubmit=async e=>{e.preventDefault();try{const res=await req('/extend','POST',{old_key:$('#old-license').value,new_key:$('#new-license').value});csrf=res.csrf;me=await req('/me');main();notify('Доступ продлён, данные сохранены')}catch(err){notify(err.message,true)}};
$('#nick').oninput=()=>$('#hello-name').textContent=$('#nick').value.trim()||'friend';
$('#logout').onclick=async()=>{try{await req('/logout','POST',{})}catch{}me=null;csrf='';$('#app').classList.add('hidden');$('#gate').classList.remove('hidden');$('#key').value='';$('#music-player').pause();location.hash='';};
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
window.addEventListener('hashchange',()=>{if(me){const tab=location.hash.slice(1);if(tabs.includes(tab)&&$('#crumb').textContent!==({home:'Главная',chats:'Чаты',music:'Музыка',settings:'Настройки'}[tab]))setTab(tab)}});
(async()=>{try{const config=await req('/config');$('#plans').innerHTML=config.plans.map(p=>`<div class="plan"><strong>${esc(p.name)}</strong><small>${p.stars} ⭐</small></div>`).join('');if(config.bot_url){$('#telegram-link').href=config.bot_url}else{$('#telegram-link').removeAttribute('href');$('#telegram-link').textContent='Ссылка на бот будет доступна после настройки'};me=await req('/me');csrf=me.csrf;main()}catch{}})();
'''

# nezzx frontend assets assigned after the legacy web assets are constructed.
WEB_ASSETS['admin.html'] = WEB_ASSETS['index.html'].replace('FunPay Studio', 'nezzx helper').replace('FunPay <b>Studio</b>', 'nezzx <b>helper</b>')
WEB_ASSETS['index.html'] = NEZZX_INDEX
WEB_ASSETS['desk.css'] = NEZZX_CSS
WEB_ASSETS['desk.js'] = NEZZX_JS


def run_cli(args):
    load_local_env()
    password = os.getenv('ADMIN_PASSWORD', '')
    master_key = os.getenv('MASTER_KEY', '')
    if len(password) < 16 or len(master_key) < 32:
        raise RuntimeError('Set ADMIN_PASSWORD (16+ chars) and MASTER_KEY (32+ chars); python bot.py --secrets')
    database_url = os.getenv('DATABASE_URL')
    if os.getenv('RENDER') and not database_url:
        raise RuntimeError('Set DATABASE_URL on Render. Paid disk is not needed. See python bot.py --guide')
    if args.restore or args.import_sqlite or args.check_db:
        store = Store(Path(os.getenv('DATA_DIR', str(BASE_DIR / 'data'))) / 'studio.sqlite3', master_key, database_url=database_url)
        try:
            if args.restore:
                count = store.restore_data(Path(args.restore).read_bytes())
                print('Restored records:', count, '. Automation is paused; pair extensions again.')
            elif args.import_sqlite:
                if not database_url:
                    raise RuntimeError('Set destination DATABASE_URL before importing SQLite.')
                from urllib.parse import quote
                uri = 'file:' + quote(str(Path(args.import_sqlite).resolve())) + '?mode=ro'
                with sqlite3.connect(uri, uri=True) as source:
                    source.row_factory = sqlite3.Row
                    tables = {table: [dict(r) for r in source.execute('SELECT * FROM ' + table).fetchall()] for table in BACKUP_TABLES}
                payload = json.dumps({'format': 'funpay-studio-backup', 'version': 1, 'tables': tables})
                count = store.restore_data(payload)
                print('Imported records:', count, '. Original SQLite was read only. Automation is paused.')
            else:
                print('Database ready:', 'PostgreSQL' if store.postgres else 'local SQLite', '. No FunPay operations performed.')
        finally:
            store.close()
        return
    stamp = VENDOR_DIR / '.commit'
    if not stamp.is_file() or stamp.read_text().strip() != API_COMMIT:
        raise RuntimeError('Run python bot.py --install first (Render Build Command).')
    import uvicorn
    application = create_app()
    port = int(os.getenv('PORT', '8000'))
    host = '0.0.0.0' if os.getenv('RENDER') else os.getenv('HOST', '127.0.0.1')
    uvicorn.run(application, host=host, port=port, workers=1, access_log=False,
                timeout_graceful_shutdown=65)


if __name__ == '__main__':
    try:
        run_cli(_CLI_ARGS)
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        print('Startup error:', str(exc), file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        # Never dump connection strings, credentials or HTTP response bodies.
        print('Startup failed (' + type(exc).__name__ + '). Check Environment and python bot.py --guide.', file=sys.stderr)
        raise SystemExit(1)
