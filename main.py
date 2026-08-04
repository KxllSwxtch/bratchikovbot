import json
import telebot
import os
import re
import requests
import locale
import logging
import urllib.parse
import time


from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from io import BytesIO
from telebot import types
from dotenv import load_dotenv
from urllib.parse import urlparse, parse_qs

from database import (
    create_tables,
    get_orders,
    get_all_orders,
    add_order,
    update_user_phone,
    update_order_status_in_db,
    delete_order_from_db,
    update_user_name,
    update_user_name,
    update_user_subscription,
    delete_favorite_car,
    add_user_if_not_exists,
    get_all_users,
    get_stored_hp,
    save_hp_spec,
)
from utils import (
    generate_encar_photo_url,
    clean_number,
    get_customs_fees,
    calculate_age,
    format_number,
    get_customs_fees_manual,
    get_pan_auto_data,
)
from get_vtb_cnyrub_rate import get_vtb_cnyrub_rate
from che168_scraper import (
    get_che168_car_info_with_fallback,
    extract_car_id_from_che168_url,
    is_che168_url,
    format_mileage as format_che168_mileage,
    format_gearbox as format_che168_gearbox,
)


CALCULATE_CAR_TEXT = "Рассчитать Автомобиль (Encar, KBChaCha, KCar, Che168)"
CHANNEL_USERNAME = "bratchikov_cars"

# China (Che168) expense constants
CHINA_FIRST_PAYMENT = 6600     # ¥6,600 задаток + отчет эксперта
CHINA_EXPENSES = 10000         # ¥10,000 расходы по Китаю (дилерский сбор, доставка, оформление)
CHINA_BROKER_FEE = 60000       # ₽60,000 брокер
CHINA_AGENT_FEE = 50000        # ₽50,000 агентские услуги
CHINA_SVH_FEE = 50000          # ₽50,000 СВХ
CHINA_LAB_FEE = 30000          # ₽30,000 лаборатория
CHINA_YURI_FEE = 120000        # ₽120,000 комиссия

# Fuel type names for display
FUEL_TYPE_NAMES = {
    1: "Бензин",
    2: "Дизель",
    4: "Электро",
    5: "Гибрид (посл.)",
    6: "Гибрид (парал.)",
}
BOT_TOKEN = os.getenv("BOT_TOKEN")

load_dotenv()
bot_token = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(bot_token)


# Set locale for number formatting
locale.setlocale(locale.LC_ALL, "en_US.UTF-8")

# Storage for the last error message ID
last_error_message_id = {}

# global variables
car_data = {}
car_id_external = ""
total_car_price = 0
krw_rub_rate = 0
rub_to_krw_rate = 0
usd_rate = 0
users = set()
user_data = {}
user_type_map = {}  # user_id: 1 (физ) или 2 (юр)

car_month = None
car_year = None

vehicle_id = None
vehicle_no = None

usd_to_krw_rate = 0
usd_to_rub_rate = 0

usdt_to_krw_rate = 0


################## КОД ДЛЯ СТАТУСОВ
# Храним заказы пользователей
pending_orders = {}
user_contacts = {}
user_names = {}

# Хранение контекста для ввода HP пользователем
# {user_id: {"car_data": {...}, "message": msg, "user_type": int, ...}}
pending_hp_input = {}

# China-specific globals
cny_rub_rate = None
pending_china_hp_requests = {}

MANAGERS = [728438182, 224917357]

faq_data = {
    "Депозит": [
        {
            "question": "Для чего нужно вносить депозит (задаток)?",
            "answer": """Депозит служит гарантией ваших намерений купить автомобиль в Южной Корее.
В случае не выкупа автомобиля у диллера, компания Bratchikov Cars» вынуждена будет выплатить неустойку в размере задатка за автомобиль.
В таком случае Ваш депозит сможет полностью или частично покрыть сумму задатка.
Сумма депозита для запуска подбора и покупки авто составляет 100 000 рублей.
Если Вы передумали покупать авто или пользоваться услугами компании Bratchikov Cars», до внесения задатка диллеру за потенциальный автомобиль, сумма депозита возвращается за вычетом выезда ОСМОТРЩИКА (150.000 вон или 10.000₽).
""",
        },
        {
            "question": "100.000₽ — за услуги или в счёт авто?",
            "answer": "💬 100.000 рублей — это *не* стоимость услуги. Эта сумма будет вычтена на Российской стороне при окончательном расчёте.",
        },
    ],
    "Процесс покупки": [
        {
            "question": "Как происходит процесс покупки?",
            "answer": """
После того, как вы внесли задаток и заключили с нами договор. Мы отправляем вам в мессенджер несколько вариантов авто, соотвествующие вашему тех заданию. 

Вам нужно выбрать понравившийся вам авто и после этого мы отправим нашего сотрудника на осмотр 

Во время осмотра наш сотрудник производит видео и фотоотчет об автомобиле. Наш менеджер отправит вам все в чат

После просмотра видео и фотоотчета Вам необходимо дать ответ: бронируем мы авто или нет
            """,
        },
        {
            "question": "Сколько осмотров входит в стоимость услуг?",
            "answer": "В стоимость услуг входит 3 осмотра, каждый последующий осмотр (150.000вон или 10.000₽)",
        },
    ],
    "Документы": [
        {
            "question": "Вы заключаете договор?",
            "answer": "Да, мы заключаем договор ПОСТАВКИ ТРАНСПОРТНОГО СРЕДСТВА, но большинство наших клиентов нам доверяют и не нуждаются в этом",
        },
        {
            "question": "Какие документы будут при получении авто?",
            "answer": """
ЭПТС (электронный паспорт транспортного средства)
СБКТС (свидетельство о безопасности конструкции транспортного средства)
ТПО (таможенный приходный ордер)
ПТД (пассажирская таможенная декларация)
            """,
        },
    ],
    "Логистика": [
        {
            "question": "А что если мой авто повредится ?",
            "answer": """
За все время работы в данной сфере, абсолютно все автомобили доехали до своих собственников без повреждений.
Так как мы работаем только с проверенными транспортными компаниями, чтобы свести подобные риски к минимуму.
""",
        },
        {
            "question": "Сможете доставить авто в мой город?",
            "answer": "На данный момент наша компания НЕ занимается перевозками по территории РФ. Но за все время работы у нас появились партнеры-перевозчики, которых мы можем посоветовать",
        },
        {
            "question": "Перегоните авто с таможни на автовоз?",
            "answer": "Да во Владивостоке у нас есть сотрудник, который перегонит ваш авто. Стоимость услуг уже включена в общую цену",
        },
        {
            "question": "Страховка автомобиля",
            "answer": "Напоминаем также, что автомобиль застрахован на все время перевозки от стоянки диллера до получения Вами во Владивостоке на каждом этапе",
        },
    ],
    "Сроки": [
        {
            "question": "От внесения депозита до покупки авто ?",
            "answer": """
Сроки на данном этапе зависят от многих факторов. 
   1. Чем быстрее вы определитесь с автомобилем и выберите нужный вариант, тем быстрее мы осмотрим его и выкупим

   2. Наличие авто на площадках, которые бы соответствовали вашим требованиям

   3. Ожидание и реальность не соответствуют. Мы часто сталкиваемся с тем, что в объявлении указана не достоверная информация об авто и также Продавец не всегда знает настоящее состояние автомобиля, что также влияет на общие сроки
""",
        },
        {
            "question": "От покупки до растаможки во Владивостоке?",
            "answer": """
С момента покупки авто в Корее, до момента доставки в порт Владивосток срок составит до 2-х недель + таможенное оформление до 10 дней. 
(Возможно увеличение сроков из за погодных условий и других случаев, не зависящих от нас)
""",
        },
        {
            "question": "От растаможки до прохождения лаборатории?",
            "answer": """
Наш сотрудник забирает машину с СВХ (склад временного хранения) после того, как брокер сообщает, что машина выпустилась. 
И в течении 2х дней проходит лабораторию. Если выпуск совпал с выходным днем, тогда процедура переносится на понедельник
""",
        },
        {
            "question": "Когда я получу ЭПТС?",
            "answer": "В лабораториях бывают очереди, поэтому срок получения ЭПТС составляет до 7 дней",
        },
        {
            "question": "Какое время в пути по морю?",
            "answer": "Если авто выходит с порта ДОНГХЕ, то время в пути 1 сутки\nЕсли авто выходит с порта ПУСАН, то время в пути 2 суток",
        },
        {"question": "Срок таможенной очистки?", "answer": "От 7 до 14 дней"},
    ],
    "Прочее": [
        {
            "question": "Видео/Фотоотпись авто",
            "answer": """
1. Фото и видеосъемка осуществляется во время осмотра авто. 

2. После выкупа автомобиля у диллера, ваш автомобиль пригоняют к нам на стоянку. На стоянке наши сотрудники делают фотоопись

3. При погрузке на автовоз

4. В порту отплытия

6. При выпуске авто с СВХ во Владивостоке
""",
        }
    ],
}


@bot.callback_query_handler(func=lambda call: call.data.startswith("add_favorite_"))
def add_favorite_car(call):
    global car_data
    user_id = call.message.chat.id

    if not car_data or "name" not in car_data:
        bot.answer_callback_query(
            call.id, "🚫 Ошибка: Данные о машине отсутствуют.", show_alert=True
        )
        return

    # Проверяем, есть ли авто уже в избранном
    existing_orders = get_orders(user_id)
    if any(order["id"] == car_data.get("car_id") for order in existing_orders):
        bot.answer_callback_query(call.id, "✅ Этот автомобиль уже в избранном.")
        return

    # Получаем данные пользователя
    user = bot.get_chat(user_id)
    user_name = user.username if user.username else "Неизвестно"

    # Проверяем, есть ли сохранённый номер телефона пользователя
    phone_number = user_contacts.get(user_id, "Неизвестно")

    # Формируем объект заказа
    order_data = {
        "user_id": user_id,
        "car_id": car_data.get("car_id", "Нет ID"),
        "title": car_data.get("name", "Неизвестно"),
        "price": f"₩{format_number(car_data.get('car_price', 0))}",
        "link": car_data.get("link", "Нет ссылки"),
        "year": car_data.get("year", "Неизвестно"),
        "month": car_data.get("month", "Неизвестно"),
        "mileage": car_data.get("mileage", "Неизвестно"),
        "fuel": car_data.get("fuel", "Неизвестно"),
        "engine_volume": car_data.get("engine_volume", "Неизвестно"),
        "transmission": car_data.get("transmission", "Неизвестно"),
        "images": car_data.get("images", []),
        "status": "🔄 Не заказано",
        "total_cost_usd": car_data.get("total_cost_usd", 0),
        "total_cost_krw": car_data.get("total_cost_krw", 0),
        "total_cost_rub": car_data.get("total_cost_rub", 0),
        "user_name": user_name,  # ✅ Добавляем user_name
        "phone_number": phone_number,  # ✅ Добавляем phone_number (если нет, "Неизвестно")
    }

    # Логируем, чтобы проверить, какие данные отправляем в БД
    print(f"✅ Добавляем заказ: {order_data}")

    # Сохраняем в базу
    add_order(order_data)

    # Подтверждаем пользователю
    bot.answer_callback_query(
        call.id, "⭐ Автомобиль добавлен в избранное!", show_alert=True
    )


@bot.message_handler(commands=["my_cars"])
def show_favorite_cars(message):
    user_id = message.chat.id
    orders = get_orders(user_id)  # Берём заказы из БД

    if not orders:
        bot.send_message(user_id, "❌ У вас нет сохранённых автомобилей.")
        return

    for car in orders:
        car_id = car["car_id"]  # Используем car_id вместо id
        car_title = car["title"]
        car_status = car["status"]
        car_link = car["link"]
        car_year = car["year"]
        car_month = car["month"]
        car_mileage = car["mileage"]
        car_engine_volume = car["engine_volume"]
        car_transmission = car["transmission"]
        total_cost_usd = car["total_cost_usd"]
        total_cost_krw = car["total_cost_krw"]
        total_cost_rub = car["total_cost_rub"]

        # Формируем текст сообщения
        response_text = (
            f"🚗 *{car_title} ({car_id})*\n\n"
            f"📅 {car_month}/{car_year} | ⚙️ {car_transmission}\n"
            f"🔢 Пробег: {car_mileage} | 🏎 Объём: {format_number(car_engine_volume)} cc\n\n"
            f"Стоимость авто под ключ:\n"
            f"${format_number(total_cost_usd)} | ₩{format_number(total_cost_krw)} | {format_number(total_cost_rub)} ₽\n\n"
            # f"📌 *Статус:* {car_status}\n\n"
            f"[🔗 Ссылка на автомобиль]({car_link})\n\n"
            f"Для консультации:\n\n"
            f"▪️ @bratchikov_cars (Юрий)\n"
        )

        # Создаём клавиатуру
        keyboard = types.InlineKeyboardMarkup()
        # if car_status == "🔄 Не заказано":
        #     keyboard.add(
        #         types.InlineKeyboardButton(
        #             f"📦 Заказать {car_title}",
        #             callback_data=f"order_car_{car_id}",
        #         )
        #     )
        keyboard.add(
            types.InlineKeyboardButton(
                "❌ Удалить авто из списка", callback_data=f"delete_car_{car_id}"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Вернуться в главное меню", callback_data="main_menu"
            )
        )

        bot.send_message(
            user_id, response_text, parse_mode="Markdown", reply_markup=keyboard
        )


@bot.callback_query_handler(func=lambda call: call.data == "show_orders")
def callback_show_orders(call):
    """Обработчик кнопки 'Посмотреть список заказов'"""
    manager_id = call.message.chat.id
    print(f"📋 Менеджер {manager_id} нажал 'Посмотреть список заказов'")

    # ✅ Вызываем show_orders() с переданным сообщением из callback-запроса
    show_orders(call.message)


def notify_managers(order):
    """Отправляем информацию о заказе всем менеджерам"""
    print(f"📦 Отправляем заказ менеджерам: {order}")

    # Создаём клавиатуру с кнопкой "Посмотреть список заказов"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "📋 Посмотреть список заказов", callback_data="show_orders"
        )
    )

    order_title = order.get("title", "Без названия")
    order_link = order.get("link", "#")
    user_name = order.get("user_name", "Неизвестный")
    user_id = order.get("user_id", None)
    phone_number = order.get("phone_number", "Не указан")

    user_mention = f"[{user_name}](tg://user?id={user_id})" if user_id else user_name

    message_text = (
        f"🚨 *Новый заказ!*\n\n"
        f"🚗 [{order_title}]({order_link})\n"
        f"👤 Заказчик: {user_mention}\n"
        f"📞 Контакт: {phone_number}\n"
        f"📌 *Статус:* 🕒 Ожидает подтверждения\n"
    )

    for manager_id in MANAGERS:
        bot.send_message(
            manager_id,
            message_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("order_car_"))
def order_car(call):
    user_id = call.message.chat.id
    car_id = call.data.split("_")[-1]

    # Получаем авто из базы
    user_orders = get_orders(user_id)
    order_found = None

    for order in user_orders:
        if str(order["car_id"]) == str(car_id):
            order_found = order
            break
        else:
            print(f"❌ Автомобиль {car_id} не совпадает с {order['car_id']}")

    if not order_found:
        print(f"❌ Ошибка: авто {car_id} не найдено в базе!")
        bot.send_message(user_id, "❌ Ошибка: автомобиль не найден.")
        return

    # ✅ Проверяем, есть ли ФИО у пользователя
    if user_id not in user_names:
        print(f"📝 Запрашиваем ФИО у {user_id}")
        bot.send_message(
            user_id,
            "📝 Введите ваше *ФИО* для оформления заказа:",
            parse_mode="Markdown",
        )

        # Сохраняем ID заказа в `pending_orders`
        pending_orders[user_id] = car_id
        return

    # ✅ Если ФИО уже есть, проверяем телефон
    if user_id not in user_contacts:
        print(f"📞 Запрашиваем телефон у {user_id}")
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button = types.KeyboardButton("📞 Отправить номер", request_contact=True)
        markup.add(button)

        bot.send_message(
            user_id,
            "📲 Для оформления заказа, пожалуйста, отправьте номер телефона, "
            "на который зарегистрирован WhatsApp или Telegram.",
            reply_markup=markup,
        )

        # Сохраняем ID заказа в `pending_orders`
        pending_orders[user_id] = car_id
        return

    # ✅ Если ФИО и телефон уже есть → обновляем заказ
    phone_number = user_contacts[user_id]
    full_name = user_names[user_id]

    update_order_status(car_id, "🕒 Ожидает подтверждения")
    update_order_status_in_db(order_found["id"], "🕒 Ожидает подтверждения")

    bot.send_message(
        user_id,
        f"✅ Ваш заказ на {order_found['title']} оформлен!\n"
        f"📌 Статус: 🕒 Ожидает подтверждения\n"
        f"📞 Контакт для связи: {phone_number}\n"
        f"👤 ФИО: {full_name}",
        callback_data="show_orders",
    )

    # ✅ Добавляем ФИО в заказ перед отправкой менеджерам
    order_found["user_name"] = full_name
    notify_managers(order_found)


# Обработчик получения номера телефона
@bot.message_handler(content_types=["contact"])
def handle_contact(message):
    if not message.contact or not message.contact.phone_number:
        bot.send_message(user_id, "❌ Ошибка: номер телефона не передан.")
        return

    user_id = message.chat.id
    phone_number = message.contact.phone_number

    # Сохраняем номер телефона
    user_contacts[user_id] = phone_number
    bot.send_message(user_id, f"✅ Ваш номер {phone_number} сохранён!")

    # Проверяем, есть ли ожидаемый заказ
    if user_id not in pending_orders:
        bot.send_message(user_id, "✅ Ваш номер сохранён, но активного заказа нет.")
        return

    if user_id in pending_orders:
        car_id = pending_orders[user_id]  # Берём car_id из `pending_orders`
        print(f"📦 Пользователь {user_id} подтвердил заказ автомобиля {car_id}")

        # Получаем заказанное авто из базы
        user_orders = get_orders(user_id)
        order_found = None

        for order in user_orders:
            if str(order["car_id"]).strip() == str(car_id).strip():
                order_found = order
                break

        if not order_found:
            bot.send_message(user_id, "❌ Ошибка: автомобиль не найден в базе данных.")
            return

        # Добавляем `user_id` в order_found, если его нет
        order_found["user_id"] = user_id
        order_found["phone_number"] = (
            phone_number  # ✅ Сохраняем номер телефона в заказе
        )

        print(
            f"🛠 Обновляем телефон {phone_number} для user_id={user_id}, order_id={order_found['id']}"
        )
        update_user_phone(user_id, phone_number, order_found["id"])
        update_order_status_in_db(order_found["id"], "🕒 Ожидает подтверждения")

        bot.send_message(
            user_id,
            f"✅ Ваш заказ на {order_found['title']} оформлен!\n"
            f"📌 Статус: 🕒 Ожидает подтверждения\n"
            f"📞 Контакт: {phone_number}",
        )

        notify_managers(order_found)


