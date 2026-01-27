import threading
import time
import telebot
import psycopg2
import os
import re
import requests
import locale
import datetime
import logging
import urllib.parse
import random

from io import BytesIO
from telebot import types
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs
from get_google_krwrub_rate import get_krwrub_rate
from get_google_usdrub_rate import get_usdrub_rate
from get_vtb_cnyrub_rate import get_vtb_cnyrub_rate
from che168_scraper import (
    get_che168_car_info,
    get_che168_car_info_with_fallback,
    extract_car_id_from_che168_url,
    is_che168_url,
    format_mileage as format_che168_mileage,
    format_gearbox as format_che168_gearbox,
)
from utils import (
    clear_memory,
    calculate_age,
    format_number,
    get_customs_fees,
    clean_number,
    get_rub_to_krw_rate,
    generate_encar_photo_url,
    get_pan_auto_car_data,
    sort_photo_urls,
    FUEL_TYPE_GASOLINE,
    FUEL_TYPE_DIESEL,
    FUEL_TYPE_ELECTRIC,
    FUEL_TYPE_HYBRID_SERIES,
    FUEL_TYPE_HYBRID_PARALLEL,
    FUEL_TYPE_NAMES,
)


CALCULATE_CAR_TEXT = "Расчёт по ссылке с Encar"
MANUAL_CAR_TEXT = "Расчёт стоимости вручную"
CALCULATE_CHINA_CAR_TEXT = "Расчёт по ссылке с Che168"
MANUAL_CHINA_CAR_TEXT = "Расчёт авто из Китая вручную"
DEALER_COMMISSION = 0.00  # 2%

# China constants
CHINA_DEPOSIT = 5000           # ¥5,000 задаток
CHINA_EXPERT_REPORT = 1600     # ¥1,600 отчет эксперта
CHINA_FIRST_PAYMENT = 6600     # ¥6,600 итого первая часть
CHINA_DEALER_FEE = 3000        # ¥3,000 дилерский сбор
CHINA_DELIVERY = 15000         # ¥15,000 доставка + оформление
CHINA_BROKER_FEE = 60000       # ₽60,000 брокер (фиксированная)
CHINA_AGENT_FEE = 50000        # ₽50,000 агентские услуги
CHINA_SVH_FEE = 50000          # ₽50,000 СВХ
CHINA_LAB_FEE = 30000          # ₽30,000 лаборатория
DATABASE_URL = os.getenv("DATABASE_URL")

# Список User-Agent'ов (можно дополнять)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.2 Mobile/15E148 Safari/604.1",
]

PROXIES = {
    "http": "http://B01vby:GBno0x@45.118.250.2:8000",
    "https": "http://B01vby:GBno0x@45.118.250.2:8000",
}

MANAGERS = [
    7311646338, # Dmitriy
    490148761, # Alexandra
]


# Configure logging
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Load keys from .env file
load_dotenv()
bot_token = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(bot_token)

# Set locale for number formatting
locale.setlocale(locale.LC_ALL, "en_US.UTF-8")

# Storage for the last error message ID
last_error_message_id = {}

# global variables
car_data = {}
user_manual_input = {}
car_id_external = ""
total_car_price = 0
users = set()
admins = [728438182, 7311646338, 490148761, 463460708]  # админы
car_month = None
car_year = None

usd_rate = 0
krw_rub_rate = None
eur_rub_rate = None
rub_to_krw_rate = None
cny_rub_rate = None  # CNY to RUB rate for Chinese cars

vehicle_id = None
vehicle_no = None

# Pending HP requests for users (when pan-auto.ru doesn't have the car)
pending_hp_requests = {}

# Storage for China manual calculation
user_manual_china_input = {}

# Pending HP requests for China cars
pending_china_hp_requests = {}


