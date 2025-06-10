import telebot
from telebot import types
import json
from bot.tg.DCACall.bybit_trader import BybitTrader
import logging

# Настройки Telegram-бота
BOT_TOKEN = "8023569170:AAHo_j_38aFIV07KpppsX4zOTwl97lnRg_E"
API_KEY = "577ZLJi9GvUAWLRyDt"
API_SECRET = "CWmMdET6GhOhJJCHAWhS2GZo9rB5R9sKxZYy"
tb_bot = telebot.TeleBot(BOT_TOKEN)

# Путь к файлу для хранения состояния trading
TRADING_STATE_FILE = "trading_state.json"

# Путь к файлу с закрытыми сделками
ALL_TRADES_FILE = '../all_trades.json'

# Инициализация BybitTrader
try:
    trader = BybitTrader(api_key=API_KEY, api_secret=API_SECRET, testnet=False)
except Exception as e:
    print(f"Ошибка инициализации BybitTrader: {e}")
    trader = None


# Инициализация состояния trading
def init_trading_state():
    try:
        with open(TRADING_STATE_FILE, 'r') as f:
            state = json.load(f)
            return state.get('trading', True)
    except FileNotFoundError:
        state = {'trading': True}
        with open(TRADING_STATE_FILE, 'w') as f:
            json.dump(state, f)
        return True


trading = init_trading_state()

# Настройка логирования
logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Функция для создания клавиатуры
def create_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(types.KeyboardButton('/start'))
    keyboard.add(types.KeyboardButton('/enable'))
    keyboard.add(types.KeyboardButton('/disable'))
    keyboard.add(types.KeyboardButton('/balance'))
    keyboard.add(types.KeyboardButton('/pnl'))
    keyboard.add(types.KeyboardButton('/positions'))
    return keyboard


# Функция для получения всех уникальных тикеров
def get_all_tickers():
    tickers = set()
    try:
        current_trades = trader.get_current_trades() if trader else {}
        for trade in current_trades.values():
            ticker = trade['symbol'].upper() + 'USDT'
            tickers.add(ticker)
    except Exception as e:
        logger.error(f"Ошибка при получении активных сделок: {e}")

    try:
        with open(ALL_TRADES_FILE, 'r') as f:
            for line in f:
                try:
                    trade = json.loads(line)
                    ticker = trade['symbol'].upper() + 'USDT'
                    tickers.add(ticker)
                except json.JSONDecodeError:
                    logger.error(f"Ошибка при чтении строки в {ALL_TRADES_FILE}")
    except FileNotFoundError:
        logger.error(f"Файл {ALL_TRADES_FILE} не найден")

    return list(tickers)


# Обработчики команд
@tb_bot.message_handler(commands=['start'])
def start_message(message):
    keyboard = create_keyboard()
    tb_bot.send_message(message.chat.id, "Бот запущен ✅", reply_markup=keyboard)


@tb_bot.message_handler(commands=['id'])
def get_user_id(message):
    keyboard = create_keyboard()
    tb_bot.send_message(message.chat.id, f"Ваш user_id: `{message.chat.id}`", parse_mode="Markdown",
                        reply_markup=keyboard)


@tb_bot.message_handler(commands=['enable'])
def enable_trading(message):
    global trading
    trading = True
    with open(TRADING_STATE_FILE, 'w') as f:
        json.dump({'trading': trading}, f)
    keyboard = create_keyboard()
    tb_bot.send_message(message.chat.id, "✅ Торговля включена", reply_markup=keyboard)


@tb_bot.message_handler(commands=['disable'])
def disable_trading(message):
    global trading
    trading = False
    with open(TRADING_STATE_FILE, 'w') as f:
        json.dump({'trading': trading}, f)
    keyboard = create_keyboard()
    tb_bot.send_message(message.chat.id, "⛔ Торговля отключена", reply_markup=keyboard)


@tb_bot.message_handler(commands=['balance'])
def get_balance(message):
    if trader is None:
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, "Ошибка: BybitTrader не инициализирован", reply_markup=keyboard)
        return
    try:
        balance = trader.get_current_balance()
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, f"Текущий баланс: {balance:.2f} USDT", reply_markup=keyboard)
    except Exception as e:
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, f"Ошибка при получении баланса: {str(e)}", reply_markup=keyboard)


@tb_bot.message_handler(commands=['pnl'])
def get_pnl(message):
    if trader is None:
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, "Ошибка: BybitTrader не инициализирован", reply_markup=keyboard)
        return
    try:
        symbols = get_all_tickers()
        if not symbols:
            keyboard = create_keyboard()
            tb_bot.send_message(message.chat.id, "Нет тикеров для получения PnL.", reply_markup=keyboard)
            return
        pnl_list, _ = trader.get_closed_pnl(symbols=symbols, limit=5)
        if pnl_list:
            response = "=== История PnL ===\n"
            for pnl in pnl_list:
                closed_pnl = float(pnl['closedPnl'])
                profit_loss = "прибыль" if closed_pnl > 0 else "убыток"
                response += (f"Символ: {pnl['symbol']}, "
                             f"PnL: {closed_pnl:.2f} USDT ({profit_loss}), "
                             f"Цена входа: {pnl['avgEntryPrice']}, "
                             f"Цена выхода: {pnl['avgExitPrice']}\n")
            keyboard = create_keyboard()
            tb_bot.send_message(message.chat.id, response, reply_markup=keyboard)
        else:
            keyboard = create_keyboard()
            tb_bot.send_message(message.chat.id, "Нет данных по закрытым PnL.", reply_markup=keyboard)
    except Exception as e:
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, f"Ошибка при получении PnL: {str(e)}", reply_markup=keyboard)


@tb_bot.message_handler(commands=['positions'])
def get_positions(message):
    if trader is None:
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, "Ошибка: BybitTrader не инициализирован", reply_markup=keyboard)
        return
    try:
        symbols = get_all_tickers()
        if not symbols:
            keyboard = create_keyboard()
            tb_bot.send_message(message.chat.id, "Нет тикеров для получения позиций.", reply_markup=keyboard)
            return
        positions = trader.get_open_positions(symbols=symbols)
        if positions:
            response = "=== Открытые позиции ===\n"
            for pos in positions:
                response += (f"Символ: {pos['symbol']}, "
                             f"Сторона: {pos['side']}, "
                             f"Размер: {pos['size']}, "
                             f"Цена входа: {pos['avgPrice']}, "
                             f"Нереализованный PnL: {pos['unrealisedPnl']}\n")
            keyboard = create_keyboard()
            tb_bot.send_message(message.chat.id, response, reply_markup=keyboard)
        else:
            keyboard = create_keyboard()
            tb_bot.send_message(message.chat.id, "Нет открытых позиций для указанных монет.", reply_markup=keyboard)
    except Exception as e:
        keyboard = create_keyboard()
        tb_bot.send_message(message.chat.id, f"Ошибка: {str(e)}", reply_markup=keyboard)


# Функция для запуска Telebot
def run_telebot():
    print("Запуск Telebot...")
    tb_bot.infinity_polling()


if __name__ == "__main__":
    run_telebot()