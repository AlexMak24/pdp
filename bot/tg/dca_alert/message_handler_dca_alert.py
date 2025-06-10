import asyncio
import json
import re
from datetime import datetime, timedelta
from pyrogram import Client

# Настройки Pyrogram (замени на свои значения)
SESSION_NAME = "my_session"
API_ID = 2867162  # Твой API ID
API_HASH = "0e7fe16d0f9ecfec4c58315e32991ea8"  # Твой API Hash
CHANNEL_ID = 'dca_alert'  # ID твоего канала

# Инициализация клиента Pyrogram
pyro_client = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH)

# Структура для хранения текущих сделок
current_trades = {}

# Файл для хранения закрытых сделок
ALL_TRADES_FILE = '../all_trades.json'

# Функция для парсинга ETA
def parse_eta(eta_text):
    """Извлекает ETA из текста и возвращает его в минутах."""
    hours = 0
    minutes = 0
    hours_match = re.search(r'(\d+)h', eta_text)
    if hours_match:
        hours = int(hours_match.group(1))
    minutes_match = re.search(r'(\d+)min', eta_text)
    if minutes_match:
        minutes = int(minutes_match.group(1))
    return hours * 60 + minutes

# Функция для сериализации сообщения
def serialize_message(message):
    """Извлекает ключевые данные из текста сообщения."""
    text = message.text.lower()
    try:
        # Извлечение символа и стороны
        if 'buying' in text:
            symbol = text.split('buying ')[1].split(' ')[0]
            side = 'buy'
        elif 'selling' in text:
            symbol = text.split('selling ')[1].split(' ')[0]
            side = 'sell'
        else:
            symbol = None
            side = None

        # Извлечение суммы (например, "$150.02k" → 150020.0)
        amount_str = text.split('$')[1].split('k')[0] if '$' in text and 'k' in text else '0'
        amount_usd = float(amount_str) * 1000

        # Извлечение цены (например, "price: 0.544400$")
        price_line = next((line for line in text.split('\n') if 'price:' in line), None)
        price = float(price_line.split('price:')[1].strip().replace('$', '')) if price_line else None

        # Извлечение ETA (например, "eta: 30min" или "eta: 1h 20min")
        eta_line = next((line for line in text.split('\n') if 'eta:' in line), None)
        if eta_line:
            eta_text = eta_line.split('eta:')[1].strip()
            eta_minutes = parse_eta(eta_text)
        else:
            eta_minutes = 0

        # Извлечение времени начала и окончания
        time_line = next((line for line in text.split('\n') if 'utc' in line and '➞' in line), None)
        if time_line:
            start_time_str, end_time_str = time_line.split('➞')
            start_time = datetime.strptime(start_time_str.strip(), '%d %b %Y %H:%M:%S UTC')
            end_time = datetime.strptime(end_time_str.strip(), '%d %b %Y %H:%M:%S UTC')
        else:
            start_time = datetime.utcnow()
            end_time = start_time + timedelta(minutes=eta_minutes) if eta_minutes else None

        return {
            'message_id': message.id,
            'symbol': symbol,
            'side': side,
            'amount_usd': amount_usd,
            'price': price,
            'start_time': str(start_time),
            'end_time': str(end_time) if end_time else None,
            'eta_minutes': eta_minutes,
            'status': 'pending',
            'text': "smt"
        }
    except Exception as e:
        print(f"Ошибка сериализации сообщения {message.id}: {e}")
        return None

# Функция для закрытия сделки
def close_trade(trade, reason):
    """Закрывает сделку и сохраняет её в файл."""
    trade['status'] = 'closed'
    trade['close_time'] = str(datetime.utcnow())
    trade['close_reason'] = reason
    with open(ALL_TRADES_FILE, 'a') as f:
        json.dump(trade, f)
        f.write('\n')
    print(f"Сделка {trade['message_id']} закрыта: {reason}")

# Функция для проверки таймаутов
async def check_trade_timeouts():
    """Периодически проверяет сделки на истечение времени."""
    while True:
        current_time = datetime.utcnow()
        for trade_id, trade in list(current_trades.items()):
            if trade['status'] == 'pending' and trade['end_time']:
                end_time = datetime.strptime(trade['end_time'], '%Y-%m-%d %H:%M:%S')
                if current_time >= end_time:
                    close_trade(trade, 'timeout')
                    del current_trades[trade_id]
        await asyncio.sleep(60)  # Проверка каждую минуту

# Функция для получения и обработки сообщений
async def fetch_and_process_messages(client, channel_id):
    """Обрабатывает сообщения из Telegram-канала."""
    async for message in client.get_chat_history(channel_id, limit=10):
        if message.text:
            print(f"\nКанал: {channel_id}")
            print(f"ID сообщения: {message.id}")
            print(f"Текст: {message.text}")

            trade = serialize_message(message)
            if trade and trade['symbol']:
                current_trades[message.id] = trade
                print(f"Добавлена новая сделка: {trade['symbol']} {trade['side']}")
            else:
                print("Сообщение не распознано как сделка")

            print(f"Текущие сделки: {current_trades}")
            print('-' * 50)

# Основная функция
async def main():
    """Запускает программу."""
    async with pyro_client:
        asyncio.create_task(check_trade_timeouts())  # Фоновая проверка таймаутов
        await fetch_and_process_messages(pyro_client, CHANNEL_ID)

# Запуск программы
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Программа остановлена пользователем")
    except Exception as e:
        print(f"Необработанная ошибка: {e}")