@bot.message_handler(
    func=lambda message: not message.text.startswith("/")
    and message.chat.id in pending_orders
)
def handle_full_name(message):
    user_id = message.chat.id
    full_name = message.text.strip()

    # ❌ Если ФИО пустое, просим ввести заново
    if not full_name:
        bot.send_message(
            user_id, "❌ ФИО не может быть пустым. Введите ваше ФИО ещё раз:"
        )
        return

    # ✅ Сохраняем ФИО
    user_names[user_id] = full_name
    bot.send_message(user_id, f"✅ Ваше ФИО '{full_name}' сохранено!")

    # Проверяем, есть ли ожидаемый заказ
    car_id = pending_orders[user_id]  # Берём car_id из `pending_orders`
    print(
        f"📦 Пользователь {user_id} подтвердил заказ автомобиля {car_id} с ФИО {full_name}"
    )

    # Получаем заказанное авто из базы
    user_orders = get_orders(user_id)
    order_found = next(
        (
            order
            for order in user_orders
            if str(order["car_id"]).strip() == str(car_id).strip()
        ),
        None,
    )

    if not order_found:
        bot.send_message(user_id, "❌ Ошибка: автомобиль не найден в базе данных.")
        return

    # ✅ Обновляем статус заказа и добавляем ФИО в БД
    import hashlib

    def convert_car_id(car_id):
        if car_id.isdigit():
            return int(car_id)  # Если уже число, просто вернуть его
        else:
            return int(hashlib.md5(car_id.encode()).hexdigest(), 16) % (
                10**9
            )  # Преобразуем в число

    # Пример использования
    numeric_car_id = convert_car_id(car_id)

    update_order_status_in_db(order_found["id"], "🕒 Ожидает подтверждения")
    update_user_name(user_id, full_name)

    # ✅ Проверяем, есть ли уже телефон пользователя
    if user_id not in user_contacts:
        print(f"📞 Запрашиваем телефон у {user_id}")
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        button = types.KeyboardButton("📞 Отправить номер", request_contact=True)
        markup.add(button)

        bot.send_message(
            user_id,
            "📲 Теперь отправьте ваш *номер телефона*, на который зарегистрирован WhatsApp или Telegram.",
            reply_markup=markup,
            parse_mode="Markdown",
        )
        return  # Ждём телефон, дальше не идём

    # ✅ Если телефон уже есть → завершаем оформление
    phone_number = user_contacts[user_id]

    bot.send_message(
        user_id,
        f"✅ Ваш заказ на {order_found['title']} оформлен!\n"
        f"📌 Статус: 🕒 Ожидает подтверждения\n"
        f"📞 Контакт: {phone_number}\n"
        f"👤 ФИО: {full_name}",
    )

    # ✅ Отправляем информацию менеджерам
    order_found["user_name"] = full_name
    print(f"📦 Перед отправкой менеджерам заказ: {order_found}")  # Отладка
    notify_managers(order_found)

    # ✅ Удаляем `pending_orders`
    del pending_orders[user_id]


# Функция оформления заказа
def process_order(user_id, car_id, username, phone_number):
    # Достаём авто из списка
    car = next(
        (car for car in user_orders.get(user_id, []) if car["id"] == car_id), None
    )

    if not car:
        bot.send_message(user_id, "❌ Ошибка: автомобиль не найден.")
        return

    car_title = car.get("title", "Неизвестно")
    car_link = car.get("link", "Нет ссылки")

    # Менеджер, которому отправлять заявку
    manager_chat_id = MANAGERS[0]  # Здесь нужно указать ID менеджера

    # Сообщение менеджеру
    manager_text = (
        f"📢 *Новый заказ на автомобиль!*\n\n"
        f"🚗 {car_title}\n"
        f"🔗 [Ссылка на автомобиль]({car_link})\n\n"
        f"🔹 Username: @{username if username else 'Не указан'}\n"
        f"📞 Телефон: {phone_number if phone_number else 'Не указан'}\n"
    )

    bot.send_message(manager_chat_id, manager_text, parse_mode="Markdown")

    # Обновляем статус авто
    car["status"] = "🕒 Ожидает подтверждения"
    bot.send_message(
        user_id,
        f"✅ Ваш заказ на {car_title} оформлен! Менеджер скоро свяжется с вами.",
    )


@bot.message_handler(commands=["orders"])
def show_orders(message):
    manager_id = message.chat.id

    # Проверяем, является ли пользователь менеджером
    if manager_id not in MANAGERS:
        bot.send_message(manager_id, "❌ У вас нет доступа к заказам.")
        return

    # Загружаем все заказы из базы данных
    orders = get_all_orders()

    if not orders:
        bot.send_message(manager_id, "📭 Нет активных заказов.")
        return

    for idx, order in enumerate(orders, start=1):
        order_id = order.get("id", "Неизвестно")
        car_title = order.get("title", "Без названия")
        user_id = order.get("user_id")
        user_name = order.get("user_name", "Неизвестный")
        phone_number = order.get("phone_number", "Неизвестно")
        car_status = order.get("status", "🕒 Ожидает подтверждения")
        car_link = order.get("link", "#")
        car_id = order.get("car_id", "Неизвестно")

        if car_status == "🔄 Не заказано":
            car_status = "🕒 Ожидает подтверждения"

        user_mention = (
            f"[{user_name}](tg://user?id={user_id})" if user_id else user_name
        )

        response_text = (
            # f"📦 *Заказ #{idx}*\n"
            f"🚗 *{car_title}* (ID: {car_id})\n\n"
            f"👤 Заказчик: {user_mention}\n"
            f"📞 Телефон: *{phone_number}*\n\n"
            f"📌 *Статус:* {car_status}\n\n"
            f"[🔗 Ссылка на автомобиль]({car_link})"
        )

        # Создаем клавиатуру
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                f"📌 Обновить статус ({car_title})",
                callback_data=f"update_status_{order_id}",
            ),
            types.InlineKeyboardButton(
                f"🗑 Удалить заказ ({car_title})",
                callback_data=f"delete_order_{order_id}",
            ),
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Вернуться в главное меню ", callback_data="main_menu"
            )
        )

        bot.send_message(
            manager_id,
            response_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("update_status_"))