def create_fuel_type_keyboard():
    """Create inline keyboard for fuel type selection."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Бензин", callback_data="fuel_1"),
        types.InlineKeyboardButton("Дизель", callback_data="fuel_2"),
    )
    keyboard.add(
        types.InlineKeyboardButton("Электро", callback_data="fuel_4"),
    )
    keyboard.add(
        types.InlineKeyboardButton("Гибрид (посл.)", callback_data="fuel_5"),
        types.InlineKeyboardButton("Гибрид (парал.)", callback_data="fuel_6"),
    )
    return keyboard


def extract_car_id_from_url(url):
    """Extract Encar car ID from URL"""
    # Match patterns like:
    # http://www.encar.com/dc/dc_cardetailview.do?carid=39844023
    # https://fem.encar.com/cars/detail/41074555
    match = re.search(r'(?:carid=|detail/)(\d+)', url)
    return match.group(1) if match else None


def get_cached_hp(manufacturer, model, engine_volume, year):
    """Look up HP from cache by Make+Model+Engine+Year"""
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT horsepower FROM car_hp_cache
            WHERE manufacturer = %s AND model = %s
            AND engine_volume = %s AND year = %s
        """, (manufacturer, model, engine_volume, year))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logging.error(f"Error getting cached HP: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def save_hp_to_cache(manufacturer, model, engine_volume, year, horsepower):
    """Save HP to cache for future lookups (only called by trusted sources)"""
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO car_hp_cache (manufacturer, model, engine_volume, year, horsepower)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (manufacturer, model, engine_volume, year)
            DO UPDATE SET horsepower = EXCLUDED.horsepower
        """, (manufacturer, model, engine_volume, year, horsepower))
        conn.commit()
        logging.info(f"Saved HP to cache: {manufacturer} {model} {engine_volume}cc {year} -> {horsepower} HP")
    except Exception as e:
        logging.error(f"Error saving HP to cache: {e}")
    finally:
        cursor.close()
        conn.close()


def is_valid_hp(hp_value):
    """Check if HP value is valid and usable"""
    if hp_value is None:
        return False
    if isinstance(hp_value, str):
        # Handle strings like "Не указана"
        return False
    if isinstance(hp_value, (int, float)):
        return hp_value > 0
    return False


def has_valid_customs(costs_rub):
    """Check if customs values from pan-auto.ru are valid"""
    if not costs_rub:
        return False
    customs_duty = costs_rub.get("customsDuty", 0)
    recycling_fee = costs_rub.get("utilizationFee", 0)
    # At least customs duty and recycling fee should be positive
    return customs_duty > 0 and recycling_fee > 0


# Настройка базы данных
import psycopg2
from psycopg2 import sql
from telebot import types

# Подключение к базе данных
DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
cursor = conn.cursor()
print("✅ Успешное подключение к БД")


def save_user_to_db(user_id, username, first_name, phone_number):
    """Сохраняет пользователя в базу данных."""
    if username is None or phone_number is None:
        return  # Пропускаем пользователей с скрытыми данными

    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        cursor = conn.cursor()

        # SQL-запрос для вставки данных
        query = sql.SQL(
            """
            INSERT INTO users (user_id, username, first_name, phone_number)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id) DO NOTHING;
        """
        )

        cursor.execute(query, (user_id, username, first_name, phone_number))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Ошибка при сохранении пользователя: {e}")


@bot.message_handler(commands=["start"])
def send_welcome(message):
    """Команда /start — сохраняет пользователя и приветствует его"""
    user = message.from_user
    user_id = user.id
    username = user.username
    first_name = user.first_name

    # Пропускаем пользователей без username
    if username is None:
        username = ""

    save_user_to_db(user_id, username, first_name, "")

    bot.send_message(
        message.chat.id,
        f"Здравствуйте, {first_name}! 👋\n\n"
        "Я бот компании GetAuto. Я помогу вам рассчитать стоимость автомобиля из Южной Кореи и Китая до Владивостока.",
        reply_markup=main_menu(),
    )


@bot.message_handler(commands=["stats"])
def show_statistics(message):
    """Команда /stats доступна только администраторам"""
    user_id = message.chat.id  # Получаем user_id того, кто запустил команду

    if user_id not in admins:
        bot.send_message(user_id, "❌ У вас нет доступа к этой команде.")
        return
    
    # Отправляем первую страницу статистики
    send_stats_page(user_id, page=1)


def send_stats_page(chat_id, page=1, message_id=None):
    """Отправляет страницу статистики с пагинацией"""
    USERS_PER_PAGE = 20
    
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        cursor = conn.cursor()
        
        # Получаем общее количество пользователей
        cursor.execute("SELECT COUNT(*) FROM users;")
        total_users = cursor.fetchone()[0]
        
        if total_users == 0:
            bot.send_message(chat_id, "📊 В базе пока нет пользователей.")
            cursor.close()
            conn.close()
            return
        
        # Вычисляем количество страниц
        total_pages = (total_users + USERS_PER_PAGE - 1) // USERS_PER_PAGE
        
        # Проверяем корректность номера страницы
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
        
        # Вычисляем offset для запроса
        offset = (page - 1) * USERS_PER_PAGE
        
        # Получаем пользователей для текущей страницы (сортировка по дате, самые новые первыми)
        cursor.execute(
            "SELECT user_id, username, first_name, created_at FROM users "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s;",
            (USERS_PER_PAGE, offset)
        )
        users = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # Формируем сообщение со статистикой
        stats_message = f"📊 <b>Статистика пользователей</b>\n"
        stats_message += f"👥 Всего пользователей: <b>{total_users}</b>\n"
        stats_message += f"📄 Страница <b>{page}/{total_pages}</b>\n\n"
        
        # Вычисляем правильную нумерацию (учитывая обратный порядок)
        start_num = offset + 1
        
        for idx, user in enumerate(users):
            user_id_db, username, first_name, created_at = user
            username_text = f"@{username}" if username else "—"
            # Экранируем HTML-символы в имени пользователя
            if first_name:
                first_name = first_name.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
            else:
                first_name = "Без имени"
            
            user_info = (
                f"👤 <b>{start_num + idx}.</b> {first_name} ({username_text}) — "
                f"{created_at.strftime('%Y-%m-%d')}\n"
            )
            stats_message += user_info
        
        # Создаем клавиатуру с кнопками навигации
        markup = types.InlineKeyboardMarkup(row_width=3)
        buttons = []
        
        # Кнопка "Назад"
        if page > 1:
            buttons.append(types.InlineKeyboardButton(
                "⬅️ Назад", 
                callback_data=f"stats_page_{page-1}"
            ))
        
        # Кнопка с номером страницы (неактивная)
        buttons.append(types.InlineKeyboardButton(
            f"{page}/{total_pages}", 
            callback_data="stats_current"
        ))
        
        # Кнопка "Вперед"
        if page < total_pages:
            buttons.append(types.InlineKeyboardButton(
                "Вперед ➡️", 
                callback_data=f"stats_page_{page+1}"
            ))
        
        if buttons:
            markup.add(*buttons)
        
        # Отправляем или редактируем сообщение
        if message_id:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=stats_message,
                parse_mode="HTML",
                reply_markup=markup if len(buttons) > 1 else None
            )
        else:
            bot.send_message(
                chat_id,
                stats_message,
                parse_mode="HTML",
                reply_markup=markup if len(buttons) > 1 else None
            )
            
    except Exception as e:
        error_msg = f"❌ Ошибка при получении статистики: {str(e)}"
        if message_id:
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=error_msg)
        else:
            bot.send_message(chat_id, error_msg)
        logging.error(f"Ошибка статистики: {e}")


def is_subscribed(user_id):
    """Проверяет, подписан ли пользователь на канал GetAuto"""
    channel_username = "@Getauto_kor"
    try:
        chat_member = bot.get_chat_member(channel_username, user_id)
        status = chat_member.status
        print(f"Статус подписки для пользователя {user_id}: {status}")

        # Проверяем все возможные статусы участника канала
        is_member = status in ["member", "administrator", "creator", "owner"]
        print(f"Результат проверки подписки: {is_member}")
        return is_member

    except Exception as e:
        print(f"Ошибка при проверке подписки для пользователя {user_id}: {e}")
        # В случае ошибки возвращаем False, чтобы пользователь мог попробовать еще раз
        return False


def print_message(message):
    print("\n\n##############")
    print(f"{message}")
    print("##############\n\n")
    return None


@bot.message_handler(commands=["setbroadcast"])
def set_broadcast(message):
    """Команда для запуска рассылки вручную"""
    if message.chat.id not in admins:
        bot.send_message(message.chat.id, "🚫 У вас нет прав для запуска рассылки.")
        return

    bot.send_message(message.chat.id, "✍️ Введите текст рассылки:")
    bot.register_next_step_handler(message, process_broadcast)


def process_broadcast(message):
    """Обрабатывает введённый текст и запускает рассылку"""
    text = message.text
    bot.send_message(message.chat.id, f"📢 Начинаю рассылку...\n\n{text}")

    # Запускаем рассылку
    send_broadcast(text)


def send_broadcast(text):
    """Функция отправки рассылки всем пользователям из базы"""
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode="require")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username FROM users WHERE username IS NOT NULL AND phone_number IS NOT NULL"
        )
        users = cursor.fetchall()

        count = 0  # Счётчик успешных сообщений

        for user in users:
            user_id, username = user
            # {username}, на связи GetAuto!\n\n
            personalized_text = f"{text}"
            try:
                bot.send_message(user_id, personalized_text, parse_mode="HTML")
                count += 1
                time.sleep(0.5)  # Задержка, чтобы не блокировали
            except Exception as e:
                print(f"Ошибка отправки пользователю {user_id}: {e}")

        bot.send_message(
            message.chat.id, f"✅ Рассылка завершена! Отправлено {count} сообщений."
        )
    except Exception as e:
        bot.send_message(message.chat.id, "❌ Ошибка при отправке рассылки.")
        print(f"Ошибка рассылки: {e}")
    finally:
        cursor.close()
        conn.close()


# Функция для установки команд меню
def set_bot_commands():
    commands = [
        types.BotCommand("start", "Запустить бота"),
        types.BotCommand("cbr", "Курсы валют"),
        types.BotCommand("stats", "Статистика"),
    ]
    bot.set_my_commands(commands)


# Функция для получения курсов валют с API
def get_currency_rates():
    global usd_rate, krw_rub_rate, eur_rub_rate, cny_rub_rate

    print_message("ПОЛУЧАЕМ КУРС ЦБ")

    url = "https://www.cbr-xml-daily.ru/daily_json.js"

    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(f"❌ Ошибка загрузки курсов. Статус: {response.status_code}")
        print(f"Ответ: {response.text}")
        return "❌ Ошибка загрузки курсов."

    try:
        data = response.json()
    except Exception as e:
        print(f"❌ Ошибка JSON: {e}")
        print(f"Ответ: {response.text}")
        return "❌ Неверный формат данных."

    eur = data["Valute"]["EUR"]["Value"] + (
        data["Valute"]["EUR"]["Value"] * DEALER_COMMISSION
    )

    usd = get_usdrub_rate()
    usd_rate = usd

    krw = get_krwrub_rate()
    krw_rub_rate = krw

    # Fetch CNY rate from VTB Bank
    cny = get_vtb_cnyrub_rate()
    cny_rub_rate = cny

    eur_rub_rate = eur

    # Check if rates were successfully fetched
    if usd is None or krw is None or eur is None:
        return "❌ Ошибка получения курсов валют. Попробуйте позже."

    rates_text = f"EUR: <b>{eur:.2f} ₽</b>\n" f"KRW: <b>{krw:.5f} ₽</b>\n"
    if cny:
        rates_text += f"CNY: <b>{cny:.4f} ₽</b>\n"

    return rates_text


# Обработчик команды /cbr
@bot.message_handler(commands=["cbr"])
def cbr_command(message):
    try:
        rates_text = get_currency_rates()

        # Создаем клавиатуру с кнопкой для расчета автомобиля
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )

        # Отправляем сообщение с курсами и клавиатурой
        bot.send_message(
            message.chat.id, rates_text, reply_markup=keyboard, parse_mode="HTML"
        )
    except Exception as e:
        bot.send_message(
            message.chat.id, "Не удалось получить курсы валют. Попробуйте позже."
        )
        print(f"Ошибка при получении курсов валют: {e}")


# Обработчик команды /currencyrates
@bot.message_handler(commands=["currencyrates"])
def currencyrates_command(message):
    bot.send_message(message.chat.id, "Актуальные курсы валют: ...")


# Main menu creation function
def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(
        types.KeyboardButton(CALCULATE_CAR_TEXT),
        types.KeyboardButton(MANUAL_CAR_TEXT),
    )
    keyboard.add(
        types.KeyboardButton(CALCULATE_CHINA_CAR_TEXT),
        types.KeyboardButton(MANUAL_CHINA_CAR_TEXT),
    )
    keyboard.add(
        types.KeyboardButton("Написать менеджеру"),
        types.KeyboardButton("Почему стоит выбрать нас?"),
    )
    keyboard.add(
        types.KeyboardButton("Мы в соц. сетях"),
        types.KeyboardButton("Написать в WhatsApp"),
    )
    keyboard.add(
        types.KeyboardButton("Оформить кредит"),
    )
    return keyboard


# Start command handler
@bot.message_handler(commands=["start"])
def send_welcome(message):
    user = message.from_user
    user_id = user.id
    username = user.username
    first_name = user.first_name
    phone_number = (
        user.phone_number if hasattr(user, "phone_number") else None
    )  # Получаем номер телефона

    if not is_subscribed(user_id):
        # Если пользователь не подписан, отправляем сообщение и не даем пользоваться ботом
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton("🔗 Подписаться", url="https://t.me/Getauto_kor")
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "✅ Проверить подписку", callback_data="check_subscription"
            )
        )
        bot.send_message(
            user_id,
            "🚫 Для использования бота, пожалуйста, подпишитесь на наш канал!",
            reply_markup=keyboard,
        )
        return  # Прерываем выполнение функции

    # Если подписан — продолжаем работу
    welcome_message = (
        f"Здравствуйте, {first_name}!\n\n"
        "Я бот компании GetAuto. Я помогу вам рассчитать стоимость понравившегося вам автомобиля из Южной Кореи или Китая до Владивостока.\n\n"
        "Выберите действие из меню ниже."
    )
    bot.send_message(user_id, welcome_message, reply_markup=main_menu())


# Error handling function
def send_error_message(message, error_text):
    global last_error_message_id

    # Remove previous error message if it exists
    if last_error_message_id.get(message.chat.id):
        try:
            bot.delete_message(message.chat.id, last_error_message_id[message.chat.id])
        except Exception as e:
            logging.error(f"Error deleting message: {e}")

    # Send new error message and store its ID
    error_message = bot.reply_to(message, error_text, reply_markup=main_menu())
    last_error_message_id[message.chat.id] = error_message.id
    logging.error(f"Error sent to user {message.chat.id}: {error_text}")


def get_car_info(url):
    global car_id_external, vehicle_no, vehicle_id, car_year, car_month

    # driver = create_driver()

    car_id_match = re.findall(r"\d+", url)
    car_id = car_id_match[0]
    car_id_external = car_id

    url = f"https://api.encar.com/v1/readside/vehicle/{car_id}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "http://www.encar.com/",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
    }

    response = requests.get(url, headers=headers).json()

    # Информация об автомобиле
    car_make = response["category"]["manufacturerEnglishName"]  # Марка
    car_model = response["category"]["modelGroupEnglishName"]  # Модель
    car_trim = response["category"]["gradeDetailEnglishName"] or ""  # Комплектация

    car_title = f"{car_make} {car_model} {car_trim}"  # Заголовок

    # Получаем все необходимые данные по автомобилю
    car_price = str(response["advertisement"]["price"])
    car_date = response["category"]["yearMonth"]
    year = car_date[2:4]
    month = car_date[4:]
    car_year = year
    car_month = month

    # Пробег (форматирование)
    mileage = response["spec"]["mileage"]
    formatted_mileage = f"{mileage:,} км"

    # Тип КПП
    transmission = response["spec"]["transmissionName"]
    formatted_transmission = "Автомат" if "오토" in transmission else "Механика"

    car_engine_displacement = str(response["spec"]["displacement"])
    car_type = response["spec"]["bodyName"]

    # Список фотографий (берем первые 10)
    car_photos = [
        generate_encar_photo_url(photo["path"]) for photo in response["photos"][:10]
    ]
    car_photos = [url for url in car_photos if url]

    # Дополнительные данные
    vehicle_no = response["vehicleNo"]
    vehicle_id = response["vehicleId"]

    # Форматируем
    formatted_car_date = f"01{month}{year}"
    formatted_car_type = "crossover" if car_type == "SUV" else "sedan"

    print_message(
        f"ID: {car_id}\nType: {formatted_car_type}\nDate: {formatted_car_date}\nCar Engine Displacement: {car_engine_displacement}\nPrice: {car_price} KRW"
    )

    return [
        car_price,
        car_engine_displacement,
        formatted_car_date,
        car_title,
        formatted_mileage,
        formatted_transmission,
        car_photos,
        year,
        month,
    ]


# Function to calculate the total cost
def calculate_cost(link, message):
    global car_data, car_id_external, car_month, car_year, krw_rub_rate, eur_rub_rate, rub_to_krw_rate

    print_message("ЗАПРОС НА РАСЧЁТ АВТОМОБИЛЯ")

    user_id = message.chat.id

    # Подтягиваем актуальный курс валют
    get_currency_rates()

    # Отправляем сообщение и сохраняем его ID
    processing_message = bot.send_message(
        user_id, "Обрабатываю данные. Пожалуйста подождите ⏳"
    )

    # Extract car_id from URL
    car_id = extract_car_id_from_url(link)
    if not car_id:
        # Fallback to old method
        if "fem.encar.com" in link:
            car_id_match = re.findall(r"\d+", link)
            if car_id_match:
                car_id = car_id_match[0]
            else:
                bot.delete_message(user_id, processing_message.message_id)
                send_error_message(message, "🚫 Не удалось извлечь carid из ссылки.")
                return
        else:
            parsed_url = urlparse(link)
            query_params = parse_qs(parsed_url.query)
            car_id = query_params.get("carid", [None])[0]

    car_id_external = car_id

    # Step 1: Try pan-auto.ru API first (has pre-calculated customs with HP)
    print_message(f"Пробуем получить данные с pan-auto.ru для car_id={car_id}")
    pan_auto_data = get_pan_auto_car_data(car_id)

    # Store manufacturer/model from pan-auto.ru if available (for HP caching later)
    manufacturer_from_pan = ""
    model_from_pan = ""

    # Check if pan-auto.ru has valid data (both customs AND HP must be valid)
    if pan_auto_data:
        costs_rub = pan_auto_data.get("costs", {}).get("RUB", {})
        hp = pan_auto_data.get("hp")
        manufacturer_from_pan = pan_auto_data.get("manufacturer", {}).get("translation", "")
        model_from_pan = pan_auto_data.get("model", {}).get("translation", "")

        if costs_rub and is_valid_hp(hp) and has_valid_customs(costs_rub):
            # Pan-auto.ru has this car with valid data - use their pre-calculated customs
            print_message("Данные найдены на pan-auto.ru с валидными HP и таможней, используем их")
            bot.delete_message(user_id, processing_message.message_id)
            calculate_cost_with_pan_auto(pan_auto_data, car_id, message)
            return
        else:
            # Pan-auto.ru has car but missing HP or customs - log the reason
            if not is_valid_hp(hp):
                print_message(f"pan-auto.ru: HP отсутствует или невалидный (hp={hp})")
            if not has_valid_customs(costs_rub):
                print_message(f"pan-auto.ru: Таможенные данные невалидны")

    # Pan-auto.ru doesn't have valid data - get data from Encar and ask for HP
    print_message("Данные не найдены или невалидны на pan-auto.ru, запрашиваем у Encar")
    result = get_car_info(link)
    (
        car_price,
        car_engine_displacement,
        formatted_car_date,
        car_title,
        formatted_mileage,
        formatted_transmission,
        car_photos,
        year,
        month,
    ) = result

    if not car_price and car_engine_displacement and formatted_car_date:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Написать менеджеру", url="https://t.me/GetAuto_manager_bot"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another",
            )
        )
        bot.send_message(
            user_id, "Ошибка", parse_mode="Markdown", reply_markup=keyboard
        )
        bot.delete_message(user_id, processing_message.message_id)
        return

    # Pan-auto.ru doesn't have data - need to ask user for HP
    if car_price and car_engine_displacement and formatted_car_date:
        bot.delete_message(user_id, processing_message.message_id)

        # Store car info for HP input handler
        # Use manufacturer/model from pan-auto.ru if available (for HP caching later)
        pending_hp_requests[user_id] = {
            "car_info": result,
            "car_id": car_id,
            "link": link,
            "car_title": car_title,
            "manufacturer": manufacturer_from_pan,
            "model": model_from_pan,
        }

        # Ask user for HP
        bot.send_message(
            user_id,
            f"🚗 {car_title}\n\n"
            "Автомобиль не найден в базе.\n\n"
            "Пожалуйста, введите мощность двигателя в л.с. (например: 150):",
        )
        bot.register_next_step_handler(message, process_hp_input_for_url)
        return


def calculate_cost_with_pan_auto(pan_auto_data, car_id, message):
    """
    Calculate cost using pre-calculated customs values from pan-auto.ru.
    This function uses the trusted customs data (clearanceCost, customsDuty, utilizationFee)
    directly from pan-auto.ru API response.
    """
    global car_data, usd_rate, krw_rub_rate, rub_to_krw_rate, vehicle_id, vehicle_no

    user_id = message.chat.id

    # Extract data from pan-auto.ru response
    costs_rub = pan_auto_data.get("costs", {}).get("RUB", {})

    customs_fee = costs_rub.get("clearanceCost", 0)  # sbor
    customs_duty = costs_rub.get("customsDuty", 0)    # tax
    recycling_fee = costs_rub.get("utilizationFee", 0)  # util

    hp = pan_auto_data.get("hp", 0)
    manufacturer = pan_auto_data.get("manufacturer", {}).get("translation", "")
    model = pan_auto_data.get("model", {}).get("translation", "")
    engine_volume = pan_auto_data.get("displacement", 0)

    # Extract year and month from formYear (can be "2024" or "202401" format)
    form_year = pan_auto_data.get("formYear", "")
    if form_year and len(form_year) >= 6:
        # Format: YYYYMM (e.g., "202401")
        year = int(form_year[:4])
        month = form_year[4:6]
    elif form_year and len(form_year) >= 4:
        # Format: YYYY only (e.g., "2024")
        year = int(form_year[:4])
        month = "01"  # Default to January
    else:
        year = 0
        month = "01"

    # Get car price from costs.RUB.carPriceEncar (this is the KRW price)
    price_krw = costs_rub.get("carPriceEncar", 0)
    mileage = pan_auto_data.get("mileage", 0)

    # Store vehicle info for insurance lookup
    vehicle_id = pan_auto_data.get("vehicleId", "")
    vehicle_no = pan_auto_data.get("vehicleNo", "")

    # Cache HP for future use (pan-auto.ru is a trusted source)
    if hp and manufacturer and model and engine_volume and year:
        save_hp_to_cache(manufacturer, model, int(engine_volume), year, hp)

    # Build car title
    car_title = f"{manufacturer} {model}" if manufacturer and model else f"Car ID: {car_id}"

    # Calculate age category
    age = calculate_age(year, month)
    age_formatted = (
        "до 3 лет" if age == "0-3"
        else ("от 3 до 5 лет" if age == "3-5"
        else "от 5 до 7 лет" if age == "5-7" else "от 7 лет")
    )

    price_usd = price_krw * krw_rub_rate / usd_rate
    engine_volume_formatted = f"{format_number(int(engine_volume))} cc"
    formatted_mileage = f"{format_number(mileage)} км" if mileage else "Н/Д"

    # Calculate total cost
    total_cost = (
        (price_krw * krw_rub_rate)
        + (440000 * krw_rub_rate)
        + (100000 * krw_rub_rate)
        + (350000 * krw_rub_rate)
        + (600 * usd_rate)
        + customs_duty
        + customs_fee
        + recycling_fee
        + (461 * usd_rate)
        + 50000
        + 30000
        + 8000
    )

    total_cost_usd = total_cost / usd_rate
    total_cost_krw = total_cost / krw_rub_rate

    # Store car_data for detail view
    car_data["agent_korea_rub"] = 50000
    car_data["agent_korea_usd"] = 50000 / usd_rate
    car_data["agent_korea_krw"] = 50000 / krw_rub_rate

    car_data["advance_rub"] = 1000000 * krw_rub_rate
    car_data["advance_usd"] = (1000000 * krw_rub_rate) / usd_rate
    car_data["advance_krw"] = 1000000

    car_data["car_price_krw"] = price_krw - 1000000
    car_data["car_price_usd"] = (price_krw - 1000000) * krw_rub_rate / usd_rate
    car_data["car_price_rub"] = (price_krw - 1000000) * krw_rub_rate

    car_data["dealer_korea_usd"] = 440000 * krw_rub_rate / usd_rate
    car_data["dealer_korea_krw"] = 440000
    car_data["dealer_korea_rub"] = 440000 * krw_rub_rate

    car_data["delivery_korea_usd"] = 100000 * krw_rub_rate / usd_rate
    car_data["delivery_korea_krw"] = 100000
    car_data["delivery_korea_rub"] = 100000 * krw_rub_rate

    car_data["transfer_korea_usd"] = 350000 * krw_rub_rate / usd_rate
    car_data["transfer_korea_krw"] = 350000
    car_data["transfer_korea_rub"] = 350000 * krw_rub_rate

    car_data["freight_korea_usd"] = 600
    car_data["freight_korea_krw"] = 600 * usd_rate / krw_rub_rate
    car_data["freight_korea_rub"] = 600 * usd_rate

    car_data["korea_total_usd"] = (
        ((price_krw) * krw_rub_rate / usd_rate)
        + (440000 * krw_rub_rate / usd_rate)
        + (100000 * krw_rub_rate / usd_rate)
        + (350000 * krw_rub_rate / usd_rate)
        + (600)
    )

    car_data["korea_total_krw"] = (
        (price_krw) + (440000) + (100000) + 350000 + (600 * usd_rate / krw_rub_rate)
    )

    car_data["korea_total_rub"] = (
        +(price_krw * krw_rub_rate)
        + (440000 * krw_rub_rate)
        + (100000 * krw_rub_rate)
        + (350000 * krw_rub_rate)
        + (600 * usd_rate)
    )

    # Russia expenses
    car_data["customs_duty_usd"] = customs_duty / usd_rate
    car_data["customs_duty_krw"] = customs_duty * rub_to_krw_rate
    car_data["customs_duty_rub"] = customs_duty

    car_data["customs_fee_usd"] = customs_fee / usd_rate
    car_data["customs_fee_krw"] = customs_fee / krw_rub_rate
    car_data["customs_fee_rub"] = customs_fee

    car_data["util_fee_usd"] = recycling_fee / usd_rate
    car_data["util_fee_krw"] = recycling_fee / krw_rub_rate
    car_data["util_fee_rub"] = recycling_fee

    car_data["broker_russia_usd"] = (
        ((customs_duty + customs_fee + recycling_fee) / 100) * 1.5 + 30000
    ) / usd_rate
    car_data["broker_russia_krw"] = (
        ((customs_duty + customs_fee + recycling_fee) / 100) * 1.5 + 30000
    ) * rub_to_krw_rate
    car_data["broker_russia_rub"] = (
        (customs_duty + customs_fee + recycling_fee) / 100
    ) * 1.5 + 30000

    car_data["svh_russia_usd"] = 50000 / usd_rate
    car_data["svh_russia_krw"] = 50000 / krw_rub_rate
    car_data["svh_russia_rub"] = 50000

    car_data["lab_russia_usd"] = 30000 / usd_rate
    car_data["lab_russia_krw"] = 30000 / krw_rub_rate
    car_data["lab_russia_rub"] = 30000

    car_data["perm_registration_russia_usd"] = 8000 / usd_rate
    car_data["perm_registration_russia_krw"] = 8000 / krw_rub_rate
    car_data["perm_registration_russia_rub"] = 8000

    preview_link = f"https://fem.encar.com/cars/detail/{car_id}"

    # Build result message
    result_message = (
        f"{car_title}\n\n"
        f"Возраст: {age_formatted} (дата регистрации: {month}/{year})\n"
        f"Пробег: {formatted_mileage}\n"
        f"Стоимость автомобиля в Корее: ₩{format_number(price_krw)} | ${format_number(price_usd)}\n"
        f"Объём двигателя: {engine_volume_formatted}\n"
        f"Мощность: {hp} л.с.\n"
        f"🟰 <b>Стоимость под ключ до Владивостока</b>:\n<b>${format_number(total_cost_usd)}</b> | <b>₩{format_number(total_cost_krw)}</b> | <b>{format_number(total_cost)} ₽</b>\n\n"
        f"‼️ <b>Доставку до вашего города уточняйте у менеджера @GetAuto_manager_bot</b>\n\n"
        f"Стоимость под ключ актуальна на сегодняшний день, возможны колебания курса на 3-5% от стоимости авто, на момент покупки автомобиля\n\n"
        f"🔗 <a href='{preview_link}'>Ссылка на автомобиль</a>\n\n"
        f"🔗 <a href='https://t.me/Getauto_kor'>Официальный телеграм канал</a>\n"
    )

    # Keyboard with actions
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Выплаты по ДТП",
            callback_data="technical_report",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/GetAuto_manager_bot"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Расчёт другого автомобиля",
            callback_data="calculate_another",
        )
    )

    # Send photos if available
    photos = pan_auto_data.get("photos", [])
    photo_msg_id = None
    if photos:
        # Extract URLs from photo data
        photo_urls = []
        for photo_data in photos:
            photo_url = (
                photo_data.get("url", "")
                if isinstance(photo_data, dict)
                else str(photo_data)
            )
            if photo_url:
                photo_urls.append(photo_url)

        # Sort by numeric key and limit to 10
        photo_urls = sort_photo_urls(photo_urls)[:10]

        media_group = []
        for photo_url in photo_urls:
            try:
                response = requests.get(photo_url)
                if response.status_code == 200:
                    photo = BytesIO(response.content)
                    media_group.append(types.InputMediaPhoto(photo))
            except Exception as e:
                print(f"Error loading photo: {e}")

        if media_group:
            sent_photos = bot.send_media_group(message.chat.id, media_group)
            if sent_photos:
                photo_msg_id = sent_photos[-1].message_id

    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
        reply_to_message_id=photo_msg_id,
    )


def process_hp_input_for_url(message):
    """
    Handle HP input when pan-auto.ru doesn't have the car.
    Only MANAGERS can save HP to database.
    After HP validation, shows fuel type selection keyboard.
    """
    user_id = message.chat.id
    user_input = message.text.strip()

    # Validate HP input
    if not user_input.isdigit() or not (50 <= int(user_input) <= 1000):
        bot.send_message(
            user_id,
            "Пожалуйста, введите корректное значение мощности (от 50 до 1000 л.с.):"
        )
        bot.register_next_step_handler(message, process_hp_input_for_url)
        return

    hp = int(user_input)

    if user_id not in pending_hp_requests:
        bot.send_message(user_id, "Ошибка: данные автомобиля не найдены. Попробуйте снова.")
        return

    # Store HP in pending data (don't pop yet - wait for fuel type selection)
    pending_hp_requests[user_id]["hp"] = hp

    # Get data for manager HP caching
    pending_data = pending_hp_requests[user_id]
    car_info = pending_data["car_info"]
    manufacturer = pending_data.get("manufacturer", "")
    model = pending_data.get("model", "")

    # Unpack car info to get engine displacement and year for caching
    (
        car_price,
        car_engine_displacement,
        formatted_car_date,
        _,  # car_title from encar
        formatted_mileage,
        formatted_transmission,
        car_photos,
        year,
        month,
    ) = car_info

    car_engine_displacement = int(car_engine_displacement)
    full_year = int(f"20{year}")

    # ONLY save HP to cache if user is a MANAGER (trusted source)
    if user_id in MANAGERS and manufacturer and model:
        save_hp_to_cache(manufacturer, model, car_engine_displacement, full_year, hp)
        bot.send_message(user_id, f"✅ Мощность {hp} л.с. сохранена в базу данных.")

    # Show fuel type selection keyboard
    bot.send_message(
        user_id,
        "Выберите тип двигателя:",
        reply_markup=create_fuel_type_keyboard()
    )


def complete_url_calculation(user_id, message):
    """
    Complete the URL-based calculation after HP and fuel type have been selected.
    Called from the fuel type callback handler.
    """
    global car_data, usd_rate, krw_rub_rate, rub_to_krw_rate, vehicle_id, vehicle_no

    pending_data = pending_hp_requests.pop(user_id, None)

    if not pending_data:
        bot.send_message(user_id, "Ошибка: данные автомобиля не найдены. Попробуйте снова.")
        return

    car_info = pending_data["car_info"]
    car_id = pending_data["car_id"]
    car_title = pending_data.get("car_title", f"Car ID: {car_id}")
    hp = pending_data.get("hp", 1)
    fuel_type = pending_data.get("fuel_type", FUEL_TYPE_GASOLINE)

    # Unpack car info
    (
        car_price,
        car_engine_displacement,
        formatted_car_date,
        _,  # car_title from encar
        formatted_mileage,
        formatted_transmission,
        car_photos,
        year,
        month,
    ) = car_info

    car_engine_displacement = int(car_engine_displacement)
    price_krw = int(car_price) * 10000
    full_year = int(f"20{year}")

    # Call calcus.ru with actual HP and fuel type
    response = get_customs_fees(
        car_engine_displacement,
        price_krw,
        full_year,
        month,
        power=hp,
        engine_type=fuel_type,
    )

    if not response:
        bot.send_message(user_id, "Ошибка при расчёте таможенных платежей. Попробуйте снова.")
        return

    # Extract customs values
    customs_fee = clean_number(response["sbor"])
    customs_duty = clean_number(response["tax"])
    recycling_fee = clean_number(response["util"])

    # Calculate age
    age = calculate_age(full_year, month)
    age_formatted = (
        "до 3 лет" if age == "0-3"
        else ("от 3 до 5 лет" if age == "3-5"
        else "от 5 до 7 лет" if age == "5-7" else "от 7 лет")
    )

    price_usd = price_krw * krw_rub_rate / usd_rate
    engine_volume_formatted = f"{format_number(car_engine_displacement)} cc"

    # Calculate total cost
    total_cost = (
        (price_krw * krw_rub_rate)
        + (440000 * krw_rub_rate)
        + (100000 * krw_rub_rate)
        + (350000 * krw_rub_rate)
        + (600 * usd_rate)
        + customs_duty
        + customs_fee
        + recycling_fee
        + (461 * usd_rate)
        + 50000
        + 30000
        + 8000
    )

    total_cost_usd = total_cost / usd_rate
    total_cost_krw = total_cost / krw_rub_rate

    # Store car_data for detail view
    car_data["agent_korea_rub"] = 50000
    car_data["agent_korea_usd"] = 50000 / usd_rate
    car_data["agent_korea_krw"] = 50000 / krw_rub_rate

    car_data["advance_rub"] = 1000000 * krw_rub_rate
    car_data["advance_usd"] = (1000000 * krw_rub_rate) / usd_rate
    car_data["advance_krw"] = 1000000

    car_data["car_price_krw"] = price_krw - 1000000
    car_data["car_price_usd"] = (price_krw - 1000000) * krw_rub_rate / usd_rate
    car_data["car_price_rub"] = (price_krw - 1000000) * krw_rub_rate

    car_data["dealer_korea_usd"] = 440000 * krw_rub_rate / usd_rate
    car_data["dealer_korea_krw"] = 440000
    car_data["dealer_korea_rub"] = 440000 * krw_rub_rate

    car_data["delivery_korea_usd"] = 100000 * krw_rub_rate / usd_rate
    car_data["delivery_korea_krw"] = 100000
    car_data["delivery_korea_rub"] = 100000 * krw_rub_rate

    car_data["transfer_korea_usd"] = 350000 * krw_rub_rate / usd_rate
    car_data["transfer_korea_krw"] = 350000
    car_data["transfer_korea_rub"] = 350000 * krw_rub_rate

    car_data["freight_korea_usd"] = 600
    car_data["freight_korea_krw"] = 600 * usd_rate / krw_rub_rate
    car_data["freight_korea_rub"] = 600 * usd_rate

    car_data["korea_total_usd"] = (
        ((price_krw) * krw_rub_rate / usd_rate)
        + (440000 * krw_rub_rate / usd_rate)
        + (100000 * krw_rub_rate / usd_rate)
        + (350000 * krw_rub_rate / usd_rate)
        + (600)
    )

    car_data["korea_total_krw"] = (
        (price_krw) + (440000) + (100000) + 350000 + (600 * usd_rate / krw_rub_rate)
    )

    car_data["korea_total_rub"] = (
        +(price_krw * krw_rub_rate)
        + (440000 * krw_rub_rate)
        + (100000 * krw_rub_rate)
        + (350000 * krw_rub_rate)
        + (600 * usd_rate)
    )

    # Russia expenses
    car_data["customs_duty_usd"] = customs_duty / usd_rate
    car_data["customs_duty_krw"] = customs_duty * rub_to_krw_rate
    car_data["customs_duty_rub"] = customs_duty

    car_data["customs_fee_usd"] = customs_fee / usd_rate
    car_data["customs_fee_krw"] = customs_fee / krw_rub_rate
    car_data["customs_fee_rub"] = customs_fee

    car_data["util_fee_usd"] = recycling_fee / usd_rate
    car_data["util_fee_krw"] = recycling_fee / krw_rub_rate
    car_data["util_fee_rub"] = recycling_fee

    car_data["broker_russia_usd"] = (
        ((customs_duty + customs_fee + recycling_fee) / 100) * 1.5 + 30000
    ) / usd_rate
    car_data["broker_russia_krw"] = (
        ((customs_duty + customs_fee + recycling_fee) / 100) * 1.5 + 30000
    ) * rub_to_krw_rate
    car_data["broker_russia_rub"] = (
        (customs_duty + customs_fee + recycling_fee) / 100
    ) * 1.5 + 30000

    car_data["svh_russia_usd"] = 50000 / usd_rate
    car_data["svh_russia_krw"] = 50000 / krw_rub_rate
    car_data["svh_russia_rub"] = 50000

    car_data["lab_russia_usd"] = 30000 / usd_rate
    car_data["lab_russia_krw"] = 30000 / krw_rub_rate
    car_data["lab_russia_rub"] = 30000

    car_data["perm_registration_russia_usd"] = 8000 / usd_rate
    car_data["perm_registration_russia_krw"] = 8000 / krw_rub_rate
    car_data["perm_registration_russia_rub"] = 8000

    preview_link = f"https://fem.encar.com/cars/detail/{car_id}"

    # Get fuel type name for display
    fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type, "Бензин")

    # Build result message
    result_message = (
        f"{car_title}\n\n"
        f"Возраст: {age_formatted} (дата регистрации: {month}/{year})\n"
        f"Пробег: {formatted_mileage}\n"
        f"Стоимость автомобиля в Корее: ₩{format_number(price_krw)} | ${format_number(price_usd)}\n"
        f"Объём двигателя: {engine_volume_formatted}\n"
        f"Мощность: {hp} л.с.\n"
        f"Тип двигателя: {fuel_type_name}\n"
        f"🟰 <b>Стоимость под ключ до Владивостока</b>:\n<b>${format_number(total_cost_usd)}</b> | <b>₩{format_number(total_cost_krw)}</b> | <b>{format_number(total_cost)} ₽</b>\n\n"
        f"‼️ <b>Доставку до вашего города уточняйте у менеджера @GetAuto_manager_bot</b>\n\n"
        f"Стоимость под ключ актуальна на сегодняшний день, возможны колебания курса на 3-5% от стоимости авто, на момент покупки автомобиля\n\n"
        f"🔗 <a href='{preview_link}'>Ссылка на автомобиль</a>\n\n"
        f"🔗 <a href='https://t.me/Getauto_kor'>Официальный телеграм канал</a>\n"
    )

    # Keyboard with actions
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Выплаты по ДТП",
            callback_data="technical_report",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/GetAuto_manager_bot"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Расчёт другого автомобиля",
            callback_data="calculate_another",
        )
    )

    # Send photos
    photo_msg_id = None
    if car_photos:
        media_group = []
        for photo_url in sort_photo_urls(car_photos)[:10]:
            try:
                resp = requests.get(photo_url)
                if resp.status_code == 200:
                    photo = BytesIO(resp.content)
                    media_group.append(types.InputMediaPhoto(photo))
            except Exception as e:
                print(f"Error loading photo: {e}")

        if media_group:
            sent_photos = bot.send_media_group(message.chat.id, media_group)
            if sent_photos:
                photo_msg_id = sent_photos[-1].message_id

    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
        reply_to_message_id=photo_msg_id,
    )


#######################
# China (Che168) calculation functions
#######################

def calculate_china_cost(link, message):
    """
    Calculate import cost for a car from Che168.com (China).
    """
    global car_data, cny_rub_rate, usd_rate

    print_message("ЗАПРОС НА РАСЧЁТ АВТОМОБИЛЯ ИЗ КИТАЯ")

    user_id = message.chat.id

    # Fetch current currency rates
    get_currency_rates()

    if cny_rub_rate is None:
        bot.send_message(
            user_id,
            "Ошибка: не удалось получить курс юаня. Попробуйте позже."
        )
        return

    # Send processing message
    processing_message = bot.send_message(
        user_id, "Обрабатываю данные. Пожалуйста подождите ⏳"
    )

    # Extract car ID from URL
    car_id = extract_car_id_from_che168_url(link)
    if not car_id:
        bot.delete_message(user_id, processing_message.message_id)
        send_error_message(message, "Не удалось извлечь ID автомобиля из ссылки.")
        return

    # Fetch car info from Che168 API (with proxy fallback)
    car_info = get_che168_car_info_with_fallback(car_id)
    if not car_info:
        bot.delete_message(user_id, processing_message.message_id)
        send_error_message(message, "Не удалось получить данные об автомобиле. Попробуйте позже.")
        return

    # Extract data from car_info
    price_cny = car_info["price_cny"]
    displacement_cc = car_info["displacement_cc"]
    year = car_info["first_reg_year"]
    month = car_info["first_reg_month"]
    car_name = car_info["car_name"]
    fuel_type_code = car_info["fuel_type_code"]
    fuel_type_ru = car_info["fuel_type_ru"]
    mileage_km = car_info["mileage_km"]
    city_name = car_info["city_name"]
    photos = car_info["photos"]
    gearbox = car_info.get("gearbox", "")
    horsepower = car_info.get("horsepower")

    # Delete processing message
    bot.delete_message(user_id, processing_message.message_id)

    # Store pending data and ask for HP
    pending_china_hp_requests[user_id] = {
        "car_info": car_info,
        "car_id": car_id,
        "link": link,
        "price_cny": price_cny,
        "displacement_cc": displacement_cc,
        "year": year,
        "month": month,
        "car_name": car_name,
        "fuel_type_code": fuel_type_code,
        "fuel_type_ru": fuel_type_ru,
        "photos": photos,
        "horsepower": horsepower,
    }

    # Check if HP was successfully extracted and is valid
    if horsepower and 50 <= horsepower <= 1000:
        # Use auto-extracted HP, skip manual input
        pending_china_hp_requests[user_id]["hp"] = horsepower
        logging.info(f"Using auto-extracted HP: {horsepower} for user {user_id}")

        # Check if fuel type is also valid
        valid_fuel_types = {1, 2, 4, 5, 6}
        if fuel_type_code in valid_fuel_types:
            # Both HP and fuel type are valid - skip to calculation
            logging.info(f"Using auto-extracted fuel type: {fuel_type_code} ({fuel_type_ru}) for user {user_id}")

            # Send info message
            bot.send_message(
                user_id,
                f"🚗 {car_name}\n"
                f"📍 {city_name}\n"
                f"💰 ¥{price_cny:,}\n"
                f"🐎 {horsepower} л.с.\n"
                f"⛽ {fuel_type_ru}\n\n"
                "⏳ Выполняю расчёт..."
            )

            # Skip fuel type selection, proceed to calculation
            complete_china_calculation(user_id, message)
        else:
            # Fuel type unknown - show selection keyboard
            keyboard = create_fuel_type_keyboard()
            bot.send_message(
                user_id,
                f"🚗 {car_name}\n"
                f"📍 {city_name}\n"
                f"💰 ¥{price_cny:,}\n"
                f"🐎 {horsepower} л.с.\n\n"
                "Выберите тип двигателя:",
                reply_markup=keyboard
            )
    else:
        # HP not available or invalid, ask user for input
        bot.send_message(
            user_id,
            f"🚗 {car_name}\n"
            f"📍 {city_name}\n"
            f"💰 ¥{price_cny:,}\n\n"
            "Пожалуйста, введите мощность двигателя в л.с. (например: 340):",
        )
        bot.register_next_step_handler(message, process_china_hp_input)


def process_china_hp_input(message):
    """Handle HP input for China car calculation."""
    user_id = message.chat.id
    user_input = message.text.strip()

    # Validate HP input
    if not user_input.isdigit() or not (50 <= int(user_input) <= 1000):
        bot.send_message(
            user_id,
            "Пожалуйста, введите корректное значение мощности (от 50 до 1000 л.с.):"
        )
        bot.register_next_step_handler(message, process_china_hp_input)
        return

    hp = int(user_input)

    if user_id not in pending_china_hp_requests:
        bot.send_message(user_id, "Ошибка: данные автомобиля не найдены. Попробуйте снова.")
        return

    # Store HP and show fuel type selection
    pending_china_hp_requests[user_id]["hp"] = hp

    # Show fuel type keyboard
    keyboard = create_fuel_type_keyboard()
    bot.send_message(
        user_id,
        "Выберите тип двигателя:",
        reply_markup=keyboard
    )
    # The fuel type selection will be handled in callback_query_handler


def complete_china_calculation(user_id, message):
    """Complete China car cost calculation after HP and fuel type are selected."""
    global car_data, cny_rub_rate, usd_rate

    if user_id not in pending_china_hp_requests:
        bot.send_message(user_id, "Ошибка: данные автомобиля не найдены.")
        return

    pending_data = pending_china_hp_requests.pop(user_id)

    price_cny = pending_data["price_cny"]
    displacement_cc = pending_data["displacement_cc"]
    year = pending_data["year"]
    month = pending_data["month"]
    car_name = pending_data["car_name"]
    fuel_type_code = pending_data.get("fuel_type", pending_data.get("fuel_type_code", 1))
    hp = pending_data["hp"]
    photos = pending_data.get("photos", [])
    link = pending_data.get("link", "")
    fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type_code, "Бензин")

    # Call calcus.ru API with CNY currency
    response = get_customs_fees(
        displacement_cc,
        price_cny,
        year,
        month,
        power=hp,
        engine_type=fuel_type_code,
        currency="CNY",
    )

    if not response:
        bot.send_message(user_id, "Ошибка при расчёте таможенных платежей. Попробуйте снова.")
        return

    # Extract customs values
    customs_fee = clean_number(response["sbor"])
    customs_duty = clean_number(response["tax"])
    recycling_fee = clean_number(response["util"])

    # Calculate costs
    first_payment_rub = CHINA_FIRST_PAYMENT * cny_rub_rate
    car_price_after_deposit = price_cny - CHINA_FIRST_PAYMENT
    dealer_fee_rub = CHINA_DEALER_FEE * cny_rub_rate
    delivery_rub = CHINA_DELIVERY * cny_rub_rate

    china_total_cny = car_price_after_deposit + CHINA_DEALER_FEE + CHINA_DELIVERY
    china_total_rub = china_total_cny * cny_rub_rate

    russia_expenses_rub = (
        customs_duty + customs_fee + recycling_fee +
        CHINA_AGENT_FEE + CHINA_BROKER_FEE + CHINA_SVH_FEE + CHINA_LAB_FEE
    )

    total_cost_rub = first_payment_rub + china_total_rub + russia_expenses_rub + 100000
    total_cost_usd = total_cost_rub / usd_rate
    total_cost_cny = total_cost_rub / cny_rub_rate
    price_usd = int(price_cny * cny_rub_rate / usd_rate)

    # Calculate age
    age = calculate_age(year, month)
    age_formatted = (
        "до 3 лет" if age == "0-3"
        else ("от 3 до 5 лет" if age == "3-5"
        else "от 5 до 7 лет" if age == "5-7" else "от 7 лет")
    )

    # Store car_data for detail view
    car_data["source"] = "che168"
    car_data["first_payment_cny"] = CHINA_FIRST_PAYMENT
    car_data["first_payment_rub"] = first_payment_rub
    car_data["car_price_cny"] = car_price_after_deposit
    car_data["car_price_rub"] = car_price_after_deposit * cny_rub_rate
    car_data["dealer_china_cny"] = CHINA_DEALER_FEE
    car_data["dealer_china_rub"] = dealer_fee_rub
    car_data["delivery_china_cny"] = CHINA_DELIVERY
    car_data["delivery_china_rub"] = delivery_rub
    car_data["china_total_cny"] = china_total_cny
    car_data["china_total_rub"] = china_total_rub
    car_data["customs_duty_rub"] = customs_duty
    car_data["customs_fee_rub"] = customs_fee
    car_data["util_fee_rub"] = recycling_fee
    car_data["agent_russia_rub"] = CHINA_AGENT_FEE
    car_data["broker_russia_rub"] = CHINA_BROKER_FEE
    car_data["svh_russia_rub"] = CHINA_SVH_FEE
    car_data["lab_russia_rub"] = CHINA_LAB_FEE
    car_data["total_cost_rub"] = total_cost_rub
    car_data["total_cost_usd"] = total_cost_usd
    car_data["total_cost_cny"] = total_cost_cny
    car_data["link"] = link
    car_data["car_name"] = car_name
    car_data["fuel_type_name"] = fuel_type_name

    # Format result message (matching Korean format)
    result_message = (
        f"{car_name}\n\n"
        f"Возраст: {age_formatted} (дата регистрации: {month:02d}/{year})\n"
        f"Стоимость автомобиля в Китае: ¥{format_number(price_cny)} | ${format_number(price_usd)}\n"
        f"Объём двигателя: {format_number(displacement_cc)} cc\n"
        f"Мощность: {hp} л.с.\n"
        f"Тип двигателя: {fuel_type_name}\n"
        f"🟰 <b>Стоимость под ключ до Хоргоса</b>:\n<b>${format_number(int(total_cost_usd))}</b> | <b>¥{format_number(int(total_cost_cny))}</b> | <b>{format_number(int(total_cost_rub))} ₽</b>\n"
        f"<i>(Хоргос — крайний город Китая, оттуда до европейской части РФ транзитом через Казахстан 10-14 дней на автовозе)</i>\n\n"
        f"Стоимость логистики по Китаю взята усреднённая, чтобы быстро посчитать сумму. Китай огромный, поэтому взяли усреднённый прайс доставки до Хоргоса.\n\n"
        f"На момент индивидуального просчёта авто, наши менеджеры сделают пересчёт более конкретно.\n\n"
        f"‼️ <b>Доставку до вашего города уточняйте у менеджера @GetAuto_manager_bot</b>\n\n"
        f"Стоимость под ключ актуальна на сегодняшний день, возможны колебания курса на 3-5% от стоимости авто, на момент покупки автомобиля\n\n"
        f"🔗 <a href='{link}'>Ссылка на автомобиль</a>\n\n"
        f"🔗 <a href='https://t.me/Getauto_kor'>Официальный телеграм канал</a>\n"
    )

    # Create keyboard
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail_china")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/GetAuto_manager_bot"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Расчёт другого автомобиля",
            callback_data="calculate_another",
        )
    )

    # Send photos if available
    photo_msg_id = None
    if photos:
        media_group = []
        for photo_url in photos[:10]:
            try:
                response = requests.get(photo_url, timeout=10)
                if response.status_code == 200:
                    photo = BytesIO(response.content)
                    media_group.append(types.InputMediaPhoto(photo))
            except Exception as e:
                print(f"Error loading photo: {e}")

        if media_group:
            sent_photos = bot.send_media_group(message.chat.id, media_group)
            if sent_photos:
                photo_msg_id = sent_photos[-1].message_id

    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
        reply_to_message_id=photo_msg_id,
    )


#######################
# China manual calculation functions
#######################

def process_china_manual_month(message):
    """Process month input for manual China car calculation."""
    global user_manual_china_input

    user_id = message.chat.id
    user_input = message.text.strip()

    # Validate month
    if not user_input.isdigit() or not (1 <= int(user_input) <= 12):
        bot.send_message(user_id, "Пожалуйста, введите корректный месяц (от 1 до 12):")
        bot.register_next_step_handler(message, process_china_manual_month)
        return

    user_manual_china_input[user_id]["month"] = int(user_input)
    bot.send_message(user_id, "Введите год первой регистрации (например, 2020):")
    bot.register_next_step_handler(message, process_china_manual_year)


def process_china_manual_year(message):
    """Process year input for manual China car calculation."""
    global user_manual_china_input

    user_id = message.chat.id
    user_input = message.text.strip()

    # Validate year
    current_year = datetime.datetime.now().year
    if not user_input.isdigit() or not (2010 <= int(user_input) <= current_year):
        bot.send_message(user_id, f"Пожалуйста, введите корректный год (от 2010 до {current_year}):")
        bot.register_next_step_handler(message, process_china_manual_year)
        return

    user_manual_china_input[user_id]["year"] = int(user_input)
    bot.send_message(user_id, "Введите объём двигателя в литрах (например, 3.0):")
    bot.register_next_step_handler(message, process_china_manual_engine)


def process_china_manual_engine(message):
    """Process engine volume input for manual China car calculation."""
    global user_manual_china_input

    user_id = message.chat.id
    user_input = message.text.strip().replace(",", ".")

    try:
        engine_liters = float(user_input)
        if not (0.5 <= engine_liters <= 10.0):
            raise ValueError("Engine volume out of range")
    except ValueError:
        bot.send_message(user_id, "Пожалуйста, введите корректный объём двигателя (от 0.5 до 10.0 литров):")
        bot.register_next_step_handler(message, process_china_manual_engine)
        return

    user_manual_china_input[user_id]["engine_liters"] = engine_liters
    user_manual_china_input[user_id]["engine_cc"] = int(engine_liters * 1000)
    bot.send_message(user_id, "Введите цену автомобиля в юанях (например, 303800):")
    bot.register_next_step_handler(message, process_china_manual_price)


def process_china_manual_price(message):
    """Process price input for manual China car calculation."""
    global user_manual_china_input

    user_id = message.chat.id
    user_input = message.text.strip().replace(" ", "").replace(",", "")

    try:
        price_cny = int(user_input)
        if price_cny <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        bot.send_message(user_id, "Пожалуйста, введите корректную цену в юанях:")
        bot.register_next_step_handler(message, process_china_manual_price)
        return

    user_manual_china_input[user_id]["price_cny"] = price_cny
    bot.send_message(user_id, "Введите мощность двигателя в л.с. (например, 340):")
    bot.register_next_step_handler(message, process_china_manual_hp)


def process_china_manual_hp(message):
    """Process HP input for manual China car calculation."""
    global user_manual_china_input

    user_id = message.chat.id
    user_input = message.text.strip()

    if not user_input.isdigit() or not (50 <= int(user_input) <= 1000):
        bot.send_message(user_id, "Пожалуйста, введите корректное значение мощности (от 50 до 1000 л.с.):")
        bot.register_next_step_handler(message, process_china_manual_hp)
        return

    user_manual_china_input[user_id]["hp"] = int(user_input)

    # Show fuel type keyboard
    keyboard = create_fuel_type_keyboard()
    bot.send_message(
        user_id,
        "Выберите тип двигателя:",
        reply_markup=keyboard
    )
    # The fuel type selection will be handled in callback_query_handler


def calculate_manual_china_cost(user_id):
    """Calculate China car import cost from manual input."""
    global car_data, cny_rub_rate, usd_rate

    if user_id not in user_manual_china_input:
        bot.send_message(user_id, "Ошибка: данные не найдены.")
        return

    # Fetch current rates
    get_currency_rates()

    if cny_rub_rate is None:
        bot.send_message(user_id, "Ошибка: не удалось получить курс юаня.")
        return

    data = user_manual_china_input.pop(user_id)

    month = data["month"]
    year = data["year"]
    engine_cc = data["engine_cc"]
    price_cny = data["price_cny"]
    hp = data["hp"]
    fuel_type = data.get("fuel_type", FUEL_TYPE_GASOLINE)

    # Call calcus.ru API
    response = get_customs_fees(
        engine_cc,
        price_cny,
        year,
        month,
        power=hp,
        engine_type=fuel_type,
        currency="CNY",
    )

    if not response:
        bot.send_message(user_id, "Ошибка при расчёте таможенных платежей. Попробуйте снова.")
        return

    # Extract customs values
    customs_fee = clean_number(response["sbor"])
    customs_duty = clean_number(response["tax"])
    recycling_fee = clean_number(response["util"])

    # Calculate costs
    first_payment_rub = CHINA_FIRST_PAYMENT * cny_rub_rate
    car_price_after_deposit = price_cny - CHINA_FIRST_PAYMENT
    dealer_fee_rub = CHINA_DEALER_FEE * cny_rub_rate
    delivery_rub = CHINA_DELIVERY * cny_rub_rate

    china_total_cny = car_price_after_deposit + CHINA_DEALER_FEE + CHINA_DELIVERY
    china_total_rub = china_total_cny * cny_rub_rate

    russia_expenses_rub = (
        customs_duty + customs_fee + recycling_fee +
        CHINA_AGENT_FEE + CHINA_BROKER_FEE + CHINA_SVH_FEE + CHINA_LAB_FEE
    )

    total_cost_rub = first_payment_rub + china_total_rub + russia_expenses_rub + 100000
    total_cost_usd = total_cost_rub / usd_rate
    total_cost_cny = total_cost_rub / cny_rub_rate
    price_usd = int(price_cny * cny_rub_rate / usd_rate)

    # Calculate age
    age = calculate_age(year, month)
    age_formatted = (
        "до 3 лет" if age == "0-3"
        else ("от 3 до 5 лет" if age == "3-5"
        else "от 5 до 7 лет" if age == "5-7" else "от 7 лет")
    )

    fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type, "Бензин")

    # Store car_data for detail view
    car_data["source"] = "che168_manual"
    car_data["first_payment_cny"] = CHINA_FIRST_PAYMENT
    car_data["first_payment_rub"] = first_payment_rub
    car_data["car_price_cny"] = car_price_after_deposit
    car_data["car_price_rub"] = car_price_after_deposit * cny_rub_rate
    car_data["dealer_china_cny"] = CHINA_DEALER_FEE
    car_data["dealer_china_rub"] = dealer_fee_rub
    car_data["delivery_china_cny"] = CHINA_DELIVERY
    car_data["delivery_china_rub"] = delivery_rub
    car_data["china_total_cny"] = china_total_cny
    car_data["china_total_rub"] = china_total_rub
    car_data["customs_duty_rub"] = customs_duty
    car_data["customs_fee_rub"] = customs_fee
    car_data["util_fee_rub"] = recycling_fee
    car_data["agent_russia_rub"] = CHINA_AGENT_FEE
    car_data["broker_russia_rub"] = CHINA_BROKER_FEE
    car_data["svh_russia_rub"] = CHINA_SVH_FEE
    car_data["lab_russia_rub"] = CHINA_LAB_FEE
    car_data["total_cost_rub"] = total_cost_rub
    car_data["total_cost_usd"] = total_cost_usd
    car_data["total_cost_cny"] = total_cost_cny
    car_data["fuel_type_name"] = fuel_type_name

    # Format result message (matching Korean manual format)
    result_message = (
        f"Возраст: {age_formatted}\n"
        f"Стоимость автомобиля в Китае: ¥{format_number(price_cny)}\n"
        f"Объём двигателя: {format_number(engine_cc)} cc\n"
        f"Мощность: {hp} л.с.\n"
        f"Тип двигателя: {fuel_type_name}\n\n"
        f"Примерная стоимость автомобиля под ключ до Хоргоса:\n"
        f"<b>¥{format_number(int(total_cost_cny))}</b> | "
        f"<b>{format_number(int(total_cost_rub))} ₽</b>\n"
        f"<i>(Хоргос — крайний город Китая, оттуда до европейской части РФ транзитом через Казахстан 10-14 дней на автовозе)</i>\n\n"
        f"Стоимость логистики по Китаю взята усреднённая, чтобы быстро посчитать сумму. Китай огромный, поэтому взяли усреднённый прайс доставки до Хоргоса.\n\n"
        f"На момент индивидуального просчёта авто, наши менеджеры сделают пересчёт более конкретно.\n\n"
        "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у менеджера @GetAuto_manager_bot\n\n"
        "🔗 <a href='https://t.me/Getauto_kor'>Официальный телеграм канал</a>\n"
    )

    # Create keyboard
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail_china_manual")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Рассчитать другой автомобиль", callback_data="calculate_another_manual"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/GetAuto_manager_bot"
        )
    )

    bot.send_message(
        user_id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# Function to get insurance total
def get_insurance_total():
    global car_id_external, vehicle_no, vehicle_id

    print_message("[ЗАПРОС] ТЕХНИЧЕСКИЙ ОТЧËТ ОБ АВТОМОБИЛЕ")

    formatted_vehicle_no = urllib.parse.quote(str(vehicle_no).strip())
    url = f"https://api.encar.com/v1/readside/record/vehicle/{str(vehicle_id)}/open?vehicleNo={formatted_vehicle_no}"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "http://www.encar.com/",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        }

        response = requests.get(url, headers)
        json_response = response.json()

        # Форматируем данные
        damage_to_my_car = json_response["myAccidentCost"]
        damage_to_other_car = json_response["otherAccidentCost"]

        print(
            f"Выплаты по представленному автомобилю: {format_number(damage_to_my_car)}"
        )
        print(f"Выплаты другому автомобилю: {format_number(damage_to_other_car)}")

        return [format_number(damage_to_my_car), format_number(damage_to_other_car)]

    except Exception as e:
        print(f"Произошла ошибка при получении данных: {e}")
        return ["", ""]


# Callback query handler
@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    global car_data, car_id_external, usd_rate

    # Обработка пагинации статистики
    if call.data.startswith("stats_page_"):
        # Проверяем, что пользователь - администратор
        if call.from_user.id not in admins:
            bot.answer_callback_query(call.id, "❌ У вас нет доступа к этой команде.")
            return
        
        try:
            page = int(call.data.replace("stats_page_", ""))
            send_stats_page(call.from_user.id, page, call.message.message_id)
            bot.answer_callback_query(call.id)
        except Exception as e:
            bot.answer_callback_query(call.id, "❌ Ошибка при переключении страницы")
            logging.error(f"Ошибка пагинации статистики: {e}")
        return
    
    elif call.data == "stats_current":
        # Для кнопки с текущей страницей - просто закрываем уведомление
        bot.answer_callback_query(call.id)
        return

    elif call.data.startswith("fuel_"):
        # Handle fuel type selection for all calculation flows
        user_id = call.message.chat.id
        fuel_type = int(call.data.split("_")[1])
        fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type, "Бензин")

        # Check which flow the user is in
        if user_id in user_manual_input and "price_krw" in user_manual_input[user_id]:
            # Korea manual calculation flow
            user_manual_input[user_id]["fuel_type"] = fuel_type
            bot.answer_callback_query(call.id, f"Выбран тип: {fuel_type_name}")
            # Delete the fuel type selection message
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            calculate_manual_cost(user_id)
        elif user_id in pending_hp_requests and "hp" in pending_hp_requests[user_id]:
            # Korea URL fallback flow
            pending_hp_requests[user_id]["fuel_type"] = fuel_type
            bot.answer_callback_query(call.id, f"Выбран тип: {fuel_type_name}")
            # Delete the fuel type selection message
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            complete_url_calculation(user_id, call.message)
        elif user_id in user_manual_china_input and "hp" in user_manual_china_input[user_id]:
            # China manual calculation flow
            user_manual_china_input[user_id]["fuel_type"] = fuel_type
            bot.answer_callback_query(call.id, f"Выбран тип: {fuel_type_name}")
            # Delete the fuel type selection message
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            calculate_manual_china_cost(user_id)
        elif user_id in pending_china_hp_requests and "hp" in pending_china_hp_requests[user_id]:
            # China URL flow
            pending_china_hp_requests[user_id]["fuel_type"] = fuel_type
            bot.answer_callback_query(call.id, f"Выбран тип: {fuel_type_name}")
            # Delete the fuel type selection message
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            complete_china_calculation(user_id, call.message)
        else:
            bot.answer_callback_query(call.id, "Ошибка: данные не найдены")
        return

    elif call.data.startswith("detail_china"):
        # Detail view for Chinese car calculations
        print_message("[ЗАПРОС] ДЕТАЛИЗАЦИЯ РАСЧËТА (КИТАЙ)")

        detail_message = (
            f"<i>ПЕРВАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Задаток (бронь авто):\n<b>¥{format_number(car_data['first_payment_cny'])}</b> | <b>{format_number(int(car_data['first_payment_rub']))} ₽</b>\n\n\n"
            f"<i>ВТОРАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Стоимость авто (минус задаток):\n<b>¥{format_number(car_data['car_price_cny'])}</b> | <b>{format_number(int(car_data['car_price_rub']))} ₽</b>\n\n"
            f"Дилерский сбор:\n<b>¥{format_number(car_data['dealer_china_cny'])}</b> | <b>{format_number(int(car_data['dealer_china_rub']))} ₽</b>\n\n"
            f"Доставка, снятие с учёта, оформление:\n<b>¥{format_number(car_data['delivery_china_cny'])}</b> | <b>{format_number(int(car_data['delivery_china_rub']))} ₽</b>\n\n"
            f"<b>Итого расходов по Китаю</b>:\n<b>¥{format_number(car_data['china_total_cny'])}</b> | <b>{format_number(int(car_data['china_total_rub']))} ₽</b>\n\n\n"
            f"<i>РАСХОДЫ РОССИЯ</i>:\n\n"
            f"Единая таможенная ставка:\n<b>{format_number(int(car_data['customs_duty_rub']))} ₽</b>\n\n"
            f"Таможенное оформление:\n<b>{format_number(int(car_data['customs_fee_rub']))} ₽</b>\n\n"
            f"Утилизационный сбор:\n<b>{format_number(int(car_data['util_fee_rub']))} ₽</b>\n\n"
            f"Агентские услуги:\n<b>{format_number(car_data['agent_russia_rub'])} ₽</b>\n\n"
            f"Брокер:\n<b>{format_number(car_data['broker_russia_rub'])} ₽</b>\n\n"
            f"СВХ:\n<b>{format_number(car_data['svh_russia_rub'])} ₽</b>\n\n"
            f"Лаборатория, СБКТС, ЭПТС:\n<b>{format_number(car_data['lab_russia_rub'])} ₽</b>\n\n"
            f"<b>Доставку до вашего города уточняйте у менеджера @GetAuto_manager_bot</b>\n\n"
            "<b>СТОИМОСТЬ ПОД КЛЮЧ АКТУАЛЬНА НА СЕГОДНЯШНИЙ ДЕНЬ, ВОЗМОЖНЫ КОЛЕБАНИЯ КУРСА НА 3-5% ОТ СТОИМОСТИ АВТО, НА МОМЕНТ ПОКУПКИ АВТОМОБИЛЯ</b>\n\n"
        )

        # Inline buttons for further actions
        keyboard = types.InlineKeyboardMarkup()

        if call.data == "detail_china_manual":
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another_manual",
                )
            )
        else:
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )

        keyboard.add(
            types.InlineKeyboardButton(
                "Связаться с менеджером", url="https://t.me/GetAuto_manager_bot"
            )
        )

        bot.send_message(
            call.message.chat.id,
            detail_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif call.data.startswith("detail") or call.data.startswith("detail_manual"):
        print_message("[ЗАПРОС] ДЕТАЛИЗАЦИЯ РАСЧËТА")

        detail_message = (
            f"<i>ПЕРВАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Задаток (бронь авто):\n<b>${format_number(car_data['advance_usd'])}</b> | <b>₩1,000,000</b> | <b>{format_number(car_data['advance_rub'])} ₽</b>\n\n\n"
            f"<i>ВТОРАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Стоимость автомобиля (за вычетом задатка):\n<b>${format_number(car_data['car_price_usd'])}</b> | <b>₩{format_number(car_data['car_price_krw'])}</b> | <b>{format_number(car_data['car_price_rub'])} ₽</b>\n\n"
            f"Диллерский сбор:\n<b>${format_number(car_data['dealer_korea_usd'])}</b> | <b>₩{format_number(car_data['dealer_korea_krw'])}</b> | <b>{format_number(car_data['dealer_korea_rub'])} ₽</b>\n\n"
            f"Доставка, снятие с учёта, оформление:\n<b>${format_number(car_data['delivery_korea_usd'])}</b> | <b>₩{format_number(car_data['delivery_korea_krw'])}</b> | <b>{format_number(car_data['delivery_korea_rub'])} ₽</b>\n\n"
            f"Транспортировка авто в порт:\n<b>${format_number(car_data['transfer_korea_usd'])}</b> | <b>₩{format_number(car_data['transfer_korea_krw'])}</b> | <b>{format_number(car_data['transfer_korea_rub'])} ₽</b>\n\n"
            f"Фрахт (Паром до Владивостока):\n<b>${format_number(car_data['freight_korea_usd'])}</b> | <b>₩{format_number(car_data['freight_korea_krw'])}</b> | <b>{format_number(car_data['freight_korea_rub'])} ₽</b>\n\n"
            f"<b>Итого расходов по Корее</b>:\n<b>${format_number(car_data['korea_total_usd'])}</b> | <b>₩{format_number(car_data['korea_total_krw'])}</b> | <b>{format_number(car_data['korea_total_rub'])} ₽</b>\n\n\n"
            f"<i>РАСХОДЫ РОССИЯ</i>:\n\n\n"
            f"Единая таможенная ставка:\n<b>${format_number(car_data['customs_duty_usd'])}</b> | <b>₩{format_number(car_data['customs_duty_krw'])}</b> | <b>{format_number(car_data['customs_duty_rub'])} ₽</b>\n\n"
            f"Таможенное оформление:\n<b>${format_number(car_data['customs_fee_usd'])}</b> | <b>₩{format_number(car_data['customs_fee_krw'])}</b> | <b>{format_number(car_data['customs_fee_rub'])} ₽</b>\n\n"
            f"Утилизационный сбор:\n<b>${format_number(car_data['util_fee_usd'])}</b> | <b>₩{format_number(car_data['util_fee_krw'])}</b> | <b>{format_number(car_data['util_fee_rub'])} ₽</b>\n\n\n"
            f"Агентские услуги по договору:\n<b>${format_number(car_data['agent_korea_usd'])}</b> | <b>₩{format_number(car_data['agent_korea_krw'])}</b> | <b>50,000 ₽</b>\n\n"
            f"Брокер-Владивосток:\n<b>${format_number(car_data['broker_russia_usd'])}</b> | <b>₩{format_number(car_data['broker_russia_krw'])}</b> | <b>{format_number(car_data['broker_russia_rub'])} ₽</b>\n\n"
            f"СВХ-Владивосток:\n<b>${format_number(car_data['svh_russia_usd'])}</b> | <b>₩{format_number(car_data['svh_russia_krw'])}</b> | <b>{format_number(car_data['svh_russia_rub'])} ₽</b>\n\n"
            f"Лаборатория, СБКТС, ЭПТС:\n<b>${format_number(car_data['lab_russia_usd'])}</b> | <b>₩{format_number(car_data['lab_russia_krw'])}</b> | <b>{format_number(car_data['lab_russia_rub'])} ₽</b>\n\n"
            f"Временная регистрация-Владивосток:\n<b>${format_number(car_data['perm_registration_russia_usd'])}</b> | <b>₩{format_number(car_data['perm_registration_russia_krw'])}</b> | <b>{format_number(car_data['perm_registration_russia_rub'])} ₽</b>\n\n"
            f"<b>Доставку до вашего города уточняйте у менеджера @GetAuto_manager_bot</b>\n\n"
            "<b>СТОИМОСТЬ ПОД КЛЮЧ АКТУАЛЬНА НА СЕГОДНЯШНИЙ ДЕНЬ, ВОЗМОЖНЫ КОЛЕБАНИЯ КУРСА НА 3-5% ОТ СТОИМОСТИ АВТО, НА МОМЕНТ ПОКУПКИ АВТОМОБИЛЯ</b>\n\n"
        )

        # Inline buttons for further actions
        keyboard = types.InlineKeyboardMarkup()

        if call.data.startswith("detail_manual"):
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another_manual",
                )
            )
        else:
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )

        keyboard.add(
            types.InlineKeyboardButton(
                "Связаться с менеджером", url="https://t.me/GetAuto_manager_bot"
            )
        )

        bot.send_message(
            call.message.chat.id,
            detail_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif call.data == "technical_report":
        bot.send_message(
            call.message.chat.id,
            "Запрашиваю отчёт по ДТП. Пожалуйста подождите ⏳",
        )

        # Retrieve insurance information
        insurance_info = get_insurance_total()

        # Проверка на наличие ошибки
        if (
            insurance_info is None
            or "Нет данных" in insurance_info[0]
            or "Нет данных" in insurance_info[1]
        ):
            error_message = (
                "Не удалось получить данные о страховых выплатах. \n\n"
                f'<a href="https://fem.encar.com/cars/report/accident/{car_id_external}">🔗 Посмотреть страховую историю вручную 🔗</a>\n\n\n'
                f"<b>Найдите две строки:</b>\n\n"
                f"보험사고 이력 (내차 피해) - Выплаты по представленному автомобилю\n"
                f"보험사고 이력 (타차 가해) - Выплаты другим участникам ДТП"
            )

            # Inline buttons for further actions
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )
            keyboard.add(
                types.InlineKeyboardButton(
                    "Связаться с менеджером", url="https://t.me/GetAuto_manager_bot"
                )
            )

            # Отправка сообщения об ошибке
            bot.send_message(
                call.message.chat.id,
                error_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            current_car_insurance_payments = (
                "0" if len(insurance_info[0]) == 0 else insurance_info[0]
            )
            other_car_insurance_payments = (
                "0" if len(insurance_info[1]) == 0 else insurance_info[1]
            )

            # Construct the message for the technical report
            tech_report_message = (
                f"Страховые выплаты по представленному автомобилю: \n<b>{current_car_insurance_payments} ₩</b>\n\n"
                f"Страховые выплаты другим участникам ДТП: \n<b>{other_car_insurance_payments} ₩</b>\n\n"
                f'<a href="https://fem.encar.com/cars/report/inspect/{car_id_external}">🔗 Ссылка на схему повреждений кузовных элементов 🔗</a>'
            )

            # Inline buttons for further actions
            keyboard = types.InlineKeyboardMarkup()
            keyboard.add(
                types.InlineKeyboardButton(
                    "Рассчитать стоимость другого автомобиля",
                    callback_data="calculate_another",
                )
            )
            keyboard.add(
                types.InlineKeyboardButton(
                    "Связаться с менеджером", url="https://t.me/GetAuto_manager_bot"
                )
            )

            bot.send_message(
                call.message.chat.id,
                tech_report_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

    elif call.data == "calculate_another":
        bot.send_message(
            call.message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта www.encar.com или che168.com:",
        )

    elif call.data == "calculate_another_manual":
        user_id = call.message.chat.id
        user_manual_input[user_id] = {}  # Очищаем старые данные пользователя
        bot.send_message(user_id, "Введите месяц выпуска (например, 10 для октября):")
        bot.register_next_step_handler(call.message, process_manual_month)

    elif call.data == "main_menu":
        bot.send_message(
            call.message.chat.id, "📌 Главное меню", reply_markup=main_menu()
        )

    elif call.data == "check_subscription":
        user_id = call.message.chat.id
        print(f"Проверка подписки для пользователя {user_id}")

        try:
            if is_subscribed(user_id):
                bot.send_message(
                    user_id,
                    "✅ Вы успешно подписаны! Теперь можете пользоваться ботом.",
                    reply_markup=main_menu(),
                )
                print(f"Пользователь {user_id} успешно подписан")
            else:
                keyboard = types.InlineKeyboardMarkup()
                keyboard.add(
                    types.InlineKeyboardButton(
                        "🔗 Подписаться", url="https://t.me/Getauto_kor"
                    )
                )
                keyboard.add(
                    types.InlineKeyboardButton(
                        "✅ Проверить подписку", callback_data="check_subscription"
                    )
                )
                bot.send_message(
                    user_id,
                    "🚫 Вы еще не подписались на канал! Подпишитесь и попробуйте снова.",
                    reply_markup=keyboard,
                )
                print(f"Пользователь {user_id} не подписан на канал")
        except Exception as e:
            print(f"Ошибка при обработке проверки подписки: {e}")
            bot.send_message(
                user_id,
                "Произошла ошибка при проверке подписки. Пожалуйста, попробуйте позже.",
                reply_markup=main_menu(),
            )


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    global user_manual_input, user_manual_china_input

    user_id = message.chat.id
    user_message = message.text.strip()

    if not is_subscribed(user_id):
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton("🔗 Подписаться", url="https://t.me/Getauto_kor")
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "✅ Проверить подписку", callback_data="check_subscription"
            )
        )
        bot.send_message(
            user_id,
            "🚫 Для использования бота, пожалуйста, подпишитесь на наш канал!",
            reply_markup=keyboard,
        )
        return  # Прерываем выполнение

    # Проверяем нажатие кнопки "Рассчитать автомобиль" (Korea)
    if user_message == CALCULATE_CAR_TEXT:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта www.encar.com или che168.com:",
        )

    elif user_message == MANUAL_CAR_TEXT:
        user_manual_input[user_id] = {}  # Создаём пустой словарь для пользователя
        bot.send_message(user_id, "Введите месяц выпуска (например, 10 для октября):")
        bot.register_next_step_handler(message, process_manual_month)

    # Проверяем нажатие кнопки "Рассчитать автомобиль" (China)
    elif user_message == CALCULATE_CHINA_CAR_TEXT:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта che168.com:",
        )

    elif user_message == MANUAL_CHINA_CAR_TEXT:
        user_manual_china_input[user_id] = {}  # Создаём пустой словарь для пользователя
        bot.send_message(user_id, "Введите месяц первой регистрации (например, 1 для января):")
        bot.register_next_step_handler(message, process_china_manual_month)

    # Проверка на корректность ссылки Encar (Korea)
    elif re.match(r"^https?://(www|fem)\.encar\.com/.*", user_message):
        calculate_cost(user_message, message)

    # Проверка на корректность ссылки Che168 (China)
    elif is_che168_url(user_message):
        calculate_china_cost(user_message, message)

    # Проверка на другие команды
    elif user_message == "Написать менеджеру":
        bot.send_message(
            message.chat.id,
            "Вы можете связаться с менеджером по ссылке: @GetAuto_manager_bot",
        )
    elif user_message == "Написать в WhatsApp":
        whatsapp_link = "https://wa.me/821030485191"  # Владимир Кан

        message_text = f"{whatsapp_link} - Владимир (Корея)"

        bot.send_message(
            message.chat.id,
            message_text,
        )
    elif user_message == "Почему стоит выбрать нас?":
        about_message = (
            "🔹 *Почему выбирают GetAuto?*\n\n"
            "🚗 *Экспертный опыт* — Мы знаем все нюансы подбора и доставки авто из Южной Кореи и Китая.\n\n"
            "🎯 *Индивидуальный подход* — Учитываем все пожелания клиентов, подбираем оптимальный вариант.\n\n"
            "🔧 *Комплексное обслуживание* — Полное сопровождение на всех этапах сделки.\n\n"
            "✅ *Гарантированное качество* — Проверенные авто, прозрачная история и состояние.\n\n"
            "💰 *Прозрачность ценообразования* — Честные цены, без скрытых платежей и комиссий.\n\n"
            "🚛 *Надежная логистика* — Организуем доставку авто в любую точку СНГ.\n\n"
            f"📲 Свяжитесь с нами и получите расчёт прямо сейчас! @GetAuto\\_manager\\_bot"
        )
        bot.send_message(message.chat.id, about_message, parse_mode="Markdown")

    elif user_message == "Мы в соц. сетях":
        channel_link = "https://t.me/Getauto_kor"
        instagram_link = "https://www.instagram.com/getauto_korea"
        youtube_link = "https://youtube.com/@getauto_korea"
        dzen_link = "https://dzen.ru/getauto_ru"
        vk_link = "https://vk.com/getauto_korea"

        message_text = f"Наш Телеграм Канал: \n{channel_link}\n\nНаш Инстаграм: \n{instagram_link}\n\nНаш YouTube Канал: \n{youtube_link}\n\nМы на Dzen: \n{dzen_link}\n\nМы в ВК: \n{vk_link}\n\n"

        bot.send_message(message.chat.id, message_text)

    elif user_message == "Оформить кредит":
        bot.send_message(message.chat.id, "Введите ваше ФИО (Фамилия Имя Отчество):")
        bot.register_next_step_handler(message, process_credit_full_name)

    else:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную ссылку на автомобиль с сайта encar.com (Корея) или che168.com (Китай).",
        )


#######################
# Для обработки заявки на кредит #
#######################
def process_credit_full_name(message):
    user_id = message.chat.id
    full_name = message.text.strip()

    # Проверяем, что ФИО содержит хотя бы 2 слова
    if len(full_name.split()) < 2:
        bot.send_message(user_id, "❌ Введите корректное ФИО (Фамилия Имя Отчество):")
        bot.register_next_step_handler(message, process_credit_full_name)
        return

    # Сохраняем в переменную и переходим к номеру телефона
    bot.send_message(user_id, "Введите ваш номер телефона:")
    bot.register_next_step_handler(message, process_credit_phone, full_name)


def process_credit_phone(message, full_name):
    user_id = message.chat.id
    phone_number = message.text.strip()

    # Проверка номера телефона
    if not re.match(r"^\+?\d{10,15}$", phone_number):
        bot.send_message(user_id, "❌ Введите корректный номер телефона:")
        bot.register_next_step_handler(message, process_credit_phone, full_name)
        return

    # Сохраняем заявку в базу данных
    save_credit_application(user_id, full_name, phone_number)

    bot.send_message(
        user_id, "✅ Ваша заявка на кредит успешно отправлена! Мы с вами свяжемся."
    )


def save_credit_application(user_id, full_name, phone_number):
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO credit_applications (user_id, full_name, phone_number)
        VALUES (%s, %s, %s)
        """,
        (user_id, full_name, phone_number),
    )

    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Заявка на кредит сохранена в базе данных")


