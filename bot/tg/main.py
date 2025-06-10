import threading
import queue
import asyncio
from tg_bot import run_telebot, tb_bot
from bot.tg.DCACall.parserDCACall import pyro_client, message_queue, check_trade_timeouts
from bot.tg.DCACall.bybit_trader import BybitTrader  # Импорт BybitTrader из DCACall
import time# Настройки Bybit API
BYBIT_API_KEY = "577ZLJi9GvUAWLRyDt"
BYBIT_API_SECRET = "CWmMdET6GhOhJJCHAWhS2GZo9rB5R9sKxZYy"
trader = BybitTrader(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=False)# Целевой пользователь для сообщений
TARGET_USER = 793784229# Функция для отправки сообщений с повторными попытками
def send_message_with_retries(user_id, text, retries=3, delay=5, timeout=60):
    for attempt in range(retries):
        try:
            tb_bot.send_message(user_id, text, timeout=timeout)
            print(f" Сообщение отправлено пользователю: {user_id}")
            return
        except Exception as e:
            print(f" Попытка {attempt + 1}/{retries} не удалась: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    print(f" Не удалось отправить сообщение после {retries} попыток")# Функция для обработки очереди сообщений
def send_messages_from_queue():
    while True:
        try:
            text = message_queue.get()
            if text:
                send_message_with_retries(TARGET_USER, text)
        except Exception as e:
            print(" Ошибка при обработке очереди:", e)
            time.sleep(1)
if __name__ == "__main__":
    # Запуск Telebot в отдельном потоке
    telebot_thread = threading.Thread(target=run_telebot, daemon=True)
    telebot_thread.start()

    # Запуск потока для обработки очереди сообщений
    queue_thread = threading.Thread(target=send_messages_from_queue, daemon=True)
    queue_thread.start()

    # Запуск Pyrogram и других асинхронных задач
    print("🔍 Запуск парсера Pyrogram...")

    # Запускаем Pyrogram и добавляем задачу check_trade_timeouts
    pyro_client.loop.create_task(check_trade_timeouts())
    pyro_client.run()