def update_order_status(call):
    manager_id = call.message.chat.id
    order_id = call.data.split("_")[-1]  # ❗ Здесь приходит ID заказа, а не car_id

    print(f"🔍 Менеджер {manager_id} пытается обновить статус заказа {order_id}")

    # Получаем заказы из базы
    orders = get_all_orders()  # ✅ Загружаем все заказы
    # print(f"📦 Все заказы из базы: {orders}")  # Логируем заказы

    # 🛠 Теперь ищем по `id`, а не по `car_id`
    order_found = next(
        (order for order in orders if str(order["id"]) == str(order_id)), None
    )

    if not order_found:
        print(f"❌ Ошибка: заказ {order_id} не найден!")
        bot.answer_callback_query(call.id, "❌ Ошибка: заказ не найден.")
        return

    user_id = order_found["user_id"]
    car_id = order_found["car_id"]  # ✅ Берём car_id

    # 🔥 Генерируем кнопки статусов
    keyboard = types.InlineKeyboardMarkup()
    for status_code, status_text in ORDER_STATUSES.items():
        keyboard.add(
            types.InlineKeyboardButton(
                status_text,
                callback_data=f"set_status_{user_id}_{order_id}_{status_code}",
            )
        )

    bot.send_message(manager_id, "📌 Выберите новый статус:", reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_car_"))
def delete_favorite_callback(call):
    user_id = call.message.chat.id
    car_id = call.data.split("_")[2]  # Получаем ID авто

    delete_favorite_car(user_id, car_id)  # Удаляем авто из БД

    bot.answer_callback_query(call.id, "✅ Авто удалено из списка!")
    bot.delete_message(
        call.message.chat.id, call.message.message_id
    )  # Удаляем сообщение с авто


@bot.callback_query_handler(func=lambda call: call.data.startswith("delete_order_"))
def delete_order(call):
    manager_id = call.message.chat.id
    order_id = call.data.split("_")[-1]

    print(f"🗑 Менеджер {manager_id} хочет удалить заказ {order_id}")

    # Удаляем заказ из базы
    delete_order_from_db(order_id)

    bot.answer_callback_query(call.id, "✅ Заказ удалён!")
    bot.send_message(manager_id, f"🗑 Заказ {order_id} успешно удалён.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("set_status_"))
def set_new_status(call):
    manager_id = call.message.chat.id

    print(f"🔄 Получен `callback_data`: {call.data}")  # Логирование данных

    # Разбиваем callback_data
    _, _, user_id, order_id, status_code = call.data.split("_", 4)

    if not user_id.isdigit():
        print(f"❌ Ошибка: user_id некорректный: {user_id}")
        bot.answer_callback_query(call.id, "❌ Ошибка: неверный ID пользователя.")
        return

    user_id = int(user_id)

    # Проверяем статус
    if status_code not in ORDER_STATUSES:
        print(f"❌ Ошибка: неверный код статуса: {status_code}")
        bot.answer_callback_query(call.id, "❌ Ошибка: неверный статус.")
        return

    new_status = ORDER_STATUSES[status_code]  # Получаем текст статуса по коду

    print(
        f"🔄 Менеджер {manager_id} меняет статус заказа {order_id} для {user_id} на {new_status}"
    )

    # Получаем все заказы
    orders = get_all_orders()
    # print(f"📦 Все заказы пользователя {user_id}: {orders}")  # Логируем

    # 🛠 Ищем заказ по `id`
    order_found = next(
        (order for order in orders if str(order["id"]) == str(order_id)), None
    )

    if not order_found:
        print(f"❌ Ошибка: заказ {order_id} не найден!")
        bot.answer_callback_query(call.id, "❌ Ошибка: заказ не найден.")
        return

    # Обновляем статус заказа в БД
    update_order_status_in_db(order_id, new_status)

    # Уведомляем клиента
    bot.send_message(
        user_id,
        f"📢 *Обновление статуса заказа!*\n\n"
        f"🚗 [{order_found['title']}]({order_found['link']})\n"
        f"📌 Новый статус:\n*{new_status}*",
        parse_mode="Markdown",
    )

    # Подтверждаем менеджеру
    bot.answer_callback_query(call.id, f"✅ Статус обновлён на {new_status}!")

    # Обновляем заказы у менеджеров
    show_orders(call.message)


@bot.callback_query_handler(func=lambda call: call.data.startswith("place_order_"))
def place_order(call):
    user_id = call.message.chat.id
    order_id = call.data.split("_")[-1]

    # Проверяем, есть ли этот заказ
    if order_id not in user_orders:
        bot.answer_callback_query(call.id, "❌ Ошибка: заказ не найден.")
        return

    order = user_orders[order_id]

    # Создаём кнопку "Обновить статус" (только для менеджеров)
    keyboard = types.InlineKeyboardMarkup()
    if user_id in MANAGERS:
        keyboard.add(
            types.InlineKeyboardButton(
                "📌 Обновить статус", callback_data=f"update_status_{order_id}"
            )
        )

    bot.send_message(
        user_id,
        f"📢 *Заказ оформлен!*\n\n"
        f"🚗 [{order['title']}]({order['link']})\n"
        f"👤 Клиент: [{order['user_name']}](tg://user?id={order['user_id']})\n"
        f"📌 *Текущий статус:* {order['status']}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )

    bot.answer_callback_query(call.id, "✅ Заказ отправлен менеджерам!")


################## КОД ДЛЯ СТАТУСОВ


@bot.callback_query_handler(func=lambda call: call.data == "check_subscription")
def check_subscription(call):
    user_id = call.from_user.id
    chat_member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)

    if chat_member.status in ["member", "administrator", "creator"]:
        bot.answer_callback_query(
            call.id, "✅ Подписка оформлена! Вы можете продолжить расчёты."
        )
        # Установить подписку для пользователя в БД
        update_user_subscription(user_id, True)
    else:
        bot.answer_callback_query(
            call.id,
            "🚫 Вы не подписались на канал! Оформите подписку, чтобы продолжить.",
        )


def is_user_subscribed(user_id):
    """Проверяет, подписан ли пользователь на канал."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember?chat_id={CHANNEL_USERNAME}&user_id={user_id}"
    response = requests.get(url).json()
    return response.get("ok") and response.get("result", {}).get("status") in [
        "member",
        "administrator",
        "creator",
    ]


def print_message(message):
    print("\n\n##############")
    print(f"{message}")
    print("##############\n\n")
    return None


# Функция для установки команд меню
def set_bot_commands():
    commands = [
        types.BotCommand("start", "Запустить бота"),
        types.BotCommand("exchange_rates", "Курсы валют"),
        types.BotCommand("my_cars", "Мои избранные автомобили"),
        types.BotCommand("users", "Статистика (для менеджеров)"),
        types.BotCommand("set_krw_rate", "Установить курс RUB → KRW (для менеджеров)"),
        types.BotCommand(
            "reset_krw_rate",
            "Сбросить кастомный курс RUB → KRW (для менеджеров)",
        ),
        # types.BotCommand("orders", "Список заказов (Для менеджеров)"),
    ]

    bot.set_my_commands(commands)


def get_usdt_to_krw_rate():
    global usdt_to_krw_rate

    # URL для получения курса USDT к KRW
    url = "https://api.coinbase.com/v2/exchange-rates?currency=USDT"
    response = requests.get(url)
    data = response.json()

    # Извлечение курса KRW
    krw_rate = data["data"]["rates"]["KRW"]
    usdt_to_krw_rate = float(krw_rate) - 11

    print(f"Курс USDT к KRW -> {str(usdt_to_krw_rate)}")

    return float(krw_rate) + 8


def get_rub_to_krw_rate():
    global rub_to_krw_rate, custom_rub_to_krw_rate

    # Если установлен кастомный курс, не перезаписываем его
    if custom_rub_to_krw_rate is not None:
        return custom_rub_to_krw_rate

    url = "https://www.cbr-xml-daily.ru/daily_json.js"

    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        krw_info = data["Valute"]["KRW"]
        krw_nominal = krw_info["Nominal"]  # 1000
        krw_value = krw_info["Value"] + 5
        krw_rate = float(krw_value) / float(krw_nominal)
        rub_to_krw_rate = krw_rate
    except requests.RequestException as e:
        print(f"Ошибка при получении курса RUB → KRW: {e}")
        return None


# Переменная для хранения кастомного курса
custom_rub_to_krw_rate = None


# Функция для сохранения кастомного курса в файл
def save_custom_rate():
    global custom_rub_to_krw_rate
    if custom_rub_to_krw_rate is not None:
        try:
            with open("custom_rate.json", "w") as f:
                json.dump({"rate": custom_rub_to_krw_rate}, f)
            print(f"Кастомный курс {custom_rub_to_krw_rate} сохранен")
        except Exception as e:
            print(f"Ошибка при сохранении кастомного курса: {e}")


# Функция для загрузки кастомного курса из файла
def load_custom_rate():
    global custom_rub_to_krw_rate, rub_to_krw_rate
    try:
        if os.path.exists("custom_rate.json"):
            with open("custom_rate.json", "r") as f:
                data = json.load(f)
                custom_rub_to_krw_rate = data.get("rate")
                rub_to_krw_rate = custom_rub_to_krw_rate  # Устанавливаем текущий курс равным кастомному
                print(f"Загружен кастомный курс: {custom_rub_to_krw_rate}")
    except Exception as e:
        print(f"Ошибка при загрузке кастомного курса: {e}")


# Загружаем кастомный курс при запуске
load_custom_rate()


@bot.message_handler(commands=["set_krw_rate"])
def set_custom_krw_rate(message):
    # Проверяем, является ли пользователь администратором
    if message.from_user.id not in MANAGERS:
        bot.reply_to(message, "У вас нет прав для использования этой команды.")
        return

    # Получаем текст сообщения и проверяем формат
    try:
        command_parts = message.text.split()
        if len(command_parts) != 2:
            bot.reply_to(message, "Неверный формат. Используйте: /set_krw_rate [число]")
            return

        # Пытаемся преобразовать введенное значение в число
        new_rate = float(command_parts[1].replace(",", "."))
        if new_rate <= 0:
            bot.reply_to(message, "Курс должен быть положительным числом.")
            return

        # Устанавливаем кастомный курс
        global custom_rub_to_krw_rate, rub_to_krw_rate
        custom_rub_to_krw_rate = new_rate
        rub_to_krw_rate = new_rate

        # Сохраняем кастомный курс в файл
        save_custom_rate()

        bot.reply_to(message, f"Установлен кастомный курс RUB → KRW: {new_rate:.4f}")
    except ValueError:
        bot.reply_to(message, "Неверный формат числа. Введите корректное число.")
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка: {str(e)}")


# Добавляем команду для сброса кастомного курса
@bot.message_handler(commands=["reset_krw_rate"])
def reset_custom_krw_rate(message):
    # Проверяем, является ли пользователь администратором
    if message.from_user.id not in MANAGERS:
        bot.reply_to(message, "У вас нет прав для использования этой команды.")
        return

    global custom_rub_to_krw_rate, rub_to_krw_rate
    custom_rub_to_krw_rate = None

    # Получаем актуальный курс
    get_rub_to_krw_rate()

    # Удаляем файл с сохраненным курсом
    try:
        if os.path.exists("custom_rate.json"):
            os.remove("custom_rate.json")
    except Exception as e:
        print(f"Ошибка при удалении файла с кастомным курсом: {e}")

    bot.reply_to(
        message,
        f"Кастомный курс сброшен. Текущий курс RUB → KRW: {rub_to_krw_rate:.4f}",
    )


# Обновление функции получения курса для учета кастомного значения
def get_actual_rub_to_krw_rate():
    global custom_rub_to_krw_rate, rub_to_krw_rate

    # Если установлен кастомный курс, используем его
    if custom_rub_to_krw_rate is not None:
        return custom_rub_to_krw_rate

    # Если стандартный курс не установлен или равен 0, пытаемся получить его
    if rub_to_krw_rate is None or rub_to_krw_rate <= 0:
        get_rub_to_krw_rate()

        # Если курс все еще не определен, используем значение по умолчанию
        if rub_to_krw_rate is None or rub_to_krw_rate <= 0:
            print("ВНИМАНИЕ: Используется значение курса по умолчанию!")
            return 0.0737  # Значение по умолчанию

    # Иначе используем стандартный курс
    return rub_to_krw_rate


def get_currency_rates():
    global cny_rub_rate

    # Fetch CNY rate
    cny = get_vtb_cnyrub_rate()
    cny_rub_rate = cny

    cny_text = f"CNY → RUB: <b>{cny_rub_rate:.2f} ₽</b>\n" if cny_rub_rate else ""

    rates_text = (
        f"Курсы обмена валют:\n\n"
        f"KRW → RUB: <b>{get_actual_rub_to_krw_rate():.5f} ₽</b>\n"
        f"{cny_text}"
    )

    return rates_text


# Функция для получения курсов валют с API
def get_usd_to_krw_rate():
    global usd_to_krw_rate

    url = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"

    try:
        response = requests.get(url)
        response.raise_for_status()  # Проверяем успешность запроса
        data = response.json()

        # Получаем курс и добавляем +25 KRW
        usd_to_krw = data.get("usd", {}).get("krw", 0) - 15
        usd_to_krw_rate = usd_to_krw

        print(f"Курс USD → KRW: {usd_to_krw_rate}")
    except requests.RequestException as e:
        print(f"Ошибка при получении курса USD → KRW: {e}")
        usd_to_krw_rate = None


def get_usd_to_rub_rate():
    global usd_to_rub_rate

    url = "https://mosca.moscow/api/v1/rate/"
    headers = {
        "Access-Token": "JI_piVMlX9TsvIRKmduIbZOWzLo-v2zXozNfuxxXj4_MpsUKd_7aQS16fExzA7MVFCVVoAAmrb_-aMuu_UIbJA"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Проверяем успешность запроса
        data = response.json()

        # Получаем курс USD → RUB
        usd_to_rub = data["buy"] + 2.57
        usd_to_rub_rate = usd_to_rub

        print(f"Курс USD → RUB: {usd_to_rub_rate}")
    except requests.RequestException as e:
        print(f"Ошибка при получении курса USD → RUB: {e}")
        usd_to_rub_rate = None


# Обработчик команды /cbr
@bot.message_handler(commands=["exchange_rates"])
def cbr_command(message):
    try:
        rates_text = get_currency_rates()

        # Создаем клавиатуру с кнопкой для расчета автомобиля
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость автомобиля", callback_data="calculate_another"
            )
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


def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(
        types.KeyboardButton(CALCULATE_CAR_TEXT),
        types.KeyboardButton("Ручной расчёт"),
        # types.KeyboardButton("Вопрос/Ответ"),
    )
    keyboard.add(
        types.KeyboardButton("Написать менеджеру"),
        types.KeyboardButton("О нас"),
        types.KeyboardButton("Telegram-канал"),
        types.KeyboardButton("YouTube"),
    )
    return keyboard


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


# Start command handler
@bot.message_handler(commands=["start"])
def send_welcome(message):
    add_user_if_not_exists(message.from_user)

    # Удаляем webhook перед стартом бота
    # Убираем обновление курса при старте
    # get_currency_rates()

    # Проверяем, подписан ли пользователь на канал
    user_id = message.from_user.id
    try:
        chat_member = bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        is_subscribed = chat_member.status in ["member", "administrator", "creator"]
    except Exception as e:
        print(f"Ошибка при проверке подписки: {e}")
        is_subscribed = False

    user_first_name = message.from_user.first_name
    welcome_message = (
        f"Здравствуйте, {user_first_name}!\n\n"
        "Я бот компании Импорт без проблем. Я помогу вам рассчитать стоимость понравившегося вам автомобиля из 🇰🇷Южной Кореи и 🇨🇳Китая до 🇷🇺 РФ.\n\n"
        "Выберите действие из меню ниже."
    )

    # Логотип компании
    logo_url = "https://res.cloudinary.com/dt0nkqowc/image/upload/v1744694951/Bratchikov/logo_gho09o.jpg"

    # Отправляем логотип перед сообщением
    bot.send_photo(
        message.chat.id,
        photo=logo_url,
    )

    # Если пользователь не подписан, предлагаем подписаться
    if not is_subscribed:
        subscription_keyboard = types.InlineKeyboardMarkup()
        subscription_keyboard.add(
            types.InlineKeyboardButton(
                "Подписаться на канал", url=f"https://t.me/{CHANNEL_USERNAME}"
            )
        )
        bot.send_message(
            message.chat.id,
            f"Для полного доступа к функциям бота, пожалуйста, подпишитесь на наш канал @{CHANNEL_USERNAME}",
            reply_markup=subscription_keyboard,
        )

    # Отправляем приветственное сообщение
    bot.send_message(message.chat.id, welcome_message, reply_markup=main_menu())


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

    if "fem.encar.com" in url:
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
        car_make = response.get("category", {}).get(
            "manufacturerEnglishName", ""
        )  # Марка
        car_model = response.get("category", {}).get(
            "modelGroupEnglishName", ""
        )  # Модель
        car_trim = response.get("category", {}).get(
            "gradeDetailEnglishName", ""
        )  # Комплектация

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
        car_type = response.get("spec", {}).get("bodyName", "")

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
    elif "kbchachacha.com" in url:
        url = f"https://www.kbchachacha.com/public/car/detail.kbc?carSeq={car_id_external}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,ru;q=0.9,en-CA;q=0.8,la;q=0.7,fr;q=0.6,ko;q=0.5",
            "Connection": "keep-alive",
        }

        response = requests.get(url=url, headers=headers)
        soup = BeautifulSoup(response.text, "html.parser")

        # Находим JSON в <script type="application/ld+json">
        json_script = soup.find("script", {"type": "application/ld+json"})
        if json_script:
            json_data = json.loads(json_script.text.strip())

            # Извлекаем данные
            car_name = json_data.get("name", "Неизвестная модель")
            car_images = json_data.get("image", [])[:10]  # Берем первые 10 фото
            car_price = json_data.get("offers", {}).get("price", "Не указано")

            # Находим таблицу с информацией
            table = soup.find("table", {"class": "detail-info-table"})
            if table:
                rows = table.find_all("tr")

                # Достаём данные
                car_number = None
                car_year = None
                car_mileage = None
                car_fuel = None
                car_engine_displacement = None

                for row in rows:
                    headers = row.find_all("th")
                    values = row.find_all("td")

                    for th, td in zip(headers, values):
                        header_text = th.text.strip()
                        value_text = td.text.strip()

                        if header_text == "차량정보":  # Номер машины
                            car_number = value_text
                        elif header_text == "연식":  # Год выпуска
                            car_year = value_text
                        elif header_text == "주행거리":  # Пробег
                            car_mileage = value_text
                        elif header_text == "연료":  # Топливо
                            car_fuel = value_text
                        elif header_text == "배기량":  # Объем двигателя
                            car_engine_displacement = value_text
            else:
                print("❌ Таблица информации не найдена")

            # Если объем двигателя не найден или равен 0, пытаемся извлечь из названия
            if not car_engine_displacement or car_engine_displacement == "0cc":
                # Ищем числа с точкой (например, 2.0) в названии автомобиля
                engine_match = re.search(r"(\d+\.\d+)", car_name)
                if engine_match:
                    # Преобразуем, например, 2.0 в 2000cc
                    engine_size = float(engine_match.group(1))
                    car_engine_displacement = f"{int(engine_size * 1000)}cc"
                    print(
                        f"✅ Извлечен объем двигателя из названия: {car_engine_displacement}"
                    )
                else:
                    # Ищем просто числа (например, 2000) в названии
                    engine_match = re.search(r"(\d{3,4})", car_name)
                    if engine_match and 500 <= int(engine_match.group(1)) <= 9000:
                        car_engine_displacement = f"{engine_match.group(1)}cc"
                        print(
                            f"✅ Извлечен объем двигателя из названия: {car_engine_displacement}"
                        )

            car_info = {
                "name": car_name,
                "car_price": car_price,
                "images": car_images,
                "number": car_number,
                "year": car_year,
                "mileage": car_mileage,
                "fuel": car_fuel,
                "engine_volume": car_engine_displacement,
                "transmission": "오토",
            }

            return car_info
        else:
            print(
                "❌ Не удалось найти JSON-данные в <script type='application/ld+json'>"
            )
    elif "kcar" in url:
        print("🔍 Парсим KCar.com...")

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en,ru;q=0.9,en-CA;q=0.8,la;q=0.7,fr;q=0.6,ko;q=0.5",
            "Referer": "https://www.kcar.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }

        response = requests.get(url, headers=headers)
        json_response = response.json()

        data = json_response.get("data", {})

        car_name = data.get("rvo", {}).get("carWhlNm", "")
        car_price = data.get("rvo", {}).get("npriceFullType", "")
        car_mileage = data.get("rvo", {}).get("milg", "")
        car_engine_displacement = data.get("rvo", {}).get("engdispmnt", "")
        transmission = data.get("rvo", {}).get("trnsmsncdNm", "")
        car_number = data.get("rvo", {}).get("cno", "")

        car_images = data.get("photoList", [])

        # Фильтруем фото, у которых есть "sortOrdr", и сортируем по этому значению
        sorted_images = sorted(
            [photo for photo in car_images if photo.get("sortOrdr")],
            key=lambda x: int(x["sortOrdr"]),
        )

        # Берём первые 10 и достаём ссылки
        car_image_urls = [photo["elanPath"] for photo in sorted_images[:10]]

        car_year = data.get("rvo", {}).get(
            "fstCarRegYm", ""
        )  # Приходит в таком формате 202211

        year = car_year[0:4]
        month = car_year[4:]

        car_fuel = data.get("rvo", {}).get("fuelTypecdNm", "")

        car_insurance_history = data.get("carHistoryAccList", [])
        own_damage_total = 0
        other_damage_total = 0

        if len(car_insurance_history) > 0:
            for record in car_insurance_history:
                own_damage_total += record.get("reprEstmCost2", 0)
                other_damage_total += record.get("reprEstmCost1", 0)

        car_info = {
            "name": car_name,
            "car_price": car_price,
            "images": car_image_urls,
            "number": car_number,
            "year": year,
            "month": month,
            "mileage": car_mileage,
            "fuel": car_fuel,
            "engine_volume": car_engine_displacement,
            "transmission": transmission,
            "own_damage_total": own_damage_total,
            "other_damage_total": other_damage_total,
        }

        return car_info


# Function to calculate the total cost
def calculate_cost(link, message, user_type):
    global car_data, car_id_external, car_month, car_year, krw_rub_rate, eur_rub_rate, rub_to_krw_rate, usd_rate, usdt_to_krw_rate

    # Теперь только получаем курсы, но не сбрасываем кастомные
    bot.send_message(
        message.chat.id,
        "✅ Подгружаю актуальный курс валют и делаю расчёты. ⏳ Пожалуйста подождите...",
        parse_mode="Markdown",
    )

    print_message("ЗАПРОС НА РАСЧЁТ АВТОМОБИЛЯ")

    # Отправляем сообщение и сохраняем его ID
    processing_message = bot.send_message(message.chat.id, "Обрабатываю данные... ⏳")

    car_id = None
    car_title = ""

    if "fem.encar.com" in link:
        car_id_match = re.findall(r"\d+", link)
        if car_id_match:
            car_id = car_id_match[0]  # Use the first match of digits
            car_id_external = car_id
            link = f"https://fem.encar.com/cars/detail/{car_id}"
        else:
            send_error_message(message, "🚫 Не удалось извлечь carid из ссылки.")
            return

    elif "kbchachacha.com" in link or "m.kbchachacha.com" in link:
        parsed_url = urlparse(link)
        query_params = parse_qs(parsed_url.query)

        print(f"Обработка ссылки KBChaCha: {link}")
        print(f"Путь URL: {parsed_url.path}")
        print(f"Query параметры: {query_params}")

        # Попытка 1: обычный carSeq в параметрах (поддерживает все форматы включая /public/web/car/detail.kbc)
        car_id = query_params.get("carSeq", [None])[0]

        if car_id:
            print(f"Найден carSeq в параметрах: {car_id}")

        # Попытка 2: если есть параметр `c=...`, надо выполнить редирект
        if not car_id and query_params.get("c"):
            print("Найден параметр 'c', выполняем редирект...")
            try:
                response = requests.get(link, allow_redirects=True, timeout=5)
                redirected_url = response.url
                print(f"URL после редиректа: {redirected_url}")
                redirected_query = parse_qs(urlparse(redirected_url).query)
                car_id = redirected_query.get("carSeq", [None])[0]
                if car_id:
                    print(f"Найден carSeq после редиректа: {car_id}")
            except Exception as e:
                print(f"Ошибка при обработке редиректа KBChaCha: {e}")
                send_error_message(message, "🚫 Ошибка при обработке ссылки KBChaCha.")
                return

        # Попытка 3: проверяем фрагмент URL (после #) на случай если carSeq там
        if not car_id and parsed_url.fragment:
            print(f"Проверяем фрагмент URL: {parsed_url.fragment}")
            fragment_params = parse_qs(parsed_url.fragment)
            car_id = fragment_params.get("carSeq", [None])[0]
            if car_id:
                print(f"Найден carSeq во фрагменте: {car_id}")

        if car_id:
            car_id_external = car_id
            # Нормализуем ссылку к стандартному формату
            link = f"https://www.kbchachacha.com/public/car/detail.kbc?carSeq={car_id}"
            print(f"Нормализованная ссылка: {link}")
        else:
            print("Не удалось извлечь carSeq из ссылки")
            send_error_message(message, "🚫 Не удалось извлечь carSeq из ссылки.")
            return

    elif "kcar.com" in link:
        parsed_url = urlparse(link)
        query_params = parse_qs(parsed_url.query)

        if "i_sCarCd" in query_params:
            car_id = query_params["i_sCarCd"][0]
            car_id_external = car_id
            link = f"https://api.kcar.com/bc/car-info-detail-of-ng?i_sCarCd={car_id}&i_sPassYn=N&bltbdKnd=CM050"
        else:
            send_error_message(
                message, "🚫 Не удалось извлечь ID автомобиля из ссылки KCar."
            )
            return

    else:
        # Извлекаем carid с URL encar
        parsed_url = urlparse(link)
        query_params = parse_qs(parsed_url.query)
        car_id = query_params.get("carid", [None])[0]

    # Переменные для pan-auto данных
    pan_auto_data = None
    use_pan_auto_customs = False
    car_hp = None
    car_manufacturer = None
    car_model = None
    car_generation = None

    # Если ссылка с encar
    if "fem.encar.com" in link:
        # Сначала пробуем получить данные из pan-auto.ru (там есть HP и готовые расчёты)
        pan_auto_data = get_pan_auto_data(car_id)

        if pan_auto_data and pan_auto_data.get("hp") and pan_auto_data.get("clearance_cost"):
            print(f"Pan-auto.ru: Данные получены успешно для car_id={car_id}")
            use_pan_auto_customs = True
            car_hp = pan_auto_data["hp"]
            car_manufacturer = pan_auto_data.get("manufacturer")
            car_model = pan_auto_data.get("model")
            car_generation = pan_auto_data.get("generation")
        else:
            print(f"Pan-auto.ru: Данные не получены или неполные для car_id={car_id}")

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

        # Если pan-auto вернул данные, используем manufacturer/model/generation оттуда для сохранения HP
        if pan_auto_data:
            car_manufacturer = pan_auto_data.get("manufacturer") or car_manufacturer
            car_model = pan_auto_data.get("model") or car_model
            car_generation = pan_auto_data.get("generation") or car_generation

        preview_link = f"https://fem.encar.com/cars/detail/{car_id}"

    # Если ссылка с kbchacha
    if "kbchachacha.com" in link:
        result = get_car_info(link)

        car_title = result["name"]

        match = re.search(r"(\d{2})년(\d{2})월", result["year"])
        if match:
            car_year = match.group(1)
            car_month = match.group(2)  # Получаем двухзначный месяц
        else:
            car_year = "Не найдено"
            car_month = "Не найдено"

        month = car_month
        year = car_year

        car_engine_displacement = re.sub(r"[^\d]", "", result["engine_volume"])
        car_engine_displacement = (
            2200 if result["fuel"] == "디젤" else car_engine_displacement
        )

        car_price = int(result["car_price"]) / 10000
        formatted_car_date = f"01{car_month}{match.group(1)}"
        formatted_mileage = result["mileage"]
        formatted_transmission = (
            "Автомат" if "오토" in result["transmission"] else "Механика"
        )
        car_photos = result["images"]

        preview_link = (
            f"https://www.kbchachacha.com/public/car/detail.kbc?carSeq={car_id}"
        )

    if "kcar" in link:
        result = get_car_info(link)

        car_title = result["name"]

        month = result["month"]
        year = result["year"]

        car_month = month
        car_year = year[2:]

        car_engine_displacement = re.sub(r"\D+", "", result["engine_volume"])
        car_price = int(result["car_price"]) / 10000

        car_photos = result["images"]

        # Форматируем дату
        formatted_car_date = (
            f"01{car_month}{car_year[-2:]}"
            if car_year != "Не найдено"
            else "Не найдено"
        )

        # Форматируем пробег
        formatted_mileage = format_number(result["mileage"]) + " км"

        # Определяем КПП
        formatted_transmission = (
            "Автомат" if "오토" in result["transmission"] else "Механика"
        )

        preview_link = f"https://www.kcar.com/bc/detail/carInfoDtl?i_sCarCd={car_id}"

        own_car_insurance_payments = result["own_damage_total"]
        other_car_insurance_payments = result["other_damage_total"]

    if not car_price and car_engine_displacement and formatted_car_date:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Написать менеджеру", url="https://t.me/bratchikov_y"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another",
            )
        )
        bot.send_message(
            message.chat.id, "Ошибка", parse_mode="Markdown", reply_markup=keyboard
        )
        bot.delete_message(message.chat.id, processing_message.message_id)
        return

    if car_price and car_engine_displacement and formatted_car_date:
        car_engine_displacement = int(car_engine_displacement)

        # Форматирование данных
        formatted_car_year = f"20{car_year}"
        engine_volume_formatted = f"{format_number(car_engine_displacement)} cc"

        age = calculate_age(int(formatted_car_year), car_month)

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
        price_krw = int(car_price) * 10000
        price_rub = price_krw * get_actual_rub_to_krw_rate()
        # price_usd = price_krw / usd_to_krw_rate

        # Определяем таможенные платежи
        customs_fee = None
        customs_duty = None
        recycling_fee = None

        # 1. Если есть данные из pan-auto.ru, используем их напрямую
        if use_pan_auto_customs and pan_auto_data:
            customs_fee = int(pan_auto_data.get("clearance_cost", 0))
            customs_duty = int(pan_auto_data.get("customs_duty", 0))
            recycling_fee = int(pan_auto_data.get("utilization_fee", 0))
            print(f"Используем таможенные данные из pan-auto.ru: sbor={customs_fee}, tax={customs_duty}, util={recycling_fee}")

        # 2. Иначе нужно получить HP и использовать calcus.ru
        else:
            # Для KBChaCha и KCar нужно получить manufacturer/model из названия авто
            if not car_manufacturer and car_title:
                # Парсим первое слово как производителя
                title_parts = car_title.split()
                if title_parts:
                    car_manufacturer = title_parts[0]
                    car_model = title_parts[1] if len(title_parts) > 1 else ""

            # Пробуем получить HP из базы данных
            stored_hp = get_stored_hp(car_manufacturer, car_model, car_generation, car_engine_displacement)

            if stored_hp:
                print(f"HP найден в базе данных: {stored_hp}")
                car_hp = stored_hp
                response = get_customs_fees(
                    car_engine_displacement,
                    price_krw,
                    int(formatted_car_year),
                    car_month,
                    engine_type=1,
                    owner_type=user_type,
                    power=car_hp,
                )
                customs_fee = clean_number(response["sbor"])
                customs_duty = clean_number(response["tax"])
                recycling_fee = clean_number(response["util"])
            else:
                # HP не найден - нужно запросить у пользователя
                print(f"HP не найден. Запрашиваем у пользователя...")

                # Сохраняем контекст для продолжения расчёта после ввода HP
                pending_hp_input[message.from_user.id] = {
                    "car_id": car_id,
                    "car_title": car_title,
                    "car_price": car_price,
                    "car_engine_displacement": car_engine_displacement,
                    "formatted_car_date": formatted_car_date,
                    "formatted_mileage": formatted_mileage,
                    "formatted_transmission": formatted_transmission,
                    "car_photos": car_photos,
                    "year": year,
                    "month": month,
                    "car_year": car_year,
                    "car_month": car_month,
                    "formatted_car_year": formatted_car_year,
                    "price_krw": price_krw,
                    "price_rub": price_rub,
                    "age": age,
                    "age_formatted": age_formatted,
                    "engine_volume_formatted": engine_volume_formatted,
                    "preview_link": preview_link,
                    "link": link,
                    "user_type": user_type,
                    "car_manufacturer": car_manufacturer,
                    "car_model": car_model,
                    "car_generation": car_generation,
                    "processing_message_id": processing_message.message_id,
                }

                # Создаём клавиатуру с кнопкой отмены
                cancel_keyboard = types.InlineKeyboardMarkup()
                cancel_keyboard.add(
                    types.InlineKeyboardButton(
                        "❌ Отмена",
                        callback_data="cancel_hp_input",
                    )
                )

                bot.send_message(
                    message.chat.id,
                    f"⚠️ <b>Для данного автомобиля не найдена информация о мощности.</b>\n\n"
                    f"🚗 {car_title}\n"
                    f"🔧 Объём двигателя: {engine_volume_formatted}\n\n"
                    f"Пожалуйста, введите мощность двигателя в л.с. (например: 159):",
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard,
                )
                return  # Выходим и ждём ввода HP от пользователя

        # Расчет итоговой стоимости автомобиля в рублях
        total_cost = (
            price_rub  # Цена авто в рублях
            + 2000000 * get_actual_rub_to_krw_rate()  # Расходы по Корее
            + customs_fee  # Таможенный сбор
            + customs_duty  # Таможенная пошлина
            + recycling_fee  # Утильсбор
            + 15000  # Брокер РФ
            + 30000  # Временная регистрация
            + 45000  # СВХ
            + 25000  # Лаборатория
            + 2000  # Коносамент
            + 2000  # Экспертиза
            + 8000  # Перегон из СВХ
            + 120000  # Услуга Юрия
            + (
                20000 if car_engine_displacement > 2000 else 0
            )  # За санкционную добавляется «услуга консультанта - 20.000
        )

        total_cost_krw = (
            price_krw  # Цена авто в вонах
            + 2000000  # Расходы по Корее
            + customs_fee * get_actual_rub_to_krw_rate()  # Таможенный сбор
            + customs_duty * get_actual_rub_to_krw_rate()  # Таможенная пошлина
            + recycling_fee * get_actual_rub_to_krw_rate()  # Утильсбор
            + 15000 * get_actual_rub_to_krw_rate()  # Брокер РФ
            + 30000 * get_actual_rub_to_krw_rate()  # Временная регистрация
            + 45000 * get_actual_rub_to_krw_rate()  # СВХ
            + 25000 * get_actual_rub_to_krw_rate()  # Лаборатория
            + 2000 * get_actual_rub_to_krw_rate()  # Коносамент
            + 2000 * get_actual_rub_to_krw_rate()  # Экспертиза
            + 8000 * get_actual_rub_to_krw_rate()  # Перегон из СВХ
            + 120000 * get_actual_rub_to_krw_rate()  # Услуга Юрия
            + (
                20000 / get_actual_rub_to_krw_rate()
                if car_engine_displacement > 2000
                else 0
            )  # За санкционную добавляется «услуга консультанта"
        )

        # car_data["total_cost_usd"] = total_cost_usd
        car_data["total_cost_krw"] = total_cost_krw
        car_data["total_cost_rub"] = total_cost

        # Стоимость автомобиля
        car_data["car_price_krw"] = price_krw
        # car_data["car_price_usd"] = price_usd
        car_data["car_price_rub"] = price_rub

        # Стояночные
        car_data["parking_korea_krw"] = 440000
        car_data["parking_korea_rub"] = 440000 * get_actual_rub_to_krw_rate()
        # car_data["parking_korea_usd"] = 440000 / usd_to_krw_rate

        # Осмотр
        car_data["car_review_krw"] = 300000
        car_data["car_review_rub"] = 300000 * get_actual_rub_to_krw_rate()
        # car_data["car_review_usd"] = 300000 / usd_to_krw_rate

        # Документы
        car_data["korea_documents_krw"] = 150000
        car_data["korea_documents_rub"] = 150000 * get_actual_rub_to_krw_rate()
        # car_data["korea_documents_usd"] = 150000 / usd_to_krw_rate

        # Перевозка
        car_data["transfer_korea_krw"] = 230000
        car_data["transfer_korea_rub"] = 230000 * get_actual_rub_to_krw_rate()
        # car_data["transfer_korea_usd"] = 230000 / usd_to_krw_rate

        # Фрахт
        car_data["freight_korea_krw"] = 880000
        car_data["freight_korea_rub"] = 880000 * get_actual_rub_to_krw_rate()
        # car_data["freight_korea_usd"] = 880000 / usd_to_krw_rate

        # Расходы по РФ
        car_data["customs_duty_rub"] = customs_duty
        car_data["customs_duty_krw"] = customs_duty / get_actual_rub_to_krw_rate()
        # car_data["customs_duty_usd"] = customs_duty / usd_to_rub_rate

        car_data["customs_fee_rub"] = customs_fee
        car_data["customs_fee_krw"] = customs_fee / get_actual_rub_to_krw_rate()
        # car_data["customs_fee_usd"] = customs_fee / usd_to_rub_rate

        car_data["util_fee_rub"] = recycling_fee
        car_data["util_fee_krw"] = recycling_fee / get_actual_rub_to_krw_rate()
        # car_data["util_fee_usd"] = recycling_fee / usd_to_rub_rate

        car_data["perm_registration_rub"] = 15000
        car_data["perm_registration_krw"] = 15000 / get_actual_rub_to_krw_rate()
        # car_data["perm_registration_usd"] = 15000 / usd_to_rub_rate

        car_data["broker_rub"] = 30000
        car_data["broker_krw"] = 30000 / get_actual_rub_to_krw_rate()
        # car_data["broker_usd"] = 30000 / usd_to_rub_rate

        car_data["svh_rub"] = 45000
        car_data["svh_krw"] = 45000 / get_actual_rub_to_krw_rate()
        # car_data["svh_usd"] = 45000 / usd_to_rub_rate

        car_data["lab_rub"] = 25000
        car_data["lab_krw"] = 25000 / get_actual_rub_to_krw_rate()
        # car_data["lab_usd"] = 25000 / usd_to_rub_rate

        car_data["konosament_rub"] = 2000
        car_data["konosament_krw"] = 2000 / get_actual_rub_to_krw_rate()
        # car_data["konosament_usd"] = 2000 / usd_to_rub_rate

        car_data["expertise_rub"] = 2000
        car_data["expertise_krw"] = 2000 / get_actual_rub_to_krw_rate()
        # car_data["expertise_usd"] = 2000 / usd_to_rub_rate

        car_data["svh_transfer_rub"] = 8000
        car_data["svh_transfer_krw"] = 8000 / get_actual_rub_to_krw_rate()
        # car_data["svh_transfer_usd"] = 8000 / usd_to_rub_rate

        car_data["consultant_fee_rub"] = 20000 if car_engine_displacement > 2000 else 0
        car_data["consultant_fee_krw"] = (
            20000 / get_actual_rub_to_krw_rate()
            if car_engine_displacement > 2000
            else 0
        )

        car_data["yuri_fee_rub"] = 120000
        car_data["yuri_fee_krw"] = 120000 / get_actual_rub_to_krw_rate()
        # car_data["yuri_fee_usd"] = 120000 / usd_to_rub_rate

        # car_data["consultant_fee_usd"] = (
        #     20000 / usd_to_rub_rate if car_engine_displacement > 2000 else 0
        # )

        car_insurance_payments_chutcha = ""
        if "kcar" in link:
            own_insurance_text = (
                f"₩{format_number(own_car_insurance_payments)}"
                if isinstance(own_car_insurance_payments, int)
                else "Нет"
            )
            other_insurance_text = (
                f"₩{format_number(other_car_insurance_payments)}"
                if isinstance(other_car_insurance_payments, int)
                else "Нет"
            )

            car_insurance_payments_chutcha = (
                f"Страховые выплаты по данному автомобилю:\n{own_insurance_text}\n"
                f"Страховые выплаты другому автомобилю:\n{other_insurance_text}\n\n"
            )

        # Формирование сообщения результата
        # <b>${format_number(total_cost_usd)}</b> |
        # f"Стоимость автомобиля в Корее: ₩{format_number(price_krw)}\n"
        # f"Стоимость автомобиля под ключ до Владивостока:\n<b>₩{format_number(total_cost_krw)}</b> | <b>{format_number(total_cost)} ₽</b>\n\n"

        result_message = (
            f"🚗 {car_title}\n\n"
            f"🗓 Возраст: {age_formatted} (дата регистрации: {month}/{year})\n"
            f"🛣 Пробег: {formatted_mileage}\n"
            f"🔧 Объём двигателя: {engine_volume_formatted}\n"
            f"⚙️ КПП: {formatted_transmission}\n\n"
            f"💵 <b>Курс Воны к Рублю: {get_actual_rub_to_krw_rate():.4f} ₽</b>\n\n"
            f"🇰🇷 Платежи в Корее\n"
            f"▪️ Стоимость автомобиля: <b>₩{format_number(car_data['car_price_krw'])}</b> | <b>{format_number(car_data['car_price_rub'])} ₽</b>\n"
            f"▪️ Расходы по Корее (Фрахт, Стояночные, Логистика, Осмотр, Экспортные документы): <b>₩{format_number(2000000)}</b> | <b>{format_number(2000000 * get_actual_rub_to_krw_rate())} ₽</b>\n\n\n"
            f"🇷🇺 Платежи в России\n"
            f"▪️ <b>Единая таможенная ставка</b>: <b>{format_number(car_data['customs_duty_rub'])} ₽</b>\n"
            f"▪️ <b>Таможенное оформление</b>: <b>{format_number(car_data['customs_fee_rub'])} ₽</b>\n"
            f"▪️ <b>Утилизационный сбор</b>: <b>{format_number(car_data['util_fee_rub'])} ₽</b>\n\n"
            f"▪️ Брокер: <b>{format_number(car_data['broker_rub'])} ₽</b>\n"
            f"▪️ Временная регистрация: <b>{format_number(car_data['perm_registration_rub'])} ₽</b>\n"
            f"▪️ СВХ: <b>{format_number(car_data['svh_rub'])} ₽</b>\n"
            f"▪️ Лаборатория: <b>{format_number(car_data['lab_rub'])} ₽</b>\n"
            f"▪️ Коносамент: <b>{format_number(car_data['konosament_rub'])} ₽</b>\n"
            f"▪️ Экспертиза: <b>{format_number(car_data['expertise_rub'])} ₽</b>\n"
            f"▪️ Перегон из СВХ: <b>{format_number(car_data['svh_transfer_rub'])} ₽</b>\n"
            f"▪️ Услуги консультанта: <b>{format_number(car_data['consultant_fee_rub'])} ₽</b>\n"
            f"▪️ Моя комиссия: <b>{format_number(car_data['yuri_fee_rub'])} ₽</b>\n\n"
            f"🟰 Итого под ключ до Владивостока: <b>{format_number(car_data['total_cost_rub'])} ₽</b>\n\n"
            f"{car_insurance_payments_chutcha}"
            f"🔗 <a href='{preview_link}'>Ссылка на автомобиль</a>\n\n"
            "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у меня:\n"
            f"▪️ @bratchikov_y (Юрий)\n\n"
            "🔗 <a href='https://t.me/bratchikov_cars'>Официальный телеграм канал</a>\n"
        )

        # Клавиатура с дальнейшими действиями
        keyboard = types.InlineKeyboardMarkup()
        # keyboard.add(
        #     types.InlineKeyboardButton("Детали расчёта", callback_data="detail")
        # )

        # Кнопка для добавления в избранное
        keyboard.add(
            types.InlineKeyboardButton(
                "⭐ Добавить в избранное",
                callback_data=f"add_favorite_{car_id_external}",
            )
        )

        if "fem.encar.com" in link:
            keyboard.add(
                types.InlineKeyboardButton(
                    "Технический Отчёт об Автомобиле", callback_data="technical_card"
                )
            )
            keyboard.add(
                types.InlineKeyboardButton(
                    "Выплаты по ДТП",
                    callback_data="technical_report",
                )
            )
        keyboard.add(
            types.InlineKeyboardButton(
                "Написать менеджеру", url="https://t.me/bratchikov_y"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Расчёт другого автомобиля",
                callback_data="calculate_another",
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Главное меню",
                callback_data="main_menu",
            )
        )

        # Отправляем до 10 фотографий
        media_group = []
        for photo_url in sorted(car_photos):
            try:
                response = requests.get(photo_url)
                if response.status_code == 200:
                    photo = BytesIO(response.content)  # Загружаем фото в память
                    media_group.append(
                        types.InputMediaPhoto(photo)
                    )  # Добавляем в список

                    # Если набрали 10 фото, отправляем альбом
                    if len(media_group) == 10:
                        bot.send_media_group(message.chat.id, media_group)
                        media_group.clear()  # Очищаем список для следующей группы
                else:
                    print(f"Ошибка загрузки фото: {photo_url} - {response.status_code}")
            except Exception as e:
                print(f"Ошибка при обработке фото {photo_url}: {e}")

        # Отправка оставшихся фото, если их меньше 10
        if media_group:
            bot.send_media_group(message.chat.id, media_group)

        car_data["car_id"] = car_id
        car_data["name"] = car_title
        car_data["images"] = car_photos if isinstance(car_photos, list) else []
        car_data["link"] = preview_link
        car_data["year"] = year
        car_data["month"] = month
        car_data["mileage"] = formatted_mileage
        car_data["engine_volume"] = car_engine_displacement
        car_data["transmission"] = formatted_transmission
        car_data["car_price"] = price_krw
        car_data["user_name"] = message.from_user.username
        car_data["first_name"] = message.from_user.first_name
        car_data["last_name"] = message.from_user.last_name

        bot.send_message(
            message.chat.id,
            result_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        bot.delete_message(
            message.chat.id, processing_message.message_id
        )  # Удаляем сообщение о передаче данных в обработку

    else:
        send_error_message(
            message,
            "🚫 Произошла ошибка при получении данных. Проверьте ссылку и попробуйте снова.",
        )
        bot.delete_message(message.chat.id, processing_message.message_id)


# ==================== CHINA (CHE168) CALCULATION ====================

def calculate_china_cost(link, message, user_type):
    """
    Calculate import cost for a car from Che168.com (China).
    """
    global car_data, cny_rub_rate

    print_message("ЗАПРОС НА РАСЧЁТ АВТОМОБИЛЯ ИЗ КИТАЯ")

    user_id = message.chat.id

    # Fetch CNY rate if not available
    if cny_rub_rate is None:
        cny = get_vtb_cnyrub_rate()
        cny_rub_rate = cny

    if cny_rub_rate is None:
        bot.send_message(
            user_id,
            "Ошибка: не удалось получить курс юаня. Попробуйте позже."
        )
        return

    # Send processing message
    bot.send_message(
        user_id,
        "✅ Подгружаю актуальный курс валют и делаю расчёты. ⏳ Пожалуйста подождите...",
        parse_mode="Markdown",
    )
    processing_message = bot.send_message(user_id, "Обрабатываю данные... ⏳")

    # Extract car ID from URL
    car_id = extract_car_id_from_che168_url(link)
    if not car_id:
        bot.delete_message(user_id, processing_message.message_id)
        send_error_message(message, "🚫 Не удалось извлечь ID автомобиля из ссылки.")
        return

    # Fetch car info from Che168 API (with proxy fallback)
    car_info = get_che168_car_info_with_fallback(car_id)
    if not car_info:
        bot.delete_message(user_id, processing_message.message_id)
        send_error_message(message, "🚫 Не удалось получить данные об автомобиле. Попробуйте позже.")
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

    # Store pending data
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
        "user_type": user_type,
    }

    # Check if HP was successfully extracted and is valid
    if horsepower and 50 <= horsepower <= 1000:
        pending_china_hp_requests[user_id]["hp"] = horsepower
        logging.info(f"Using auto-extracted HP: {horsepower} for user {user_id}")

        # Check if fuel type is also valid
        valid_fuel_types = {1, 2, 4, 5, 6}
        if fuel_type_code in valid_fuel_types:
            logging.info(f"Using auto-extracted fuel type: {fuel_type_code} ({fuel_type_ru}) for user {user_id}")

            bot.send_message(
                user_id,
                f"🚗 {car_name}\n"
                f"📍 {city_name}\n"
                f"💰 ¥{price_cny:,}\n"
                f"🐎 {horsepower} л.с.\n"
                f"⛽ {fuel_type_ru}\n\n"
                "⏳ Выполняю расчёт..."
            )

            complete_china_calculation(user_id, message)
        else:
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

    pending_china_hp_requests[user_id]["hp"] = hp

    keyboard = create_fuel_type_keyboard()
    bot.send_message(
        user_id,
        "Выберите тип двигателя:",
        reply_markup=keyboard
    )


def complete_china_calculation(user_id, message):
    """Complete China car cost calculation after HP and fuel type are selected."""
    global car_data, cny_rub_rate

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
    user_type = pending_data.get("user_type", 1)
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
        owner_type=user_type,
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
    china_expenses_rub = CHINA_EXPENSES * cny_rub_rate

    china_total_cny = car_price_after_deposit + CHINA_EXPENSES
    china_total_rub = china_total_cny * cny_rub_rate

    russia_expenses_rub = (
        customs_duty + customs_fee + recycling_fee +
        CHINA_AGENT_FEE + CHINA_BROKER_FEE + CHINA_SVH_FEE + CHINA_LAB_FEE
    )

    total_cost_rub = first_payment_rub + china_total_rub + russia_expenses_rub + CHINA_YURI_FEE
    total_cost_cny = total_cost_rub / cny_rub_rate

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
    car_data["china_expenses_cny"] = CHINA_EXPENSES
    car_data["china_expenses_rub"] = china_expenses_rub
    car_data["china_total_cny"] = china_total_cny
    car_data["china_total_rub"] = china_total_rub
    car_data["customs_duty_rub"] = customs_duty
    car_data["customs_fee_rub"] = customs_fee
    car_data["util_fee_rub"] = recycling_fee
    car_data["agent_russia_rub"] = CHINA_AGENT_FEE
    car_data["broker_russia_rub"] = CHINA_BROKER_FEE
    car_data["svh_russia_rub"] = CHINA_SVH_FEE
    car_data["lab_russia_rub"] = CHINA_LAB_FEE
    car_data["yuri_fee_rub"] = CHINA_YURI_FEE
    car_data["total_cost_rub"] = total_cost_rub
    car_data["total_cost_cny"] = total_cost_cny
    car_data["link"] = link
    car_data["car_name"] = car_name
    car_data["fuel_type_name"] = fuel_type_name
    car_data["car_id"] = pending_data.get("car_id", "")
    car_data["name"] = car_name
    car_data["images"] = photos if isinstance(photos, list) else []

    # Format mileage
    car_info = pending_data.get("car_info", {})
    mileage_km = car_info.get("mileage_km", 0)
    gearbox = car_info.get("gearbox", "")

    result_message = (
        f"🚗 {car_name}\n\n"
        f"🗓 Возраст: {age_formatted} (дата регистрации: {month:02d}/{year})\n"
        f"🛣 Пробег: {format_che168_mileage(mileage_km)}\n"
        f"🔧 Объём двигателя: {format_number(displacement_cc)} cc\n"
        f"🐎 Мощность: {hp} л.с.\n"
        f"⚙️ КПП: {format_che168_gearbox(gearbox)}\n"
        f"⛽ Тип двигателя: {fuel_type_name}\n\n"
        f"💵 <b>Курс Юаня к Рублю: {cny_rub_rate:.2f} ₽</b>\n\n"
        f"🇨🇳 Платежи в Китае\n"
        f"▪️ Стоимость автомобиля: <b>¥{format_number(price_cny)}</b> | <b>{format_number(int(price_cny * cny_rub_rate))} ₽</b>\n"
        f"▪️ Расходы по Китаю (дилерский сбор, доставка, оформление): <b>¥{format_number(CHINA_EXPENSES)}</b> | <b>{format_number(int(CHINA_EXPENSES * cny_rub_rate))} ₽</b>\n\n\n"
        f"🇷🇺 Платежи в России\n"
        f"▪️ <b>Единая таможенная ставка</b>: <b>{format_number(customs_duty)} ₽</b>\n"
        f"▪️ <b>Таможенное оформление</b>: <b>{format_number(customs_fee)} ₽</b>\n"
        f"▪️ <b>Утилизационный сбор</b>: <b>{format_number(recycling_fee)} ₽</b>\n\n"
        f"▪️ Агентские услуги: <b>{format_number(CHINA_AGENT_FEE)} ₽</b>\n"
        f"▪️ Брокер: <b>{format_number(CHINA_BROKER_FEE)} ₽</b>\n"
        f"▪️ СВХ: <b>{format_number(CHINA_SVH_FEE)} ₽</b>\n"
        f"▪️ Лаборатория: <b>{format_number(CHINA_LAB_FEE)} ₽</b>\n"
        f"▪️ Моя комиссия: <b>{format_number(CHINA_YURI_FEE)} ₽</b>\n\n"
        f"🟰 Итого под ключ: <b>¥{format_number(int(total_cost_cny))}</b> | <b>{format_number(int(total_cost_rub))} ₽</b>\n\n"
        f"🔗 <a href='{link}'>Ссылка на автомобиль</a>\n\n"
        "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у меня:\n"
        f"▪️ @bratchikov_y (Юрий)\n\n"
        "🔗 <a href='https://t.me/bratchikov_cars'>Официальный телеграм канал</a>\n"
    )

    # Create keyboard
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail_china")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "⭐ Добавить в избранное",
            callback_data=f"add_favorite_{pending_data.get('car_id', '')}",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/bratchikov_y"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Расчёт другого автомобиля",
            callback_data="calculate_another",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Главное меню",
            callback_data="main_menu",
        )
    )

    # Send photos if available
    if photos:
        media_group = []
        for photo_data in photos[:10]:
            try:
                photo_url = photo_data if isinstance(photo_data, str) else photo_data.get("url", "")
                if not photo_url:
                    continue
                resp = requests.get(photo_url, timeout=10)
                if resp.status_code == 200:
                    photo = BytesIO(resp.content)
                    media_group.append(types.InputMediaPhoto(photo))
            except Exception as e:
                print(f"Error loading photo: {e}")

        if media_group:
            bot.send_media_group(message.chat.id, media_group)

    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ==================== END CHINA CALCULATION ====================


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


def get_technical_card():
    global vehicle_id

    url = f"https://api.encar.com/v1/readside/inspection/vehicle/{vehicle_id}"

    print(vehicle_id)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "http://www.encar.com/",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
        }

        response = requests.get(url, headers=headers)
        json_response = response.json() if response.status_code == 200 else None

        if not json_response:
            return "❌ Ошибка: не удалось получить данные. Проверьте ссылку."

        master = json_response.get("master", {}).get("detail", {})
        if not master:
            return "❌ Ошибка: данные о транспортном средстве не найдены."

        vehicle_id = json_response.get("vehicleId", "Не указано")
        model_year = (master.get("modelYear") or "Не указано").strip()
        vin = master.get("vin", "Не указано")
        first_registration_date = master.get("firstRegistrationDate", "Не указано")
        registration_date = master.get("registrationDate", "Не указано")
        mileage = f"{int(master.get('mileage', 0)):,}".replace(",", " ") + " км"

        transmission_data = master.get("transmissionType")
        transmission = (
            transmission_data.get("title") if transmission_data else "Не указано"
        )

        color_data = master.get("colorType")
        color = color_data.get("title") if color_data else "Не указано"

        car_state_data = master.get("carStateType")
        car_state = car_state_data.get("title") if car_state_data else "Не указано"

        motor_type = master.get("motorType", "Не указано")

        accident = "❌ Нет" if not master.get("accdient", False) else "⚠️ Да"
        simple_repair = "❌ Нет" if not master.get("simpleRepair", False) else "⚠️ Да"
        waterlog = "❌ Нет" if not master.get("waterlog", False) else "⚠️ Да"
        tuning = "❌ Нет" if not master.get("tuning", False) else "⚠️ Да"

        # Переводы
        translations = {
            "오토": "Автоматическая",
            "수동": "Механическая",
            "자가보증": "Собственная гарантия",
            "양호": "Хорошее состояние",
            "무채색": "Нейтральный",
            "적정": "В норме",
            "없음": "Нет",
            "누유": "Утечка",
            "불량": "Неисправность",
            "미세누유": "Незначительная утечка",
            "양호": "В хорошем состоянии",
            "주의": "Требует внимания",
            "교환": "Замена",
            "부족": "Недостаточный уровень",
            "정상": "Нормально",
            "작동불량": "Неисправна",
            "소음": "Шум",
            "작동양호": "Работает хорошо",
        }

        def translate(value):
            return translations.get(value, value)

        # Проверка состояния узлов
        inners = json_response.get("inners", [])
        nodes_status = {}

        for inner in inners:
            for child in inner.get("children", []):
                type_code = child.get("type", {}).get("code", "")
                status_type = child.get("statusType")
                status = (
                    translate(status_type.get("title", "Не указано"))
                    if status_type
                    else "Не указано"
                )

                nodes_status[type_code] = status

        output = (
            f"🚗 <b>Основная информация об автомобиле</b>\n"
            f"	•	ID автомобиля: {vehicle_id}\n"
            f"	•	Год выпуска: {model_year}\n"
            f"	•	Дата первой регистрации: {first_registration_date}\n"
            f"	•	Дата регистрации в системе: {registration_date}\n"
            f"	•	VIN: {vin}\n"
            f"	•	Пробег: {mileage}\n"
            f"	•	Тип трансмиссии: {translate(transmission)} ({transmission})\n"
            f"	•	Тип двигателя: {motor_type}\n"
            f"	•	Состояние автомобиля: {translate(car_state)} ({car_state})\n"
            f"	•	Цвет: {translate(color)} ({color})\n"
            f"	•	Тюнинг: {tuning}\n"
            f"	•	Автомобиль попадал в ДТП: {accident}\n"
            f"	•	Были ли простые ремонты: {simple_repair}\n"
            f"	•	Затопление: {waterlog}\n"
            f"\n⸻\n\n"
            f"⚙️ <b>Проверка основных узлов</b>\n"
            f"	•	Двигатель: ✅ {nodes_status.get('s001', 'Не указано')}\n"
            f"	•	Трансмиссия: ✅ {nodes_status.get('s002', 'Не указано')}\n"
            f"	•	Работа двигателя на холостом ходу: ✅ {nodes_status.get('s003', 'Не указано')}\n"
            f"	•	Утечка масла двигателя: {'❌ Нет' if nodes_status.get('s004', '없음') == 'Нет' else '⚠️ Да'} ({nodes_status.get('s004', 'Не указано')})\n"
            f"	•	Уровень масла в двигателе: ✅ {nodes_status.get('s005', 'Не указано')}\n"
            f"	•	Утечка охлаждающей жидкости: {'❌ Нет' if nodes_status.get('s006', '없음') == 'Нет' else '⚠️ Да'} ({nodes_status.get('s006', 'Не указано')})\n"
            f"	•	Уровень охлаждающей жидкости: ✅ {nodes_status.get('s007', 'Не указано')}\n"
            f"	•	Система подачи топлива: ✅ {nodes_status.get('s008', 'Не указано')}\n"
            f"	•	Автоматическая коробка передач: ✅ {nodes_status.get('s009', 'Не указано')}\n"
            f"	•	Утечка масла в АКПП: {'❌ Нет' if nodes_status.get('s010', '없음') == 'Нет' else '⚠️ Да'} ({nodes_status.get('s010', 'Не указано')})\n"
            f"	•	Работа АКПП на холостом ходу: ✅ {nodes_status.get('s011', 'Не указано')}\n"
            f"	•	Система сцепления: ✅ {nodes_status.get('s012', 'Не указано')}\n"
            f"	•	Карданный вал и подшипники: ✅ {nodes_status.get('s013', 'Не указано')}\n"
            f"	•	Редуктор: ✅ {nodes_status.get('s014', 'Не указано')}\n"
        )

        return output

    except requests.RequestException as e:
        return f"❌ Ошибка при получении данных: {e}"


# Вопрос/Ответ
@bot.message_handler(func=lambda msg: msg.text == "Вопрос/Ответ")
def handle_faq(message):
    markup = types.InlineKeyboardMarkup()
    for topic in faq_data:
        markup.add(
            types.InlineKeyboardButton(topic, callback_data=f"faq_topic:{topic}")
        )
    bot.send_message(message.chat.id, "Выберите тему:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "faq_back")
def handle_faq_back(call):
    markup = types.InlineKeyboardMarkup()
    for topic in faq_data.keys():
        markup.add(
            types.InlineKeyboardButton(topic, callback_data=f"faq_topic:{topic}")
        )

    bot.edit_message_text(
        "📚 *Выберите тему из списка:*",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("faq_topic:"))
def handle_faq_topic(call):
    topic = call.data.split(":")[1]
    questions = faq_data.get(topic, [])

    markup = types.InlineKeyboardMarkup()
    for i, q in enumerate(questions):
        markup.add(
            types.InlineKeyboardButton(
                q["question"], callback_data=f"faq_question:{topic}:{i}"
            )
        )

    # Добавляем кнопку "Вернуться к темам"
    markup.add(
        types.InlineKeyboardButton("🔙 Вернуться к темам", callback_data="faq_back")
    )

    bot.edit_message_text(
        f"🔹 *{topic}* — выберите вопрос:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith("faq_question:"))
def handle_faq_question(call):
    _, topic, index = call.data.split(":")
    index = int(index)
    question_data = faq_data[topic][index]

    text = f"❓ *{question_data['question']}*\n\n{question_data['answer']}"
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "🔙 Назад к вопросам", callback_data=f"faq_topic:{topic}"
        )
    )
    bot.send_message(
        call.message.chat.id, text="Выберите действие", reply_markup=markup
    )


# Обработчик отмены ввода HP
@bot.callback_query_handler(func=lambda call: call.data == "cancel_hp_input")
def handle_cancel_hp_input(call):
    """Отменяет ожидание ввода HP от пользователя."""
    user_id = call.from_user.id

    if user_id in pending_hp_input:
        context = pending_hp_input.pop(user_id)
        # Удаляем сообщение "Обрабатываю данные..."
        try:
            bot.delete_message(call.message.chat.id, context.get("processing_message_id"))
        except Exception:
            pass

    bot.answer_callback_query(call.id, "Расчёт отменён")
    bot.send_message(
        call.message.chat.id,
        "❌ Расчёт отменён.\n\nВведите ссылку на другой автомобиль для расчёта.",
        parse_mode="HTML",
    )


# Callback query handler
@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    global car_data, car_id_external, usd_rate

    # ---- Fuel type selection (China flow) ----
    if call.data.startswith("fuel_"):
        user_id = call.message.chat.id
        fuel_type = int(call.data.split("_")[1])
        fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type, "Бензин")

        if user_id in pending_china_hp_requests and "hp" in pending_china_hp_requests[user_id]:
            pending_china_hp_requests[user_id]["fuel_type"] = fuel_type
            bot.answer_callback_query(call.id, f"Выбран тип: {fuel_type_name}")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            complete_china_calculation(user_id, call.message)
        else:
            bot.answer_callback_query(call.id, "Ошибка: данные не найдены")
        return

    # ---- Manual calculation country selection ----
    elif call.data == "manual_country_korea":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        # Show age selection for Korea
        keyboard = types.ReplyKeyboardMarkup(
            resize_keyboard=True, one_time_keyboard=True
        )
        keyboard.add("До 3 лет", "От 3 до 5 лет")
        keyboard.add("От 5 до 7 лет", "Более 7 лет")
        keyboard.add("Главное меню")
        msg = bot.send_message(
            call.message.chat.id,
            "🇰🇷 Выберите возраст автомобиля:",
            reply_markup=keyboard,
        )
        bot.register_next_step_handler(msg, process_car_age)
        return

    elif call.data == "manual_country_china":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        # Show age selection for China
        keyboard = types.ReplyKeyboardMarkup(
            resize_keyboard=True, one_time_keyboard=True
        )
        keyboard.add("До 3 лет", "От 3 до 5 лет")
        keyboard.add("От 5 до 7 лет", "Более 7 лет")
        keyboard.add("Главное меню")
        msg = bot.send_message(
            call.message.chat.id,
            "🇨🇳 Выберите возраст автомобиля:",
            reply_markup=keyboard,
        )
        bot.register_next_step_handler(msg, process_china_car_age)
        return

    # ---- China manual fuel type selection ----
    elif call.data.startswith("china_manual_fuel_"):
        fuel_type = int(call.data.replace("china_manual_fuel_", ""))
        fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type, "Бензин")
        user_id = call.from_user.id

        if user_id in user_data and "country" in user_data[user_id] and user_data[user_id]["country"] == "china":
            user_data[user_id]["fuel_type"] = fuel_type
            bot.answer_callback_query(call.id, f"Выбран тип: {fuel_type_name}")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            # Ask for price in CNY
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(types.KeyboardButton("Главное меню"))
            msg = bot.send_message(
                call.message.chat.id,
                "Введите стоимость автомобиля в юанях (例如: 150000):",
                reply_markup=markup,
            )
            bot.register_next_step_handler(msg, process_china_car_price)
        else:
            bot.answer_callback_query(call.id, "Ошибка: данные не найдены")
        return

    # ---- Detail view for China Manual ----
    elif call.data == "detail_china_manual":
        print_message("[ЗАПРОС] ДЕТАЛИЗАЦИЯ РАСЧËТА (КИТАЙ РУЧНОЙ)")

        detail_message = (
            f"<i>ПЕРВАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Задаток (бронь авто + отчёт эксперта):\n<b>¥{format_number(car_data['first_payment_cny'])}</b> | <b>{format_number(int(car_data['first_payment_rub']))} ₽</b>\n\n\n"
            f"<i>ВТОРАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Стоимость авто (минус задаток):\n<b>¥{format_number(car_data['car_price_cny'])}</b> | <b>{format_number(int(car_data['car_price_rub']))} ₽</b>\n\n"
            f"Расходы по Китаю (дилерский сбор, доставка, оформление):\n<b>¥{format_number(car_data['china_expenses_cny'])}</b> | <b>{format_number(int(car_data['china_expenses_rub']))} ₽</b>\n\n"
            f"<b>Итого расходов по Китаю</b>:\n<b>¥{format_number(car_data['china_total_cny'])}</b> | <b>{format_number(int(car_data['china_total_rub']))} ₽</b>\n\n\n"
            f"<i>РАСХОДЫ РОССИЯ</i>:\n\n"
            f"Единая таможенная ставка:\n<b>{format_number(int(car_data['customs_duty_rub']))} ₽</b>\n\n"
            f"Таможенное оформление:\n<b>{format_number(int(car_data['customs_fee_rub']))} ₽</b>\n\n"
            f"Утилизационный сбор:\n<b>{format_number(int(car_data['util_fee_rub']))} ₽</b>\n\n"
            f"Агентские услуги:\n<b>{format_number(car_data['agent_russia_rub'])} ₽</b>\n\n"
            f"Брокер:\n<b>{format_number(car_data['broker_russia_rub'])} ₽</b>\n\n"
            f"СВХ:\n<b>{format_number(car_data['svh_russia_rub'])} ₽</b>\n\n"
            f"Лаборатория, СБКТС, ЭПТС:\n<b>{format_number(car_data['lab_russia_rub'])} ₽</b>\n\n"
            f"Комиссия:\n<b>{format_number(car_data['yuri_fee_rub'])} ₽</b>\n\n"
            f"<b>Итого под ключ</b>:\n<b>¥{format_number(int(car_data['total_cost_cny']))}</b> | <b>{format_number(int(car_data['total_cost_rub']))} ₽</b>\n\n"
            f"<b>Доставку до вашего города уточняйте у меня:</b>\n"
            f"▪️ @bratchikov_y (Юрий)\n"
        )

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another_manual",
            )
        )
        keyboard.add(
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )

        bot.send_message(
            call.message.chat.id,
            detail_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # ---- Detail view for China (Che168) ----
    elif call.data.startswith("detail_china"):
        print_message("[ЗАПРОС] ДЕТАЛИЗАЦИЯ РАСЧËТА (КИТАЙ)")

        detail_message = (
            f"<i>ПЕРВАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Задаток (бронь авто + отчёт эксперта):\n<b>¥{format_number(car_data['first_payment_cny'])}</b> | <b>{format_number(int(car_data['first_payment_rub']))} ₽</b>\n\n\n"
            f"<i>ВТОРАЯ ЧАСТЬ ОПЛАТЫ</i>:\n\n"
            f"Стоимость авто (минус задаток):\n<b>¥{format_number(car_data['car_price_cny'])}</b> | <b>{format_number(int(car_data['car_price_rub']))} ₽</b>\n\n"
            f"Расходы по Китаю (дилерский сбор, доставка, оформление):\n<b>¥{format_number(car_data['china_expenses_cny'])}</b> | <b>{format_number(int(car_data['china_expenses_rub']))} ₽</b>\n\n"
            f"<b>Итого расходов по Китаю</b>:\n<b>¥{format_number(car_data['china_total_cny'])}</b> | <b>{format_number(int(car_data['china_total_rub']))} ₽</b>\n\n\n"
            f"<i>РАСХОДЫ РОССИЯ</i>:\n\n"
            f"Единая таможенная ставка:\n<b>{format_number(int(car_data['customs_duty_rub']))} ₽</b>\n\n"
            f"Таможенное оформление:\n<b>{format_number(int(car_data['customs_fee_rub']))} ₽</b>\n\n"
            f"Утилизационный сбор:\n<b>{format_number(int(car_data['util_fee_rub']))} ₽</b>\n\n"
            f"Агентские услуги:\n<b>{format_number(car_data['agent_russia_rub'])} ₽</b>\n\n"
            f"Брокер:\n<b>{format_number(car_data['broker_russia_rub'])} ₽</b>\n\n"
            f"СВХ:\n<b>{format_number(car_data['svh_russia_rub'])} ₽</b>\n\n"
            f"Лаборатория, СБКТС, ЭПТС:\n<b>{format_number(car_data['lab_russia_rub'])} ₽</b>\n\n"
            f"Комиссия:\n<b>{format_number(car_data['yuri_fee_rub'])} ₽</b>\n\n"
            f"<b>Итого под ключ</b>:\n<b>¥{format_number(int(car_data['total_cost_cny']))}</b> | <b>{format_number(int(car_data['total_cost_rub']))} ₽</b>\n\n"
            f"<b>Доставку до вашего города уточняйте у меня:</b>\n"
            f"▪️ @bratchikov_y (Юрий)\n"
        )

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another",
            )
        )
        keyboard.add(
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )

        bot.send_message(
            call.message.chat.id,
            detail_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    # ---- Detail view for Korea (existing) ----
    elif call.data.startswith("detail"):
        print_message("[ЗАПРОС] ДЕТАЛИЗАЦИЯ РАСЧËТА")

        # <b>${format_number(car_data['car_price_usd'])}</b> |
        # <b>${format_number(car_data['parking_korea_usd'])}</b> |
        # <b>${format_number(car_data['car_review_usd'])}</b> |
        # <b>${format_number(car_data['korea_documents_usd'])}</b> |
        # <b>${format_number(car_data['transfer_korea_usd'])}</b> |
        # <b>${format_number(car_data['freight_korea_usd'])}</b> |
        # <b>${format_number(car_data['customs_duty_usd'])}</b> |
        # <b>${format_number(car_data['customs_fee_usd'])}</b> |
        # <b>${format_number(car_data['util_fee_usd'])}</b> |
        # <b>${format_number(car_data['broker_usd'])}</b> |
        # <b>${format_number(car_data['perm_registration_usd'])}</b> |
        # <b>${format_number(car_data['svh_usd'])}</b> |
        # <b>${format_number(car_data['lab_usd'])}</b> |
        # <b>${format_number(car_data['konosament_usd'])}</b> |
        # <b>${format_number(car_data['expertise_usd'])}</b> |
        # <b>${format_number(car_data['svh_transfer_usd'])}</b> |
        # <b>${format_number(car_data['consultant_fee_usd'])}</b> |
        # <b>${format_number(car_data['total_cost_usd'])}</b> |

        detail_message = (
            f"Стоимость автомобиля:\n<b>₩{format_number(car_data['car_price_krw'])}</b> | <b>{format_number(car_data['car_price_rub'])} ₽</b>\n\n"
            f"Стояночные:\n<b>₩{format_number(car_data['parking_korea_krw'])}</b> | <b>{format_number(car_data['parking_korea_rub'])} ₽</b>\n\n"
            f"Осмотр:\n<b>₩{format_number(car_data['car_review_krw'])}</b> | <b>{format_number(car_data['car_review_rub'])} ₽</b>\n\n"
            f"Документы:\n<b>₩{format_number(car_data['korea_documents_krw'])}</b> | <b>{format_number(car_data['korea_documents_rub'])} ₽</b>\n\n"
            f"Перевозка:\n<b>₩{format_number(car_data['transfer_korea_krw'])}</b> | <b>{format_number(car_data['transfer_korea_rub'])} ₽</b>\n\n"
            f"Фрахт:\n<b>₩{format_number(car_data['freight_korea_krw'])}</b> | <b>{format_number(car_data['freight_korea_rub'])} ₽</b>\n\n\n"
            f"Единая таможенная ставка:\n<b>₩{format_number(car_data['customs_duty_krw'])}</b> | <b>{format_number(car_data['customs_duty_rub'])} ₽</b>\n\n"
            f"Таможенное оформление:\n<b>₩{format_number(car_data['customs_fee_krw'])}</b> | <b>{format_number(car_data['customs_fee_rub'])} ₽</b>\n\n"
            f"Утилизационный сбор:\n<b>₩{format_number(car_data['util_fee_krw'])}</b> | <b>{format_number(car_data['util_fee_rub'])} ₽</b>\n\n\n"
            f"Брокер:\n<b>₩{format_number(car_data['broker_krw'])}</b> | <b>{format_number(car_data['broker_rub'])} ₽</b>\n\n"
            f"Временная регистрация:\n<b>₩{format_number(car_data['perm_registration_krw'])}</b> | <b>{format_number(car_data['perm_registration_rub'])} ₽</b>\n\n"
            f"СВХ (Склад временного хранения):\n<b>₩{format_number(car_data['svh_krw'])}</b> | <b>{format_number(car_data['svh_rub'])} ₽</b>\n\n"
            f"Лаборатория:\n<b>₩{format_number(car_data['lab_krw'])}</b> | <b>{format_number(car_data['lab_rub'])} ₽</b>\n\n"
            f"Коносамент:\n<b>₩{format_number(car_data['konosament_krw'])}</b> | <b>{format_number(car_data['konosament_rub'])} ₽</b>\n\n"
            f"Экспертиза:\n<b>₩{format_number(car_data['expertise_krw'])}</b> | <b>{format_number(car_data['expertise_rub'])} ₽</b>\n\n"
            f"Перегон из СВХ/Лаборатория/Стоянка:\n<b>₩{format_number(car_data['svh_transfer_krw'])}</b> | <b>{format_number(car_data['svh_transfer_rub'])} ₽</b>\n\n"
            f"Услуги консультанта:\n<b>₩{format_number(car_data['consultant_fee_krw'])}</b> | <b>{format_number(car_data['consultant_fee_rub'])} ₽</b>\n\n"
            f"Итого под ключ: \n<b>₩{format_number(car_data['total_cost_krw'])}</b> | <b>{format_number(car_data['total_cost_rub'])} ₽</b>\n\n"
            f"<b>Доставку до вашего города уточняйте у меня:</b>\n"
            f"▪️ @bratchikov_y (Юрий)\n"
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
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )

        bot.send_message(
            call.message.chat.id,
            detail_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    elif call.data == "technical_card":
        print_message("[ЗАПРОС] ТЕХНИЧЕСКАЯ ОТЧËТ ОБ АВТОМОБИЛЕ")

        technical_card_output = get_technical_card()

        bot.send_message(
            call.message.chat.id,
            "Запрашиваю отчёт по автомобилю. Пожалуйста подождите ⏳",
        )

        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(
            types.InlineKeyboardButton(
                "Рассчитать стоимость другого автомобиля",
                callback_data="calculate_another",
            )
        )
        keyboard.add(
            types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
        )
        bot.send_message(
            call.message.chat.id,
            technical_card_output,
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
                    "Связаться с менеджером", url="https://t.me/bratchikov_y"
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
                    "Связаться с менеджером", url="https://t.me/bratchikov_y"
                )
            )
            keyboard.add(
                types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
            )

            bot.send_message(
                call.message.chat.id,
                tech_report_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

    elif call.data == "user_type_physical":
        user_type_map[call.message.chat.id] = 1
        bot.send_message(
            call.message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта (encar.com, kbchachacha.com, kcar.com, che168.com)",
        )

    elif call.data == "user_type_legal":
        user_type_map[call.message.chat.id] = 2
        bot.send_message(
            call.message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта (encar.com, kbchachacha.com, kcar.com, che168.com)",
        )

    elif call.data == "calculate_another":
        markup_type_keyboard = types.InlineKeyboardMarkup(row_width=2)
        markup_type_keyboard.add(
            types.InlineKeyboardButton("Физ лицо", callback_data="user_type_physical"),
            types.InlineKeyboardButton("Юр лицо", callback_data="user_type_legal"),
        )
        bot.send_message(
            call.message.chat.id,
            "Выберите тип расчёта",
            reply_markup=markup_type_keyboard,
        )

    elif call.data == "calculate_another_manual":
        # Show country selection inline keyboard
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("🇰🇷 Корея", callback_data="manual_country_korea"),
            types.InlineKeyboardButton("🇨🇳 Китай", callback_data="manual_country_china"),
        )
        bot.send_message(
            call.message.chat.id,
            "Выберите страну для расчёта:",
            reply_markup=keyboard,
        )

    elif call.data == "main_menu":
        bot.send_message(call.message.chat.id, "Главное меню", reply_markup=main_menu())

    elif call.data == "show_faq":
        show_faq(call.message)


def process_car_age(message):
    user_input = message.text.strip()

    # Проверяем ввод
    age_mapping = {
        "До 3 лет": "0-3",
        "От 3 до 5 лет": "3-5",
        "От 5 до 7 лет": "5-7",
        "Более 7 лет": "7-0",
    }

    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        return

    elif user_input not in age_mapping:
        bot.send_message(message.chat.id, "Пожалуйста, выберите возраст из списка.")
        return

    # Сохраняем возраст авто
    user_data[message.chat.id] = {"car_age": age_mapping[user_input]}

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Главное меню"))

    # Запрашиваем объем двигателя
    bot.send_message(
        message.chat.id,
        "Введите объем двигателя в см³ (например, 1998):",
        reply_markup=markup,
    )
    bot.register_next_step_handler(message, process_engine_volume)


def process_engine_volume(message):
    user_input = message.text.strip()

    # Проверяем, что введено число
    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        return
    elif not user_input.isdigit():
        bot.send_message(
            message.chat.id, "Пожалуйста, введите корректный объем двигателя в см³."
        )
        bot.register_next_step_handler(message, process_engine_volume)
        return

    # Сохраняем объем двигателя
    user_data[message.chat.id]["engine_volume"] = int(user_input)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Главное меню"))

    # Запрашиваем мощность двигателя
    bot.send_message(
        message.chat.id,
        "Введите мощность двигателя в л.с. (например: 159):",
        reply_markup=markup,
    )
    bot.register_next_step_handler(message, process_hp)


def process_hp(message):
    """Обрабатывает ввод мощности двигателя для ручного расчёта."""
    user_input = message.text.strip()

    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        return

    # Валидация ввода HP
    try:
        hp = int(user_input)
        if not (50 <= hp <= 1500):
            raise ValueError("HP out of range")
    except ValueError:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную мощность в л.с. (число от 50 до 1500).",
        )
        bot.register_next_step_handler(message, process_hp)
        return

    # Сохраняем мощность
    user_data[message.chat.id]["hp"] = hp

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Главное меню"))

    # Запрашиваем стоимость авто
    bot.send_message(
        message.chat.id,
        "Введите стоимость автомобиля в корейских вонах (например, 15000000):",
        reply_markup=markup,
    )
    bot.register_next_step_handler(message, process_car_price)


def process_car_price(message):
    global usd_to_krw_rate, usd_to_rub_rate

    # Получаем актуальный курс валют, но убираем перезагрузку rub_to_krw_rate
    user_input = message.text.strip()

    # Проверяем, что введено число
    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        return
    elif not user_input.isdigit():
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную стоимость автомобиля в вонах.",
        )
        bot.register_next_step_handler(message, process_car_price)
        return

    # Сохраняем стоимость автомобиля
    user_data[message.chat.id]["car_price_krw"] = int(user_input)

    # Извлекаем данные пользователя
    if message.chat.id not in user_data:
        user_data[message.chat.id] = {}

    if "car_age" not in user_data[message.chat.id]:
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте снова.")
        return  # Прерываем выполнение, если возраст не установлен

    age_group = user_data[message.chat.id]["car_age"]
    engine_volume = user_data[message.chat.id]["engine_volume"]
    car_price_krw = user_data[message.chat.id]["car_price_krw"]
    hp = user_data[message.chat.id].get("hp", 1)

    # Конвертируем стоимость авто в рубли
    price_krw = car_price_krw
    price_rub = price_krw * get_actual_rub_to_krw_rate()
    # price_usd = price_krw / usd_to_krw_rate

    response = get_customs_fees_manual(
        engine_volume,
        price_krw,
        age_group,
        engine_type=1,
        power=hp,
    )

    # Таможенный сбор
    customs_fee = clean_number(response["sbor"])
    customs_duty = clean_number(response["tax"])
    recycling_fee = clean_number(response["util"])

    # Расчет итоговой стоимости автомобиля в рублях
    total_cost = (
        price_rub  # Цена авто в рублях
        + 2000000 * get_actual_rub_to_krw_rate()  # Расходы по Корее
        + customs_fee  # Таможенный сбор
        + customs_duty  # Таможенная пошлина
        + recycling_fee  # Утильсбор
        + 30000  # Брокер РФ
        + 15000  # Временная регистрация
        + 45000  # СВХ
        + 25000  # Лаборатория
        + 2000  # Коносамент
        + 2000  # Экспертиза
        + 8000  # Перегон из СВХ
        + (
            20000 if engine_volume > 2000 else 0
        )  # За санкционную добавляется «услуга консультанта - 20.000
    )

    total_cost_krw = (
        price_krw  # Цена авто в вонах
        + 2000000  # Расходы по Корее
        + customs_fee / get_actual_rub_to_krw_rate()  # Таможенный сбор
        + customs_duty / get_actual_rub_to_krw_rate()  # Таможенная пошлина
        + recycling_fee / get_actual_rub_to_krw_rate()  # Утильсбор
        + 15000 / get_actual_rub_to_krw_rate()  # Брокер РФ
        + 30000 / get_actual_rub_to_krw_rate()  # Временная регистрация
        + 45000 / get_actual_rub_to_krw_rate()  # СВХ
        + 25000 / get_actual_rub_to_krw_rate()  # Лаборатория
        + 2000 / get_actual_rub_to_krw_rate()  # Коносамент
        + 2000 / get_actual_rub_to_krw_rate()  # Экспертиза
        + 8000 / get_actual_rub_to_krw_rate()  # Перегон из СВХ
        + (
            20000 / get_actual_rub_to_krw_rate() if engine_volume > 2000 else 0
        )  # За санкционную добавляется «услуга консультанта"
    )

    # total_cost_usd = (
    #     price_usd  # Цена авто в долларах
    #     + ((2000000 / usd_to_krw_rate))  # Расходы по Корее
    #     + (customs_fee / usd_to_rub_rate)  # Таможенный сбор
    #     + (customs_duty / usd_to_rub_rate)  # Таможенная пошлина
    #     + (recycling_fee / usd_to_rub_rate)  # Утильсбор
    #     + (30000 / usd_to_rub_rate)  # Брокер РФ
    #     + (15000 / usd_to_rub_rate)  # Временная регистрация
    #     + (45000 / usd_to_rub_rate)  # СВХ
    #     + (25000 / usd_to_rub_rate)  # Лаборатория
    #     + (2000 / usd_to_rub_rate)  # Коносамент
    #     + (2000 / usd_to_rub_rate)  # Экспертиза
    #     + (8000 / usd_to_rub_rate)  # Перегон из СВХ
    #     + (20000 / usd_to_rub_rate)
    #     # За санкционную добавляется «услуга консультанта - 20
    # )

    # car_data["total_cost_usd"] = total_cost_usd
    car_data["total_cost_krw"] = total_cost_krw
    car_data["total_cost_rub"] = total_cost

    # Стоимость автомобиля
    car_data["car_price_krw"] = price_krw
    # car_data["car_price_usd"] = price_usd
    car_data["car_price_rub"] = price_rub

    # Стояночные
    car_data["parking_korea_krw"] = 440000
    car_data["parking_korea_rub"] = 440000 * get_actual_rub_to_krw_rate()
    # car_data["parking_korea_usd"] = 440000 / usd_to_krw_rate

    # Осмотр
    car_data["car_review_krw"] = 300000
    car_data["car_review_rub"] = 300000 * get_actual_rub_to_krw_rate()
    # car_data["car_review_usd"] = 300000 / usd_to_krw_rate

    # Документы
    car_data["korea_documents_krw"] = 150000
    car_data["korea_documents_rub"] = 150000 * get_actual_rub_to_krw_rate()
    # car_data["korea_documents_usd"] = 150000 / usd_to_krw_rate

    # Перевозка
    car_data["transfer_korea_krw"] = 230000
    car_data["transfer_korea_rub"] = 230000 * get_actual_rub_to_krw_rate()
    # car_data["transfer_korea_usd"] = 230000 / usd_to_krw_rate

    # Фрахт
    car_data["freight_korea_krw"] = 880000
    car_data["freight_korea_rub"] = 880000 * get_actual_rub_to_krw_rate()
    # car_data["freight_korea_usd"] = 880000 / usd_to_krw_rate

    # Расходы по РФ
    car_data["customs_duty_rub"] = customs_duty
    car_data["customs_duty_krw"] = customs_duty / get_actual_rub_to_krw_rate()
    # car_data["customs_duty_usd"] = customs_duty / usd_to_rub_rate

    car_data["customs_fee_rub"] = customs_fee
    car_data["customs_fee_krw"] = customs_fee / get_actual_rub_to_krw_rate()
    # car_data["customs_fee_usd"] = customs_fee / usd_to_rub_rate

    car_data["util_fee_rub"] = recycling_fee
    car_data["util_fee_krw"] = recycling_fee / get_actual_rub_to_krw_rate()
    # car_data["util_fee_usd"] = recycling_fee / usd_to_rub_rate

    car_data["perm_registration_rub"] = 15000
    car_data["perm_registration_krw"] = 15000 / get_actual_rub_to_krw_rate()
    # car_data["perm_registration_usd"] = 15000 / usd_to_rub_rate

    car_data["broker_rub"] = 30000
    car_data["broker_krw"] = 30000 / get_actual_rub_to_krw_rate()
    # car_data["broker_usd"] = 30000 / usd_to_rub_rate

    car_data["svh_rub"] = 45000
    car_data["svh_krw"] = 45000 / get_actual_rub_to_krw_rate()
    # car_data["svh_usd"] = 45000 / usd_to_rub_rate

    car_data["lab_rub"] = 25000
    car_data["lab_krw"] = 25000 / get_actual_rub_to_krw_rate()
    # car_data["lab_usd"] = 25000 / usd_to_rub_rate

    car_data["konosament_rub"] = 2000
    car_data["konosament_krw"] = 2000 / get_actual_rub_to_krw_rate()
    # car_data["konosament_usd"] = 2000 / usd_to_rub_rate

    car_data["expertise_rub"] = 2000
    car_data["expertise_krw"] = 2000 / get_actual_rub_to_krw_rate()
    # car_data["expertise_usd"] = 2000 / usd_to_rub_rate

    car_data["svh_transfer_rub"] = 8000
    car_data["svh_transfer_krw"] = 8000 / get_actual_rub_to_krw_rate()
    # car_data["svh_transfer_usd"] = 8000 / usd_to_rub_rate

    car_data["consultant_fee_rub"] = 20000 if engine_volume > 2000 else 0
    car_data["consultant_fee_krw"] = (
        20000 / get_actual_rub_to_krw_rate() if engine_volume > 2000 else 0
    )

    car_data["yuri_fee_rub"] = 120000
    car_data["yuri_fee_krw"] = 120000 / get_actual_rub_to_krw_rate()
    # car_data["yuri_fee_usd"] = 120000 / usd_to_rub_rate

    # car_data["consultant_fee_usd"] = (
    #     20000 / usd_to_rub_rate if car_engine_displacement > 2000 else 0
    # )

    # Формирование сообщения результата
    # <b>${format_number(total_cost_usd)}</b> |
    # f"Стоимость автомобиля в Корее: ₩{format_number(price_krw)}\n"
    # f"Стоимость автомобиля под ключ до Владивостока:\n<b>₩{format_number(total_cost_krw)}</b> | <b>{format_number(total_cost)} ₽</b>\n\n"

    result_message = (
        f"🗓 Возраст: {age_group}\n"
        f"🔧 Объём двигателя: {engine_volume} cc\n"
        f"🐴 Мощность: {hp} л.с.\n"
        f"💵 <b>Курс Воны к Рублю: {get_actual_rub_to_krw_rate():.4f} ₽</b>\n\n"
        f"🇰🇷 Платежи в Корее\n"
        f"▪️ Стоимость автомобиля: <b>₩{format_number(car_data['car_price_krw'])}</b> | <b>{format_number(car_data['car_price_rub'])} ₽</b>\n"
        f"▪️ Расходы по Корее (Фрахт, Стояночные, Логистика, Осмотр, Экспортные документы): <b>₩{format_number(2000000)}</b> | <b>{format_number(2000000 * get_actual_rub_to_krw_rate())} ₽</b>\n\n\n"
        f"🇷🇺 Платежи в России\n"
        f"▪️ <b>Единая таможенная ставка</b>: <b>{format_number(car_data['customs_duty_rub'])} ₽</b>\n"
        f"▪️ <b>Таможенное оформление</b>: <b>{format_number(car_data['customs_fee_rub'])} ₽</b>\n"
        f"▪️ <b>Утилизационный сбор</b>: <b>{format_number(car_data['util_fee_rub'])} ₽</b>\n\n"
        f"▪️ Брокер: <b>{format_number(car_data['broker_rub'])} ₽</b>\n"
        f"▪️ Временная регистрация: <b>{format_number(car_data['perm_registration_rub'])} ₽</b>\n"
        f"▪️ СВХ: <b>{format_number(car_data['svh_rub'])} ₽</b>\n"
        f"▪️ Лаборатория: <b>{format_number(car_data['lab_rub'])} ₽</b>\n"
        f"▪️ Коносамент: <b>{format_number(car_data['konosament_rub'])} ₽</b>\n"
        f"▪️ Экспертиза: <b>{format_number(car_data['expertise_rub'])} ₽</b>\n"
        f"▪️ Перегон из СВХ: <b>{format_number(car_data['svh_transfer_rub'])} ₽</b>\n"
        f"▪️ Услуги консультанта: <b>{format_number(car_data['consultant_fee_rub'])} ₽</b>\n"
        f"▪️ Моя комиссия: <b>{format_number(car_data['yuri_fee_rub'])} ₽</b>\n\n"
        f"🟰 Итого под ключ до Владивостока: <b>{format_number(car_data['total_cost_rub'])} ₽</b>\n\n"
        "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у меня:\n"
        f"▪️ @bratchikov_y (Юрий)\n\n"
        "🔗 <a href='https://t.me/bratchikov_cars'>Официальный телеграм канал</a>\n"
    )

    # Клавиатура с дальнейшими действиями
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "Детали расчёта", callback_data="detail_manual"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Связаться с менеджером", url="https://t.me/bratchikov_y"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Рассчитать другой автомобиль", callback_data="calculate_another_manual"
        )
    )
    keyboard.add(types.InlineKeyboardButton("Главное меню", callback_data="main_menu"))

    # Отправляем сообщение пользователю
    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Очищаем данные пользователя после расчета
    del user_data[message.chat.id]


# ==================== CHINA MANUAL CALCULATION FLOW ====================


def process_china_car_age(message):
    """Обрабатывает выбор возраста автомобиля для ручного расчёта (Китай)."""
    user_input = message.text.strip()

    age_mapping = {
        "До 3 лет": "0-3",
        "От 3 до 5 лет": "3-5",
        "От 5 до 7 лет": "5-7",
        "Более 7 лет": "7-0",
    }

    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        return

    elif user_input not in age_mapping:
        bot.send_message(message.chat.id, "Пожалуйста, выберите возраст из списка.")
        return

    # Сохраняем возраст и страну
    user_data[message.chat.id] = {"car_age": age_mapping[user_input], "country": "china"}

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Главное меню"))

    # Запрашиваем объем двигателя
    bot.send_message(
        message.chat.id,
        "Введите объем двигателя в см³ (例如: 1998):",
        reply_markup=markup,
    )
    bot.register_next_step_handler(message, process_china_engine_volume)


def process_china_engine_volume(message):
    """Обрабатывает ввод объёма двигателя для ручного расчёта (Китай)."""
    user_input = message.text.strip()

    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        if message.chat.id in user_data:
            del user_data[message.chat.id]
        return

    elif not user_input.isdigit():
        bot.send_message(
            message.chat.id, "Пожалуйста, введите корректный объем двигателя в см³."
        )
        bot.register_next_step_handler(message, process_china_engine_volume)
        return

    # Сохраняем объем двигателя
    user_data[message.chat.id]["engine_volume"] = int(user_input)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Главное меню"))

    # Запрашиваем мощность двигателя
    bot.send_message(
        message.chat.id,
        "Введите мощность двигателя в л.с. (例如: 159):",
        reply_markup=markup,
    )
    bot.register_next_step_handler(message, process_china_hp)


def process_china_hp(message):
    """Обрабатывает ввод мощности двигателя для ручного расчёта (Китай)."""
    user_input = message.text.strip()

    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        if message.chat.id in user_data:
            del user_data[message.chat.id]
        return

    # Валидация ввода HP
    try:
        hp = int(user_input)
        if not (50 <= hp <= 1500):
            raise ValueError("HP out of range")
    except ValueError:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную мощность в л.с. (число от 50 до 1500).",
        )
        bot.register_next_step_handler(message, process_china_hp)
        return

    # Сохраняем мощность
    user_data[message.chat.id]["hp"] = hp

    # Show fuel type inline keyboard
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("Бензин", callback_data="china_manual_fuel_1"),
        types.InlineKeyboardButton("Дизель", callback_data="china_manual_fuel_2"),
    )
    keyboard.add(
        types.InlineKeyboardButton("Электро", callback_data="china_manual_fuel_4"),
    )
    keyboard.add(
        types.InlineKeyboardButton("Гибрид (посл.)", callback_data="china_manual_fuel_5"),
        types.InlineKeyboardButton("Гибрид (парал.)", callback_data="china_manual_fuel_6"),
    )

    bot.send_message(
        message.chat.id,
        "Выберите тип двигателя:",
        reply_markup=keyboard,
    )


def process_china_car_price(message):
    """Обрабатывает ввод стоимости автомобиля для ручного расчёта (Китай)."""
    global car_data, cny_rub_rate

    user_input = message.text.strip()

    if user_input == "Главное меню":
        bot.send_message(message.chat.id, "Главное меню", reply_markup=main_menu())
        if message.chat.id in user_data:
            del user_data[message.chat.id]
        return

    elif not user_input.isdigit():
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите корректную стоимость автомобиля в юанях.",
        )
        bot.register_next_step_handler(message, process_china_car_price)
        return

    # Check user data exists
    if message.chat.id not in user_data:
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте снова.")
        return

    if "car_age" not in user_data[message.chat.id]:
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте снова.")
        return

    # Get CNY rate
    current_cny_rate = get_vtb_cnyrub_rate()
    if current_cny_rate is None:
        bot.send_message(message.chat.id, "Ошибка при получении курса юаня. Попробуйте позже.")
        return

    cny_rub_rate = current_cny_rate

    # Extract user data
    price_cny = int(user_input)
    age_group = user_data[message.chat.id]["car_age"]
    engine_volume = user_data[message.chat.id]["engine_volume"]
    hp = user_data[message.chat.id].get("hp", 1)
    fuel_type_code = user_data[message.chat.id].get("fuel_type", 1)
    fuel_type_name = FUEL_TYPE_NAMES.get(fuel_type_code, "Бензин")

    # Call calcus.ru API with CNY currency
    response = get_customs_fees_manual(
        engine_volume,
        price_cny,
        age_group,
        engine_type=fuel_type_code,
        power=hp,
        currency="CNY",
    )

    if not response:
        bot.send_message(message.chat.id, "Ошибка при расчёте таможенных платежей. Попробуйте снова.")
        return

    # Extract customs values
    customs_fee = clean_number(response["sbor"])
    customs_duty = clean_number(response["tax"])
    recycling_fee = clean_number(response["util"])

    # Calculate costs using China constants
    first_payment_rub = CHINA_FIRST_PAYMENT * cny_rub_rate
    car_price_after_deposit = price_cny - CHINA_FIRST_PAYMENT
    china_expenses_rub = CHINA_EXPENSES * cny_rub_rate

    china_total_cny = car_price_after_deposit + CHINA_EXPENSES
    china_total_rub = china_total_cny * cny_rub_rate

    russia_expenses_rub = (
        customs_duty + customs_fee + recycling_fee +
        CHINA_AGENT_FEE + CHINA_BROKER_FEE + CHINA_SVH_FEE + CHINA_LAB_FEE
    )

    total_cost_rub = first_payment_rub + china_total_rub + russia_expenses_rub + CHINA_YURI_FEE
    total_cost_cny = total_cost_rub / cny_rub_rate

    # Format age for display
    age_formatted = (
        "до 3 лет" if age_group == "0-3"
        else ("от 3 до 5 лет" if age_group == "3-5"
        else "от 5 до 7 лет" if age_group == "5-7" else "от 7 лет")
    )

    # Store car_data for detail view
    car_data["source"] = "china_manual"
    car_data["first_payment_cny"] = CHINA_FIRST_PAYMENT
    car_data["first_payment_rub"] = first_payment_rub
    car_data["car_price_cny"] = car_price_after_deposit
    car_data["car_price_rub"] = car_price_after_deposit * cny_rub_rate
    car_data["china_expenses_cny"] = CHINA_EXPENSES
    car_data["china_expenses_rub"] = china_expenses_rub
    car_data["china_total_cny"] = china_total_cny
    car_data["china_total_rub"] = china_total_rub
    car_data["customs_duty_rub"] = customs_duty
    car_data["customs_fee_rub"] = customs_fee
    car_data["util_fee_rub"] = recycling_fee
    car_data["agent_russia_rub"] = CHINA_AGENT_FEE
    car_data["broker_russia_rub"] = CHINA_BROKER_FEE
    car_data["svh_russia_rub"] = CHINA_SVH_FEE
    car_data["lab_russia_rub"] = CHINA_LAB_FEE
    car_data["yuri_fee_rub"] = CHINA_YURI_FEE
    car_data["total_cost_rub"] = total_cost_rub
    car_data["total_cost_cny"] = total_cost_cny

    # Format result message
    result_message = (
        f"🗓 Возраст: {age_formatted}\n"
        f"🔧 Объём двигателя: {format_number(engine_volume)} cc\n"
        f"🐎 Мощность: {hp} л.с.\n"
        f"⛽ Тип двигателя: {fuel_type_name}\n\n"
        f"💵 <b>Курс Юаня к Рублю: {cny_rub_rate:.2f} ₽</b>\n\n"
        f"🇨🇳 Платежи в Китае\n"
        f"▪️ Стоимость автомобиля: <b>¥{format_number(price_cny)}</b> | <b>{format_number(int(price_cny * cny_rub_rate))} ₽</b>\n"
        f"▪️ Расходы по Китаю (дилерский сбор, доставка, оформление): <b>¥{format_number(CHINA_EXPENSES)}</b> | <b>{format_number(int(CHINA_EXPENSES * cny_rub_rate))} ₽</b>\n\n\n"
        f"🇷🇺 Платежи в России\n"
        f"▪️ <b>Единая таможенная ставка</b>: <b>{format_number(customs_duty)} ₽</b>\n"
        f"▪️ <b>Таможенное оформление</b>: <b>{format_number(customs_fee)} ₽</b>\n"
        f"▪️ <b>Утилизационный сбор</b>: <b>{format_number(recycling_fee)} ₽</b>\n\n"
        f"▪️ Агентские услуги: <b>{format_number(CHINA_AGENT_FEE)} ₽</b>\n"
        f"▪️ Брокер: <b>{format_number(CHINA_BROKER_FEE)} ₽</b>\n"
        f"▪️ СВХ: <b>{format_number(CHINA_SVH_FEE)} ₽</b>\n"
        f"▪️ Лаборатория: <b>{format_number(CHINA_LAB_FEE)} ₽</b>\n"
        f"▪️ Моя комиссия: <b>{format_number(CHINA_YURI_FEE)} ₽</b>\n\n"
        f"🟰 Итого под ключ: <b>¥{format_number(int(total_cost_cny))}</b> | <b>{format_number(int(total_cost_rub))} ₽</b>\n\n"
        "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у меня:\n"
        f"▪️ @bratchikov_y (Юрий)\n\n"
        "🔗 <a href='https://t.me/bratchikov_cars'>Официальный телеграм канал</a>\n"
    )

    # Create keyboard
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Детали расчёта", callback_data="detail_china_manual")
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Связаться с менеджером", url="https://t.me/bratchikov_y"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Рассчитать другой автомобиль",
            callback_data="calculate_another_manual",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton("Главное меню", callback_data="main_menu")
    )

    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Cleanup user data
    if message.chat.id in user_data:
        del user_data[message.chat.id]


# ==================== END CHINA MANUAL CALCULATION FLOW ====================


@bot.message_handler(commands=["users"])
def handle_users_command(message):
    user_id = message.from_user.id
    if user_id not in MANAGERS:
        bot.reply_to(message, "❌ У вас нет доступа к этой команде.")
        return

    rows = get_all_users()  # получаем список всех пользователей

    if not rows:
        bot.reply_to(message, "❌ Пользователи не найдены.")
        return

    user_lines = []
    for r in rows:
        name = f"{r.get('first_name', '—')} {r.get('last_name') or ''}".strip()
        telegram_id = r.get("telegram_id", "—")
        created_at = r.get("created_at")
        created_str = (
            created_at.strftime("%d.%m.%Y %H:%M")
            if isinstance(created_at, datetime)
            else "—"
        )
        username = r.get("username", "")

        user_lines.append(
            f"👤 <b>{name}</b>\n"
            f"🆔 <code>{telegram_id}</code>\n"
            f"📅 {created_str}\n"
            f"Никнейм: @{username}\n"
        )

    batch = ""
    for line in user_lines:
        if len(batch + line + "\n\n") > 4000:
            bot.send_message(message.chat.id, batch.strip(), parse_mode="HTML")
            batch = ""
        batch += line + "----------------------------------------\n"

    if batch:
        bot.send_message(message.chat.id, batch.strip(), parse_mode="HTML")


# Обработчик ввода HP от пользователя
@bot.message_handler(func=lambda message: message.from_user.id in pending_hp_input)
def handle_hp_input(message):
    """Обрабатывает ввод мощности двигателя от пользователя."""
    global car_data, car_id_external

    user_id = message.from_user.id
    context = pending_hp_input.get(user_id)

    if not context:
        return

    user_input = message.text.strip()

    # Валидация ввода HP
    try:
        hp = int(user_input)
        if not (50 <= hp <= 1500):
            raise ValueError("HP out of range")
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Некорректное значение мощности.\n"
            "Пожалуйста, введите число от 50 до 1500 (например: 159):",
            parse_mode="HTML",
        )
        return  # Ждём корректного ввода

    # Удаляем пользователя из pending_hp_input
    del pending_hp_input[user_id]

    # Сохраняем HP в базу данных, если пользователь — менеджер
    if user_id in MANAGERS:
        try:
            save_hp_spec(
                context["car_manufacturer"],
                context["car_model"],
                context["car_generation"],
                context["car_engine_displacement"],
                hp,
                user_id,
            )
            bot.send_message(
                message.chat.id,
                f"✅ Мощность {hp} л.с. сохранена в базу данных для будущих расчётов.",
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"Ошибка при сохранении HP в БД: {e}")

    # Продолжаем расчёт с введённым HP
    bot.send_message(
        message.chat.id,
        f"⏳ Расчитываю стоимость с мощностью {hp} л.с...",
        parse_mode="HTML",
    )

    # Получаем таможенные платежи с calcus.ru
    try:
        response = get_customs_fees(
            context["car_engine_displacement"],
            context["price_krw"],
            int(context["formatted_car_year"]),
            context["car_month"],
            engine_type=1,
            owner_type=context["user_type"],
            power=hp,
        )

        customs_fee = clean_number(response["sbor"])
        customs_duty = clean_number(response["tax"])
        recycling_fee = clean_number(response["util"])
    except Exception as e:
        print(f"Ошибка при получении таможенных платежей: {e}")
        bot.send_message(
            message.chat.id,
            "❌ Ошибка при расчёте таможенных платежей. Попробуйте позже.",
            parse_mode="HTML",
        )
        return

    # Расчет итоговой стоимости
    car_engine_displacement = context["car_engine_displacement"]
    price_rub = context["price_rub"]
    price_krw = context["price_krw"]

    total_cost = (
        price_rub
        + 2000000 * get_actual_rub_to_krw_rate()
        + customs_fee
        + customs_duty
        + recycling_fee
        + 15000
        + 30000
        + 45000
        + 25000
        + 2000
        + 2000
        + 8000
        + 120000
        + (20000 if car_engine_displacement > 2000 else 0)
    )

    total_cost_krw = (
        price_krw
        + 2000000
        + customs_fee * get_actual_rub_to_krw_rate()
        + customs_duty * get_actual_rub_to_krw_rate()
        + recycling_fee * get_actual_rub_to_krw_rate()
        + 15000 * get_actual_rub_to_krw_rate()
        + 30000 * get_actual_rub_to_krw_rate()
        + 45000 * get_actual_rub_to_krw_rate()
        + 25000 * get_actual_rub_to_krw_rate()
        + 2000 * get_actual_rub_to_krw_rate()
        + 2000 * get_actual_rub_to_krw_rate()
        + 8000 * get_actual_rub_to_krw_rate()
        + 120000 * get_actual_rub_to_krw_rate()
        + (20000 / get_actual_rub_to_krw_rate() if car_engine_displacement > 2000 else 0)
    )

    # Сохраняем данные в car_data
    car_data["total_cost_krw"] = total_cost_krw
    car_data["total_cost_rub"] = total_cost
    car_data["car_price_krw"] = price_krw
    car_data["car_price_rub"] = price_rub
    car_data["parking_korea_krw"] = 440000
    car_data["parking_korea_rub"] = 440000 * get_actual_rub_to_krw_rate()
    car_data["car_review_krw"] = 300000
    car_data["car_review_rub"] = 300000 * get_actual_rub_to_krw_rate()
    car_data["korea_documents_krw"] = 150000
    car_data["korea_documents_rub"] = 150000 * get_actual_rub_to_krw_rate()
    car_data["transfer_korea_krw"] = 230000
    car_data["transfer_korea_rub"] = 230000 * get_actual_rub_to_krw_rate()
    car_data["freight_korea_krw"] = 880000
    car_data["freight_korea_rub"] = 880000 * get_actual_rub_to_krw_rate()
    car_data["customs_duty_rub"] = customs_duty
    car_data["customs_duty_krw"] = customs_duty / get_actual_rub_to_krw_rate()
    car_data["customs_fee_rub"] = customs_fee
    car_data["customs_fee_krw"] = customs_fee / get_actual_rub_to_krw_rate()
    car_data["util_fee_rub"] = recycling_fee
    car_data["util_fee_krw"] = recycling_fee / get_actual_rub_to_krw_rate()
    car_data["perm_registration_rub"] = 15000
    car_data["perm_registration_krw"] = 15000 / get_actual_rub_to_krw_rate()
    car_data["broker_rub"] = 30000
    car_data["broker_krw"] = 30000 / get_actual_rub_to_krw_rate()
    car_data["svh_rub"] = 45000
    car_data["svh_krw"] = 45000 / get_actual_rub_to_krw_rate()
    car_data["lab_rub"] = 25000
    car_data["lab_krw"] = 25000 / get_actual_rub_to_krw_rate()
    car_data["konosament_rub"] = 2000
    car_data["konosament_krw"] = 2000 / get_actual_rub_to_krw_rate()
    car_data["expertise_rub"] = 2000
    car_data["expertise_krw"] = 2000 / get_actual_rub_to_krw_rate()
    car_data["svh_transfer_rub"] = 8000
    car_data["svh_transfer_krw"] = 8000 / get_actual_rub_to_krw_rate()
    car_data["consultant_fee_rub"] = 20000 if car_engine_displacement > 2000 else 0
    car_data["consultant_fee_krw"] = (
        20000 / get_actual_rub_to_krw_rate() if car_engine_displacement > 2000 else 0
    )
    car_data["yuri_fee_rub"] = 120000
    car_data["yuri_fee_krw"] = 120000 / get_actual_rub_to_krw_rate()

    # Формируем результат
    car_title = context["car_title"]
    car_id = context["car_id"]
    car_id_external = car_id
    preview_link = context["preview_link"]
    car_photos = context["car_photos"]
    formatted_mileage = context["formatted_mileage"]
    formatted_transmission = context["formatted_transmission"]
    engine_volume_formatted = context["engine_volume_formatted"]
    age_formatted = context["age_formatted"]
    month = context["month"]
    year = context["year"]

    result_message = (
        f"🚗 {car_title}\n\n"
        f"🗓 Возраст: {age_formatted} (дата регистрации: {month}/{year})\n"
        f"🛣 Пробег: {formatted_mileage}\n"
        f"🔧 Объём двигателя: {engine_volume_formatted}\n"
        f"🐴 Мощность: {hp} л.с.\n"
        f"⚙️ КПП: {formatted_transmission}\n\n"
        f"💵 <b>Курс Воны к Рублю: {get_actual_rub_to_krw_rate():.4f} ₽</b>\n\n"
        f"🇰🇷 Платежи в Корее\n"
        f"▪️ Стоимость автомобиля: <b>₩{format_number(car_data['car_price_krw'])}</b> | <b>{format_number(car_data['car_price_rub'])} ₽</b>\n"
        f"▪️ Расходы по Корее (Фрахт, Стояночные, Логистика, Осмотр, Экспортные документы): <b>₩{format_number(2000000)}</b> | <b>{format_number(2000000 * get_actual_rub_to_krw_rate())} ₽</b>\n\n\n"
        f"🇷🇺 Платежи в России\n"
        f"▪️ <b>Единая таможенная ставка</b>: <b>{format_number(car_data['customs_duty_rub'])} ₽</b>\n"
        f"▪️ <b>Таможенное оформление</b>: <b>{format_number(car_data['customs_fee_rub'])} ₽</b>\n"
        f"▪️ <b>Утилизационный сбор</b>: <b>{format_number(car_data['util_fee_rub'])} ₽</b>\n\n"
        f"▪️ Брокер: <b>{format_number(car_data['broker_rub'])} ₽</b>\n"
        f"▪️ Временная регистрация: <b>{format_number(car_data['perm_registration_rub'])} ₽</b>\n"
        f"▪️ СВХ: <b>{format_number(car_data['svh_rub'])} ₽</b>\n"
        f"▪️ Лаборатория: <b>{format_number(car_data['lab_rub'])} ₽</b>\n"
        f"▪️ Коносамент: <b>{format_number(car_data['konosament_rub'])} ₽</b>\n"
        f"▪️ Экспертиза: <b>{format_number(car_data['expertise_rub'])} ₽</b>\n"
        f"▪️ Перегон из СВХ: <b>{format_number(car_data['svh_transfer_rub'])} ₽</b>\n"
        f"▪️ Услуги консультанта: <b>{format_number(car_data['consultant_fee_rub'])} ₽</b>\n"
        f"▪️ Моя комиссия: <b>{format_number(car_data['yuri_fee_rub'])} ₽</b>\n\n"
        f"🟰 Итого под ключ до Владивостока: <b>{format_number(car_data['total_cost_rub'])} ₽</b>\n\n"
        f"🔗 <a href='{preview_link}'>Ссылка на автомобиль</a>\n\n"
        "Если данное авто попадает под санкции, пожалуйста уточните возможность отправки в вашу страну у меня:\n"
        f"▪️ @bratchikov_y (Юрий)\n\n"
        "🔗 <a href='https://t.me/bratchikov_cars'>Официальный телеграм канал</a>\n"
    )

    # Клавиатура
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(
            "⭐ Добавить в избранное",
            callback_data=f"add_favorite_{car_id_external}",
        )
    )

    if "fem.encar.com" in context["link"]:
        keyboard.add(
            types.InlineKeyboardButton(
                "Технический Отчёт об Автомобиле", callback_data="technical_card"
            )
        )
        keyboard.add(
            types.InlineKeyboardButton(
                "Выплаты по ДТП",
                callback_data="technical_report",
            )
        )
    keyboard.add(
        types.InlineKeyboardButton(
            "Написать менеджеру", url="https://t.me/bratchikov_y"
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Расчёт другого автомобиля",
            callback_data="calculate_another",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            "Главное меню",
            callback_data="main_menu",
        )
    )

    # Отправляем фотографии
    media_group = []
    for photo_url in sorted(car_photos):
        try:
            resp = requests.get(photo_url)
            if resp.status_code == 200:
                photo = BytesIO(resp.content)
                media_group.append(types.InputMediaPhoto(photo))

                if len(media_group) == 10:
                    bot.send_media_group(message.chat.id, media_group)
                    media_group.clear()
        except Exception as e:
            print(f"Ошибка при загрузке фото: {e}")

    if media_group:
        bot.send_media_group(message.chat.id, media_group)

    # Сохраняем данные об авто
    car_data["car_id"] = car_id
    car_data["name"] = car_title
    car_data["images"] = car_photos if isinstance(car_photos, list) else []
    car_data["link"] = preview_link
    car_data["year"] = year
    car_data["month"] = month
    car_data["mileage"] = formatted_mileage
    car_data["engine_volume"] = car_engine_displacement
    car_data["transmission"] = formatted_transmission
    car_data["car_price"] = price_krw
    car_data["user_name"] = message.from_user.username
    car_data["first_name"] = message.from_user.first_name
    car_data["last_name"] = message.from_user.last_name

    bot.send_message(
        message.chat.id,
        result_message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )

    # Удаляем сообщение "Обрабатываю данные..."
    try:
        bot.delete_message(message.chat.id, context["processing_message_id"])
    except Exception:
        pass


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_message = message.text.strip()

    # Проверяем нажатие кнопки "Рассчитать автомобиль"
    if user_message == CALCULATE_CAR_TEXT:
        add_user_if_not_exists(message.from_user)
        print(message.from_user)
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.add(types.KeyboardButton("Физ. лицо"), types.KeyboardButton("Юр. лицо"))
        bot.send_message(
            message.chat.id,
            "Выберите тип расчета:",
            reply_markup=markup,
        )

    elif user_message in ["Физ. лицо", "Юр. лицо"]:
        user_type = 1 if user_message == "Физ. лицо" else 2
        user_type_map[message.from_user.id] = user_type

        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с одного из сайтов (encar.com, kbchachacha.com, kcar.com, che168.com):",
            reply_markup=types.ReplyKeyboardRemove(),  # Убираем клавиатуру
        )

    elif user_message == "Ручной расчёт":
        # Show country selection inline keyboard
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("🇰🇷 Корея", callback_data="manual_country_korea"),
            types.InlineKeyboardButton("🇨🇳 Китай", callback_data="manual_country_china"),
        )
        bot.send_message(
            message.chat.id,
            "Выберите страну для расчёта:",
            reply_markup=keyboard,
        )

    elif user_message == "Вопрос/Ответ":
        show_faq(message)

    elif re.match(
        r"^https?://(www|fem)\.encar\.com/.*|^https?://(www\.)?kbchachacha\.com/.*|^https?://m\.kbchachacha\.com/.*|^https?://(www\.)?kcar\.com/.*|^https?://m\.kcar\.com/.*",
        user_message,
    ):
        user_type = user_type_map.get(message.from_user.id)

        if user_type is None:
            markup = types.ReplyKeyboardMarkup(
                resize_keyboard=True, one_time_keyboard=True
            )
            markup.add(
                types.KeyboardButton("Физ. лицо"), types.KeyboardButton("Юр. лицо")
            )
            bot.send_message(
                message.chat.id,
                "❗️Пожалуйста, выберите *Тип расчёта* перед отправкой ссылки.",
                parse_mode="Markdown",
                reply_markup=markup,
            )
        else:
            calculate_cost(user_message, message, user_type)

    elif is_che168_url(user_message):
        user_type = user_type_map.get(message.from_user.id)

        if user_type is None:
            markup = types.ReplyKeyboardMarkup(
                resize_keyboard=True, one_time_keyboard=True
            )
            markup.add(
                types.KeyboardButton("Физ. лицо"), types.KeyboardButton("Юр. лицо")
            )
            bot.send_message(
                message.chat.id,
                "❗️Пожалуйста, выберите *Тип расчёта* перед отправкой ссылки.",
                parse_mode="Markdown",
                reply_markup=markup,
            )
        else:
            calculate_china_cost(user_message, message, user_type)

    elif user_message == "Написать менеджеру":
        managers_list = [
            {"name": "Юрий ", "whatsapp": "https://wa.me/79250108056"},
        ]

        # Формируем сообщение со списком менеджеров
        message_text = "Вы можете связаться с одним из наших менеджеров:\n\n"
        for manager in managers_list:
            message_text += f"[{manager['name']}]({manager['whatsapp']})\n"

        # Отправляем сообщение с использованием Markdown
        bot.send_message(message.chat.id, message_text, parse_mode="Markdown")

    elif user_message == "О нас":
        about_message = "Bratchikov Cars\nЮжнокорейская экспортная компания.\nСпециализируемся на поставках автомобилей из Южной Кореи в страны СНГ.\nОпыт работы более 5 лет.\n\nПочему выбирают нас?\n• Надежность и скорость доставки.\n• Индивидуальный подход к каждому клиенту.\n• Полное сопровождение сделки.\n\n💬 Ваш путь к надежным автомобилям начинается здесь!"
        bot.send_message(message.chat.id, about_message)

    elif user_message == "Telegram-канал":
        channel_link = "https://t.me/bratchikov_cars"
        bot.send_message(
            message.chat.id, f"Подписывайтесь на наш Telegram-канал: {channel_link}"
        )

    elif user_message == "YouTube":
        youtube_link = "https://www.youtube.com/@KoreaCar_import"
        bot.send_message(
            message.chat.id,
            f"Свежий контент из Южной Кореи: {youtube_link}",
        )

    else:
        bot.send_message(
            message.chat.id,
            "Пожалуйста, введите ссылку на автомобиль с сайта (encar.com, kbchachacha.com, kcar.com, che168.com)",
        )


logger = logging.getLogger(__name__)


if __name__ == "__main__":
    set_bot_commands()
    create_tables()

    # Настройка обхода блокировок
    telebot.apihelper.RETRY_ON_ERROR = True

    # Бот работает через long polling, поэтому webhook должен быть снят.
    # Достаточно одного вызова при старте: на VPS всегда запущен ровно один
    # экземпляр (systemd), в отличие от Heroku, где дино могли пересекаться.
    try:
        bot.remove_webhook()
    except Exception as e:
        print(f"Ошибка при удалении webhook: {e}")

    # interval=0: при long polling (timeout=30) пауза между вызовами getUpdates
    # не нужна — она добавляла до секунды задержки на каждое сообщение.
    bot.polling(none_stop=True, interval=0, timeout=30)