#######################
# Для ручного расчёта #
#######################
# Обработчик ввода месяца
def process_manual_month(message):
    user_id = message.chat.id
    user_input = message.text.strip()

    # Проверяем, если пользователь нажал кнопку, а не ввёл число
    if user_input in [
        CALCULATE_CAR_TEXT,
        MANUAL_CAR_TEXT,
        "Написать менеджеру",
        "О нас",
        "Мы в соц. сетях",
        "Написать в WhatsApp",
    ]:
        handle_message(message)  # Передаём управление стандартному обработчику команд
        return  # Завершаем обработку ввода месяца

    # Проверяем корректность ввода месяца
    if not user_input.isdigit() or not (1 <= int(user_input) <= 12):
        bot.send_message(user_id, "❌ Некорректный месяц! Введите число от 1 до 12.")
        bot.register_next_step_handler(message, process_manual_month)
        return

    # Если всё ок, продолжаем ввод данных
    user_manual_input[user_id]["month"] = int(user_input)
    bot.send_message(
        user_id, "✅ Отлично! Теперь введите год выпуска (например, 2021):"
    )
    bot.register_next_step_handler(message, process_manual_year)


# Обработчик ввода года
def process_manual_year(message):
    user_id = message.chat.id
    user_input = message.text.strip()

    if not user_input.isdigit() or not (
        1980 <= int(user_input) <= datetime.datetime.now().year
    ):
        bot.send_message(
            user_id, "Некорректный год! Введите год от 1980 до текущего года:"
        )
        bot.register_next_step_handler(message, process_manual_year)
        return

    user_manual_input[user_id]["year"] = int(user_input)
    bot.send_message(user_id, "Введите объём двигателя в CC (например, 2000):")
    bot.register_next_step_handler(message, process_manual_engine)


