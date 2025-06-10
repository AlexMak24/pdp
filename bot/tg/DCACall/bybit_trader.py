from pybit.unified_trading import HTTP
import time
from math import floor, ceil
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BybitTrader:
    def __init__(self, api_key, api_secret, testnet=True, position_size_usd=50):
        """Инициализация торгового бота Bybit."""
        self.session = HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
            recv_window=10000
        )
        self.margin_usd = position_size_usd
        self.instrument_specs = {}  # Кэш спецификаций инструментов

    def get_instrument_specs(self, symbol):
        """Получение спецификаций инструмента."""
        if symbol in self.instrument_specs:
            return self.instrument_specs[symbol]

        try:
            response = self.session.get_instruments_info(category="linear", symbol=symbol)
            if response["retCode"] == 0:
                info = response["result"]["list"][0]
                qty_step = float(info["lotSizeFilter"]["qtyStep"])
                price_step = float(info["priceFilter"]["tickSize"])
                min_order_qty = float(info["lotSizeFilter"]["minOrderQty"])
                max_order_qty = float(info["lotSizeFilter"]["maxOrderQty"])
                max_leverage = float(info["leverageFilter"]["maxLeverage"])
                current_price = self.get_current_price(symbol)
                min_order_size = min_order_qty * current_price
                max_order_size = max_order_qty * current_price
                specs = (qty_step, price_step, min_order_size, max_order_size, max_leverage)
                self.instrument_specs[symbol] = specs
                logger.info(f"Спецификации {symbol}: qty_step={qty_step}, price_step={price_step}, "
                            f"min_order_size={min_order_size:.2f} USDT, max_order_size={max_order_size:.2f} USDT, "
                            f"max_leverage={max_leverage}")
                return specs
            else:
                logger.error(f"Ошибка при получении спецификаций: {response['retMsg']}")
                return 0.001, 0.01, 50, 10000, 10
        except Exception as e:
            logger.error(f"Ошибка при получении спецификаций: {e}")
            return 0.001, 0.01, 50, 10000, 10

    def get_current_price(self, symbol):
        """Получение текущей рыночной цены."""
        try:
            response = self.session.get_tickers(category="linear", symbol=symbol)
            if response["retCode"] == 0:
                return float(response["result"]["list"][0]["lastPrice"])
            else:
                logger.error(f"Ошибка при получении цены: {response['retMsg']}")
                return 1.0
        except Exception as e:
            logger.error(f"Ошибка при получении цены: {e}")
            return 1.0

    def round_to_tick(self, price, price_step):
        """Округление цены до шага цены."""
        return round(price / price_step) * price_step

    def get_best_bid_ask(self, symbol):
        """Получение лучшей цены bid и ask."""
        for _ in range(3):
            try:
                orderbook = self.session.get_orderbook(category="linear", symbol=symbol)
                if orderbook["retCode"] == 0:
                    best_bid = float(orderbook["result"]["b"][0][0])
                    best_ask = float(orderbook["result"]["a"][0][0])
                    return best_bid, best_ask
                else:
                    logger.error(f"Ошибка при получении стакана: {orderbook['retMsg']}")
            except Exception as e:
                logger.error(f"Исключение при получении стакана: {e}")
            time.sleep(2)
        return None, None

    def check_position_mode(self, symbol):
        """Проверяет текущий режим позиции для символа."""
        try:
            response = self.session.get_positions(category="linear", symbol=symbol)
            if response["retCode"] == 0:
                positions = response["result"]["list"]
                sides = set(pos["side"] for pos in positions if float(pos["size"]) > 0)
                is_hedge_mode = len(sides) > 1 or any(pos.get("positionIdx") in [1, 2] for pos in positions)
                logger.info(f"Текущий режим позиции для {symbol}: {'Hedge' if is_hedge_mode else 'One-Way'}")
                return 3 if is_hedge_mode else 0
            else:
                logger.error(f"Ошибка при проверке режима позиции: {response['retMsg']}")
                return None
        except Exception as e:
            logger.error(f"Ошибка при проверке режима позиции: {e}")
            return None

    def switch_to_hedge_mode(self, symbol):
        """Переключает на Hedge Mode."""
        try:
            response = self.session.switch_position_mode(category="linear", symbol=symbol, mode=3)
            if response["retCode"] == 0:
                logger.info(f"Успешно переключено на Hedge Mode для {symbol}")
                return True
            else:
                logger.error(f"Ошибка переключения на Hedge Mode: {response['retMsg']}")
                return False
        except Exception as e:
            logger.error(f"Ошибка при переключении режима: {e}")
            return False

    def set_leverage(self, symbol, leverage):
        """Установка плеча с учетом текущего значения и обработки ошибок."""
        try:
            position_info = self.session.get_positions(category="linear", symbol=symbol)
            if position_info["retCode"] != 0:
                logger.error(f"Ошибка при получении позиций: {position_info['retMsg']}")
                return False

            positions = position_info["result"]["list"]
            if positions:
                current_leverage = float(positions[0]["leverage"])
                logger.info(f"Текущее плечо для {symbol}: {current_leverage}x")
                if current_leverage == leverage:
                    logger.info(f"Плечо уже установлено на {leverage}x, установка не требуется")
                    return True
            else:
                logger.info(f"Позиций нет для {symbol}, устанавливаем плечо")

            response = self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage)
            )
            if response["retCode"] == 0:
                logger.info(f"Плечо успешно установлено для {symbol}: {leverage}x")
                return True
            elif response["retCode"] == 110043:
                logger.info(f"Плечо не изменено, так как уже установлено на {leverage}x")
                return True
            else:
                logger.error(f"Ошибка при установке плеча: {response['retMsg']} (ErrCode: {response['retCode']})")
                return False
        except Exception as e:
            logger.error(f"Ошибка при установке плеча: {e}")
            return False

    def calculate_quantity(self, price, leverage, qty_step):
        """Расчет количества контрактов."""
        position_size = self.margin_usd * leverage
        quantity = position_size / price
        qty = round(quantity / qty_step) * qty_step
        return qty

    def get_available_balance(self):
        """Получение доступного баланса USDT."""
        for attempt in range(3):
            try:
                wallet = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
                if wallet["retCode"] == 0:
                    for coin in wallet["result"]["list"][0]["coin"]:
                        if coin["coin"] == "USDT":
                            balance = float(coin["walletBalance"])
                            logger.info(f"Доступный баланс USDT: ${balance}")
                            return balance
                    return 0
                else:
                    logger.error(f"Ошибка при получении баланса: {wallet['retMsg']}")
            except Exception as e:
                logger.error(f"Ошибка при получении баланса: {e}")
            time.sleep(2)
        return 0

    def check_order_status(self, symbol, order_id):
        """Проверка статуса ордера."""
        try:
            response = self.session.get_open_orders(category="linear", symbol=symbol, orderId=order_id)
            if response["retCode"] == 0 and response["result"]["list"]:
                return response["result"]["list"][0]["orderStatus"]
            return None
        except Exception as e:
            logger.error(f"Ошибка при проверке статуса ордера: {e}")
            return None

    def place_order(self, symbol, side, stop_loss_pct=None, leverage=None, take_profit_pct=None, qty=None,
                    reduce_only=False, max_attempts=5):
        """Размещение лимитного ордера с движением за BBO."""
        try:
            # Получение спецификаций инструмента
            qty_step, price_step, min_order_size, max_order_size, max_leverage = self.get_instrument_specs(symbol)
            if leverage:
                leverage = min(leverage, max_leverage)

            # Проверка баланса (только для открытия позиции)
            if not reduce_only:
                available_balance = self.get_available_balance()
                if available_balance < self.margin_usd + 0.2:
                    logger.error(
                        f"Недостаточно средств: требуется маржа ${self.margin_usd}, доступно ${available_balance}")
                    return None

            # Проверка и установка Hedge Mode
            position_mode = self.check_position_mode(symbol)
            if position_mode is None:
                return None
            if position_mode != 3:
                logger.info(f"Переключение на Hedge Mode для {symbol}...")
                if not self.switch_to_hedge_mode(symbol):
                    return None

            # Установка плеча (только для открытия позиции)
            if leverage and not reduce_only:
                if not self.set_leverage(symbol, leverage):
                    return None

            order_id = None
            attempts = 0

            while attempts < max_attempts:
                best_bid, best_ask = self.get_best_bid_ask(symbol)
                if best_bid is None or best_ask is None:
                    return None

                if side.lower() == "buy":
                    price = best_ask if reduce_only else best_ask * 1.001
                    order_side = "Buy"
                    position_idx = 2 if reduce_only else 1  # 2 для закрытия short, 1 для открытия long
                    if stop_loss_pct is not None and not reduce_only:
                        stop_loss = price * (1 - stop_loss_pct / 100)
                        stop_loss = floor(stop_loss / price_step) * price_step
                    else:
                        stop_loss = None
                    if take_profit_pct is not None and not reduce_only:
                        take_profit = price * (1 + take_profit_pct / 100)
                        take_profit = ceil(take_profit / price_step) * price_step
                    else:
                        take_profit = None
                elif side.lower() == "sell":
                    price = best_bid if reduce_only else best_bid * 0.999
                    order_side = "Sell"
                    position_idx = 1 if reduce_only else 2  # 1 для закрытия long, 2 для открытия short
                    if stop_loss_pct is not None and not reduce_only:
                        stop_loss = price * (1 + stop_loss_pct / 100)
                        stop_loss = ceil(stop_loss / price_step) * price_step
                    else:
                        stop_loss = None
                    if take_profit_pct is not None and not reduce_only:
                        take_profit = price * (1 - take_profit_pct / 100)
                        take_profit = floor(take_profit / price_step) * price_step
                    else:
                        take_profit = None
                else:
                    logger.error("Ошибка: Укажите 'buy' или 'sell'")
                    return None

                # Округление цены до шага цены
                price = self.round_to_tick(price, price_step)

                # Используем переданный qty для закрытия или рассчитываем для открытия
                order_qty = qty if qty is not None else self.calculate_quantity(price, leverage, qty_step)

                # Округляем qty до qty_step
                order_qty = round(float(order_qty) / qty_step) * qty_step

                if order_qty * price < min_order_size or order_qty * price > max_order_size:
                    logger.error(
                        f"Размер ордера вне допустимого диапазона: {order_qty * price} USDT "
                        f"(min: {min_order_size}, max: {max_order_size})")
                    return None

                if order_id:
                    try:
                        self.session.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                        logger.info(f"Отменен предыдущий ордер ID: {order_id}")
                    except Exception as e:
                        logger.error(f"Ошибка при отмене предыдущего ордера ID {order_id}: {e}")

                order_params = {
                    "category": "linear",
                    "symbol": symbol,
                    "side": order_side,
                    "orderType": "Limit",
                    "price": str(price),
                    "qty": str(order_qty),
                    "timeInForce": "GTC",
                    "positionIdx": position_idx,
                    "reduceOnly": reduce_only
                }
                if stop_loss is not None:
                    order_params["stopLoss"] = str(stop_loss)
                if take_profit is not None:
                    order_params["takeProfit"] = str(take_profit)

                order = self.session.place_order(**order_params)

                if order["retCode"] != 0:
                    logger.error(f"Ошибка при размещении ордера: {order['retMsg']} (ErrCode: {order['retCode']})")
                    return None

                order_id = order["result"]["orderId"]
                logger.info(f"Ордер {order_side} на {order_qty} {symbol} размещен по цене {price}" +
                            (f", SL: {stop_loss}" if stop_loss else "") +
                            (f", TP: {take_profit}" if take_profit else ""))

                time.sleep(5)
                status = self.check_order_status(symbol, order_id)

                if status == "Filled":
                    logger.info(f"Ордер исполнен по цене {price}")
                    return order
                elif status in ["New", "PartiallyFilled"]:
                    logger.info(f"Ордер не исполнен, статус: {status}, обновляем цену")
                    attempts += 1
                else:
                    logger.info(f"Ордер завершен со статусом: {status}")
                    return None

            logger.warning(f"Достигнуто максимальное количество попыток ({max_attempts})")
            try:
                self.session.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                logger.info(f"Отменен ордер ID: {order_id}")
            except Exception as e:
                logger.error(f"Ошибка при отмене ордера ID {order_id}: {e}")
            return None

        except Exception as e:
            logger.error(f"Ошибка при размещении ордера: {e}")
            return None

    def run(self, ticker, side, stop_loss, leverage, take_profit=None):
        """Запуск бота с указанными параметрами для открытия позиции."""
        logger.info(
            f"Запуск торгового бота для {ticker}, сторона: {side}, стоп-лосс: {stop_loss}%, "
            f"плечо: {leverage}x" + (f", тейк-профит: {take_profit}%" if take_profit is not None else ""))
        self.place_order(ticker, side, stop_loss, leverage, take_profit)

    def close_position(self, symbol):
        """Закрытие позиции для указанного символа лимитным ордером."""
        try:
            # Получение спецификаций инструмента
            qty_step, price_step, min_order_size, max_order_size, max_leverage = self.get_instrument_specs(symbol)

            # Получение текущих позиций
            response = self.session.get_positions(category="linear", symbol=symbol)
            if response["retCode"] != 0:
                logger.error(f"Ошибка при получении позиций: {response['retMsg']}")
                return False

            positions = response["result"]["list"]
            active_positions = [pos for pos in positions if float(pos["size"]) > 0]

            if not active_positions:
                logger.info(f"Нет открытых позиций для {symbol}")
                return True

            for pos in active_positions:
                # Определяем сторону закрытия
                close_side = "sell" if pos["side"] == "Buy" else "buy"
                qty = float(pos["size"])  # Преобразуем строку в float

                # Округляем количество до qty_step
                qty = round(qty / qty_step) * qty_step

                # Проверяем, что размер ордера в допустимом диапазоне
                current_price = self.get_current_price(symbol)
                if qty * current_price < min_order_size or qty * current_price > max_order_size:
                    logger.error(
                        f"Размер ордера для закрытия вне допустимого диапазона: {qty * current_price} USDT "
                        f"(min: {min_order_size}, max: {max_order_size})")
                    return False

                # Повторная проверка позиции перед размещением ордера
                response = self.session.get_positions(category="linear", symbol=symbol)
                if response["retCode"] != 0:
                    logger.error(f"Ошибка при повторной проверке позиций: {response['retMsg']}")
                    return False

                current_positions = [p for p in response["result"]["list"] if float(p["size"]) > 0 and p["side"] == pos["side"]]
                if not current_positions:
                    logger.info(f"Позиция {pos['side']} для {symbol} уже закрыта или отсутствует")
                    continue

                # Размещаем лимитный ордер для закрытия
                order = self.place_order(
                    symbol=symbol,
                    side=close_side,
                    stop_loss_pct=None,
                    leverage=None,
                    take_profit_pct=None,
                    qty=qty,
                    reduce_only=True,  # Используем reduceOnly для закрытия
                    max_attempts=5
                )

                if order is None:
                    # Проверяем, не была ли позиция закрыта
                    response = self.session.get_positions(category="linear", symbol=symbol)
                    if response["retCode"] == 0:
                        current_positions = [p for p in response["result"]["list"] if float(p["size"]) > 0 and p["side"] == pos["side"]]
                        if not current_positions:
                            logger.info(f"Позиция {pos['side']} для {symbol} была закрыта несмотря на ошибку ордера")
                            continue
                    logger.error(f"Не удалось разместить ордер для закрытия позиции {pos['side']} для {symbol}")
                    return False

                # Проверяем, закрыта ли позиция после ордера
                response = self.session.get_positions(category="linear", symbol=symbol)
                if response["retCode"] == 0:
                    current_positions = [p for p in response["result"]["list"] if float(p["size"]) > 0 and p["side"] == pos["side"]]
                    if not current_positions:
                        logger.info(f"Позиция {pos['side']} для {symbol} успешно закрыта, количество: {qty}")
                    else:
                        logger.error(f"Позиция {pos['side']} для {symbol} не полностью закрыта")
                        return False
                else:
                    logger.error(f"Ошибка при проверке статуса позиции после ордера: {response['retMsg']}")
                    return False

            return True

        except Exception as e:
            logger.error(f"Ошибка при закрытии позиции: {e}")
            return False

    def get_open_positions(self, symbols=None):
        """Получение списка открытых позиций для указанных символов или всех активных позиций."""
        try:
            params = {"category": "linear"}
            if symbols is None:
                params["settleCoin"] = "USDT"
                response = self.session.get_positions(**params)
            else:
                all_positions = []
                for symbol in symbols:
                    params["symbol"] = symbol
                    response = self.session.get_positions(**params)
                    if response["retCode"] == 0:
                        positions = response["result"]["list"]
                        active_positions = [pos for pos in positions if float(pos["size"]) > 0]
                        all_positions.extend(active_positions)
                    else:
                        logger.error(f"Ошибка при получении позиций для {symbol}: {response['retMsg']}")
                return all_positions

            if response["retCode"] == 0:
                positions = response["result"]["list"]
                active_positions = [pos for pos in positions if float(pos["size"]) > 0]
                return active_positions
            else:
                logger.error(f"Ошибка при получении позиций: {response['retMsg']}")
                return []
        except Exception as e:
            logger.error(f"Ошибка при получении позиций: {e}")
            return []

    def get_trade_history(self, symbols=None, limit=50, cursor=None):
        """Получение истории сделок для указанных символов."""
        if symbols is None:
            symbols = []

        all_trades = []
        for symbol in symbols:
            try:
                params = {"category": "linear", "symbol": symbol, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.session.get_order_history(**params)
                if response["retCode"] == 0:
                    trades = response["result"]["list"]
                    all_trades.extend(trades)
                else:
                    logger.error(f"Ошибка при получении истории сделок для {symbol}: {response['retMsg']}")
            except Exception as e:
                logger.error(f"Ошибка при получении истории сделок для {symbol}: {e}")
        return all_trades, None

    def get_closed_pnl(self, symbols=None, limit=50, cursor=None):
        """Получение истории закрытых PnL для указанных символов."""
        if symbols is None:
            symbols = []

        all_pnl = []
        for symbol in symbols:
            try:
                params = {"category": "linear", "symbol": symbol, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.session.get_closed_pnl(**params)
                if response["retCode"] == 0:
                    pnl_list = response["result"]["list"]
                    all_pnl.extend(pnl_list)
                else:
                    logger.error(f"Ошибка при получении закрытых PnL для {symbol}: {response['retMsg']}")
            except Exception as e:
                logger.error(f"Ошибка при получении закрытых PnL для {symbol}: {e}")
        return all_pnl, None

    def get_current_balance(self):
        """Получение текущего баланса в USDT."""
        try:
            wallet = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            if wallet["retCode"] == 0:
                for coin in wallet["result"]["list"][0]["coin"]:
                    if coin["coin"] == "USDT":
                        balance = float(coin["walletBalance"])
                        return balance
                return 0
            else:
                logger.error(f"Ошибка при получении баланса: {wallet['retMsg']}")
                return 0
        except Exception as e:
            logger.error(f"Ошибка при получении баланса: {e}")
            return 0

if __name__ == "__main__":
    trader = BybitTrader(
        api_key="YOUR_API_KEY",  # Замените на ваш API-ключ
        api_secret="YOUR_API_SECRET",  # Замените на ваш API-секрет
        testnet=True,  # Установите False для реальной торговли
        position_size_usd=10
    )

    # Пример 1: Открытие позиции с тейк-профитом
    trader.run(
        ticker="FARTCOINUSDT",
        side="buy",
        stop_loss=3,  # 3% стоп-лосс
        leverage=25,
        take_profit=5  # 5% тейк-профит
    )

    # Пример 2: Закрытие позиции
    success = trader.close_position("FARTCOINUSDT")
    if success:
        logger.info("Позиция для FARTCOINUSDT успешно закрыта")
    else:
        logger.error("Не удалось закрыть позицию для FARTCOINUSDT")

    # Пример 3: Получение открытых позиций
    logger.info("\n=== Открытые позиции ===")
    positions = trader.get_open_positions()
    if positions:
        for pos in positions:
            logger.info(f"Символ: {pos['symbol']}, Сторона: {pos['side']}, Размер: {pos['size']}, "
                        f"Цена входа: {pos['avgPrice']}, Нереализованный PnL: {pos['unrealisedPnl']}")
    else:
        logger.info("Нет открытых позиций.")

    # Пример 4: Получение истории сделок
    logger.info("\n=== История сделок ===")
    trades, _ = trader.get_trade_history(symbols=["FARTCOINUSDT"], limit=5)
    if trades:
        for trade in trades:
            logger.info(f"Символ: {trade['symbol']}, Сторона: {trade['side']}, Цена: {trade['price']}, "
                        f"Количество: {trade['qty']}, Время: {trade['createdTime']}")
    else:
        logger.info("История сделок пуста.")

    # Пример 5: Получение текущего баланса
    logger.info("\n=== Текущий баланс ===")
    balance = trader.get_current_balance()
    logger.info(f"Текущий баланс: {balance:.2f} USDT")