# Обработчик ввода объёма двигателя
def process_manual_engine(message):
    user_id = message.chat.id
    user_input = message.text.strip()

    if not user_input.isdigit() or not (500 <= int(user_input) <= 10000):
        bot.send_message(
            user_id, "Некорректный объём! Введите число от 500 до 10000 CC:"
        )
        bot.register_next_step_handler(message, process_manual_engine)
        return

    user_manual_input[user_id]["engine_volume"] = int(user_input)
    # Ask for HP next (required from December 1st for utilization fee calculation)
    bot.send_message(
        user_id, "Введите мощность двигателя в л.с. (например: 150):"
    )
    bot.register_next_step_handler(message, process_manual_horsepower)


# Обработчик ввода мощности двигателя для ручного расчёта
def process_manual_horsepower(message):
    """Handle HP input in manual calculation (HP is NOT cached - no Make/Model info)"""
    user_id = message.chat.id
    user_input = message.text.strip()

    if not user_input.isdigit() or not (50 <= int(user_input) <= 1000):
        bot.send_message(
            user_id,
            "Пожалуйста, введите корректное значение мощности (от 50 до 1000 л.с.):"
        )
        bot.register_next_step_handler(message, process_manual_horsepower)
        return

    # Note: HP is NOT cached for manual calculations (no Make/Model info available)
    user_manual_input[user_id]["horsepower"] = int(user_input)
    bot.send_message(
        user_id, "Введите стоимость автомобиля в Корее (например, 30000000):"
    )
    bot.register_next_step_handler(message, process_manual_price)


# Обработчик ввода стоимости автомобиля
def process_manual_price(message):
    user_id = message.chat.id
    user_input = message.text.strip()

    if not user_input.isdigit() or not (1000000 <= int(user_input) <= 1000000000000):
        bot.send_message(
            user_id,
            "Некорректная стоимость! Введите сумму от 1 000 000 до 200 000 000 KRW:",
        )
        bot.register_next_step_handler(message, process_manual_price)
        return

    user_manual_input[user_id]["price_krw"] = int(user_input)

    # Показываем выбор типа двигателя
    bot.send_message(
        user_id,
        "Выберите тип двигателя:",
        reply_markup=create_fuel_type_keyboard()
    )


# Функция расчёта стоимости авто
def calculate_manual_cost(user_id):
    global rub_to_krw_rate, usd_rate, krw_rub_rate

    data = user_manual_input[user_id]

    price_krw = data["price_krw"]
    engine_volume = data["engine_volume"]
    month = data["month"]
    year = data["year"]
    hp = data.get("horsepower", 1)  # Get HP from user input, default to 1 for backward compatibility
    fuel_type = data.get("fuel_type", FUEL_TYPE_GASOLINE)  # Get fuel type from user selection

    car_engine_displacement = int(engine_volume)

    # Форматирование данных
    engine_volume_formatted = f"{format_number(car_engine_displacement)} cc"
    age = calculate_age(year, month)
    age_formatted = (
        "до 3 лет"
        if age == "0-3"
        else (
            "от 3 до 5 лет"
            if age == "3-5"
            else "от 5 до 7 лет" if age == "5-7" else "от 7 лет"
        )
    )

    # Конвертируем стоимость авто в рубли
    price_krw = int(price_krw)

    response = get_customs_fees(
        car_engine_displacement,
        price_krw,
        year,
        month,
        power=hp,  # Pass user-provided HP for accurate utilization fee calculation
        engine_type=fuel_type,  # Pass user-selected fuel type
    )

    customs_fee = clean_number(response["sbor"])
    customs_duty = clean_number(response["tax"])
    recycling_fee = clean_number(response["util"])

    # Расчет итоговой стоимости автомобиля в рублях
    total_cost = (
        50000
        + (price_krw * krw_rub_rate)
        + (440000 * krw_rub_rate)
        + (100000 * krw_rub_rate)
        + (350000 * krw_rub_rate)
        + (600 * usd_rate)
        + customs_duty
        + customs_fee
        + recycling_fee
        + (465 * usd_rate)
        + 50000
        + 30000
        + 8000
    )

    total_cost_krw = total_cost / krw_rub_rate

    car_data["agent_korea_rub"] = 50000
    car_data["agent_korea_usd"] = 50000 / usd_rate
    car_data["agent_korea_krw"] = 50000 / krw_rub_rate

    car_data["advance_rub"] = 1000000 * krw_rub_rate
    car_data["advance_usd"] = (1000000 * krw_rub_rate) / usd_rate
    car_data["advance_krw"] = 1000000

    # Задаток 1 млн. вон
    car_data["car_price_krw"] = price_krw - 1000000
    car_data["car_price_usd"] = (price_krw - 1000000) * krw_rub_rate / usd_rate
    car_data["car_price_rub"] = (price_krw - 1000000) * krw_rub_rate

    car_data["dealer_korea_usd"] = 440000 * krw_rub_rate / usd_rate
    car_data["dealer_korea_krw"] = 440000
    car_data["dealer_korea_rub"] = 440000 * krw_rub_rate

    car_data["delivery_korea_usd"] = 100000 * krw_rub_rate / usd_rate
    car_data["delivery_korea_krw"] = 100000
    car_data["delivery_korea_rub"] = 100000 * krw_rub_rate

    car_data["transfer_korea_usd"] = 350000 * krw_rub_rate / usd_rate
    car_data["transfer_korea_krw"] = 350000
    car_data["transfer_korea_rub"] = 350000 * krw_rub_rate

    car_data["freight_korea_usd"] = 600
    car_data["freight_korea_krw"] = 600 * usd_rate / krw_rub_rate
    car_data["freight_korea_rub"] = 600 * usd_rate

    car_data["korea_total_usd"] = (
        (50000 / usd_rate)
        + ((price_krw) * krw_rub_rate / usd_rate)
        + (440000 * krw_rub_rate / usd_rate)
        + (100000 * krw_rub_rate / usd_rate)
        + (350000 * krw_rub_rate / usd_rate)
        + (600)
    )

    car_data["korea_total_krw"] = (
        (50000 / krw_rub_rate)
        + (price_krw)
        + (440000)
        + (100000)
        + 350000
        + (600 * usd_rate / krw_rub_rate)
    )

    car_data["korea_total_rub"] = (
        (50000)
        + (price_krw * krw_rub_rate)
        + (440000 * krw_rub_rate)
        + (100000 * krw_rub_rate)
        + (350000 * krw_rub_rate)
        + (600 * usd_rate)
    )

    # Расходы Россия
    car_data["customs_duty_usd"] = customs_duty / usd_rate
    car_data["customs_duty_krw"] = customs_duty * rub_to_krw_rate
    car_data["customs_duty_rub"] = customs_duty

    car_data["customs_fee_usd"] = customs_fee / usd_rate
    car_data["customs_fee_krw"] = customs_fee / krw_rub_rate
    car_data["customs_fee_rub"] = customs_fee

    car_data["util_fee_usd"] = recycling_fee / usd_rate
    car_data["util_fee_krw"] = recycling_fee / krw_rub_rate
    car_data["util_fee_rub"] = recycling_fee

    car_data["broker_russia_usd"] = (
        ((customs_duty + customs_fee + recycling_fee) / 100) * 1.5 + 30000
    ) / usd_rate
    car_data["broker_russia_krw"] = (
        ((customs_duty + customs_fee + recycling_fee) / 100) * 1.5 + 30000
    ) * rub_to_krw_rate
    car_data["broker_russia_rub"] = (
        (customs_duty + customs_fee + recycling_fee) / 100
    ) * 1.5 + 30000

    car_data["svh_russia_usd"] = 50000 / usd_rate
    car_data["svh_russia_krw"] = 50000 / krw_rub_rate
    car_data["svh_russia_rub"] = 50000

    car_data["lab_russia_usd"] = 30000 / usd_rate
    car_data["lab_russia_krw"] = 30000 / krw_rub_rate
    car_data["lab_russia_rub"] = 30000

    car_data["perm_registration_russia_usd"] = 8000 / usd_rate
    car_data["perm_registration_russia_krw"] = 8000 / krw_rub_rate
    car_data["perm_registration_russia_rub"] = 8000

    # Get fuel type name for display
    fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type, "Бензин")

    # Формирование сообщения
    result_message = (
        f"Возраст: {age_formatted}\n"
        f"Стоимость автомобиля в Корее: ₩{format_number(price_krw)}\n"
        f"Объём двигателя: {engine_volume_formatted}\n"
        f"Тип двигателя: {fuel_type_name}\n\n"
        f"Примерная стоимость автомобиля под ключ до Владивостока:\n"
        # f"<b>${format_number(total_cost_usd)}</b> | "
        f"<b>₩{format_number(total_cost_krw)}</b> | "
        f"<b>{format_number(total_cost)} ₽</b>\n\n"
        "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у менеджера @GetAuto_manager_bot\n\n"
        "🔗 <a href='https://t.me/Getauto_kor'>Официальный телеграм канал</a>\n"
    )

    # Клавиатура с действиями
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail_manual")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Рассчитать другой автомобиль", callback_data="calculate_another_manual"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/GetAuto_manager_bot"
        )
    )

    # Отправка сообщения пользователю
    bot.send_message(user_id, result_message, parse_mode="HTML", reply_markup=keyboard)


# Run the bot
if __name__ == "__main__":
    rub_to_krw_rate = get_rub_to_krw_rate()
    get_currency_rates()
    set_bot_commands()
    bot.polling(non_stop=True)
