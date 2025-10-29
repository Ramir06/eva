import asyncio
import logging
from datetime import datetime, timedelta
from enum import Enum
import json
import os
import aiosqlite

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Конфигурация
class Config:
    BOT_TOKEN = "8267888504:AAFRtxWTqsNolzjQUMPqnFcHySVjK-g-T4M"  # Замените на ваш токен
    ADMIN_IDS = [6267550362]  # Замените на ваш ID
    DB_PATH = "eva_drive.db"
    SHIFT_CLOSE_TIME = "21:00"


# Enums
class UserRole(Enum):
    ADMIN = "admin"
    SENIOR_CASHIER = "senior_cashier"
    CASHIER = "cashier"



class WarningLevel(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


# States
class AdminStates(StatesGroup):
    waiting_for_cashier_id = State()
    waiting_for_senior_cashier_id = State()
    waiting_for_warning_reason = State()
    waiting_for_salary_amount = State()


class CashierStates(StatesGroup):
    waiting_for_order_customer = State()
    waiting_for_order_car = State()
    waiting_for_order_product = State()
    waiting_for_order_amount = State()


# Класс базы данных SQLite
# Исправленный класс базы данных SQLite
class Database:
    def __init__(self, db_path="eva_drive.db"):
        self.db_path = db_path
        self.conn = None

    async def connect(self):
        """Подключение к SQLite"""
        self.conn = await aiosqlite.connect(self.db_path)
        # Включаем поддержку row_factory для работы с dict
        self.conn.row_factory = aiosqlite.Row
        await self.init_tables()
        logger.info("✅ SQLite база данных подключена")

    async def init_tables(self):
        """Инициализация таблиц"""
        # Таблица пользователей
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')

        # Таблица замечаний
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                reason TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'normal',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by INTEGER REFERENCES users(id)
            )
        ''')

        # Таблица смен
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cashier_id INTEGER REFERENCES users(id),
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status TEXT DEFAULT 'open',
                total_orders INTEGER DEFAULT 0,
                total_amount REAL DEFAULT 0
            )
        ''')

        # Таблица заказов
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER REFERENCES shifts(id),
                customer_phone TEXT NOT NULL,
                car_brand TEXT NOT NULL,
                product TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Таблица зарплат
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS salaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                amount REAL NOT NULL,
                period_start DATE NOT NULL,
                period_end DATE NOT NULL,
                paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_by INTEGER REFERENCES users(id)
            )
        ''')

        await self.conn.commit()
        logger.info("✅ Таблицы созданы")

    # Вспомогательный метод для конвертации строк в dict
    async def _row_to_dict(self, row):
        """Конвертирует строку в словарь"""
        if row is None:
            return None
        return dict(row)

    async def _rows_to_dict(self, rows):
        """Конвертирует список строк в список словарей"""
        return [dict(row) for row in rows]

    # User methods
    async def create_user(self, user_id: int, username: str, full_name: str, role: str):
        await self.conn.execute('''
            INSERT OR REPLACE INTO users (id, username, full_name, role, is_active) 
            VALUES (?, ?, ?, ?, 1)
        ''', (user_id, username, full_name, role))
        await self.conn.commit()

    async def get_user(self, user_id: int):
        async with self.conn.execute('SELECT * FROM users WHERE id = ? AND is_active = 1', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_dict(row)

    async def get_users_by_role(self, role: str):
        async with self.conn.execute('SELECT * FROM users WHERE role = ? AND is_active = 1', (role,)) as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    async def get_all_active_users(self):
        async with self.conn.execute('SELECT * FROM users WHERE is_active = 1') as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    async def delete_user(self, user_id: int):
        await self.conn.execute('UPDATE users SET is_active = 0 WHERE id = ?', (user_id,))
        await self.conn.commit()

    # Warning methods
    async def add_warning(self, user_id: int, reason: str, level: str, created_by: int):
        await self.conn.execute('''
            INSERT INTO warnings (user_id, reason, level, created_by)
            VALUES (?, ?, ?, ?)
        ''', (user_id, reason, level, created_by))
        await self.conn.commit()

    async def get_user_warnings(self, user_id: int):
        async with self.conn.execute('''
            SELECT * FROM warnings 
            WHERE user_id = ? 
            ORDER BY created_at DESC
        ''', (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    async def get_all_warnings(self):
        async with self.conn.execute('SELECT * FROM warnings ORDER BY created_at DESC') as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    # Shift methods
    async def create_shift(self, cashier_id: int):
        cursor = await self.conn.execute('INSERT INTO shifts (cashier_id) VALUES (?)', (cashier_id,))
        await self.conn.commit()
        shift_id = cursor.lastrowid

        async with self.conn.execute('SELECT * FROM shifts WHERE id = ?', (shift_id,)) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_dict(row)

    async def close_shift(self, shift_id: int):
        await self.conn.execute('''
            UPDATE shifts 
            SET end_time = CURRENT_TIMESTAMP, status = 'closed' 
            WHERE id = ?
        ''', (shift_id,))
        await self.conn.commit()

    async def get_open_shift(self, cashier_id: int):
        async with self.conn.execute('''
            SELECT * FROM shifts 
            WHERE cashier_id = ? AND status = 'open'
        ''', (cashier_id,)) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_dict(row)

    async def get_all_shifts(self):
        async with self.conn.execute('''
            SELECT s.*, u.full_name as cashier_name 
            FROM shifts s 
            LEFT JOIN users u ON s.cashier_id = u.id 
            ORDER BY s.start_time DESC
        ''') as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    async def get_shift_by_id(self, shift_id: int):
        async with self.conn.execute('''
            SELECT s.*, u.full_name as cashier_name 
            FROM shifts s 
            LEFT JOIN users u ON s.cashier_id = u.id 
            WHERE s.id = ?
        ''', (shift_id,)) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_dict(row)

    # Order methods
    async def create_order(self, shift_id: int, customer_phone: str, car_brand: str, product: str, amount: float):
        # Создаем заказ
        cursor = await self.conn.execute('''
            INSERT INTO orders (shift_id, customer_phone, car_brand, product, amount)
            VALUES (?, ?, ?, ?, ?)
        ''', (shift_id, customer_phone, car_brand, product, amount))
        await self.conn.commit()
        order_id = cursor.lastrowid

        # Обновляем статистику смены
        await self.conn.execute('''
            UPDATE shifts 
            SET total_orders = total_orders + 1, 
                total_amount = total_amount + ?
            WHERE id = ?
        ''', (amount, shift_id))
        await self.conn.commit()

        async with self.conn.execute('SELECT * FROM orders WHERE id = ?', (order_id,)) as cursor:
            row = await cursor.fetchone()
            return await self._row_to_dict(row)

    async def get_orders_by_shift(self, shift_id: int):
        async with self.conn.execute('''
            SELECT * FROM orders 
            WHERE shift_id = ? 
            ORDER BY created_at DESC
        ''', (shift_id,)) as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    async def get_all_orders(self):
        async with self.conn.execute('''
            SELECT o.*, u.full_name as cashier_name, s.start_time as shift_start
            FROM orders o
            LEFT JOIN shifts s ON o.shift_id = s.id
            LEFT JOIN users u ON s.cashier_id = u.id
            ORDER BY o.created_at DESC
        ''') as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    # Salary methods
    async def add_salary(self, user_id: int, amount: float, period_start: str, period_end: str, paid_by: int):
        await self.conn.execute('''
            INSERT INTO salaries (user_id, amount, period_start, period_end, paid_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, amount, period_start, period_end, paid_by))
        await self.conn.commit()

    async def get_user_salaries(self, user_id: int):
        async with self.conn.execute('''
            SELECT * FROM salaries 
            WHERE user_id = ? 
            ORDER BY paid_at DESC
        ''', (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return await self._rows_to_dict(rows)

    async def close(self):
        """Закрытие соединения с базой данных"""
        if self.conn:
            await self.conn.close()
# Простой сервис для текстовых отчетов (вместо Excel)
class ReportService:
    @staticmethod
    def create_orders_report(orders_data):
        if not orders_data:
            return "📊 ОТЧЕТ ПО ЗАКАЗАМ\n\nНет данных о заказах"

        text = "📊 ОТЧЕТ ПО ЗАКАЗАМ\n\n"
        total_amount = 0

        for order in orders_data[:50]:  # Ограничиваем 50 заказами
            text += f"🆔 #{order['id']}\n"
            text += f"👤 Кассир: {order.get('cashier_name', 'Неизвестно')}\n"
            text += f"📞 Клиент: {order['customer_phone']}\n"
            text += f"🚗 Авто: {order['car_brand']}\n"
            text += f"📦 Товар: {order['product']}\n"
            text += f"💰 Сумма: {order['amount']} руб.\n"
            text += f"🕐 Дата: {order['created_at']}\n"
            text += "─" * 30 + "\n"
            total_amount += order['amount']

        text += f"\n💰 ОБЩАЯ СУММА: {total_amount} руб.\n"
        text += f"📦 ВСЕГО ЗАКАЗОВ: {len(orders_data)}"

        return text

    @staticmethod
    def create_shifts_report(shifts_data):
        if not shifts_data:
            return "🕐 ОТЧЕТ ПО СМЕНАМ\n\nНет данных о сменах"

        text = "🕐 ОТЧЕТ ПО СМЕНАМ\n\n"

        for shift in shifts_data[:20]:  # Ограничиваем 20 сменами
            status = "🔴 Открыта" if shift['status'] == 'open' else "🟢 Закрыта"
            text += f"🆔 #{shift['id']}\n"
            text += f"👤 Кассир: {shift.get('cashier_name', 'Неизвестно')}\n"
            text += f"📊 Статус: {status}\n"
            text += f"📦 Заказов: {shift['total_orders']}\n"
            text += f"💰 Сумма: {shift['total_amount']} руб.\n"
            text += f"🕐 Начало: {shift['start_time']}\n"
            if shift['end_time']:
                text += f"🕐 Конец: {shift['end_time']}\n"
            text += "─" * 30 + "\n"

        return text

    @staticmethod
    def create_employees_report(users_data, warnings_data):
        if not users_data:
            return "👥 ОТЧЕТ ПО СОТРУДНИКАМ\n\nНет данных о сотрудниках"

        text = "👥 ОТЧЕТ ПО СОТРУДНИКАМ\n\n"

        for user in users_data:
            warnings_count = len([w for w in warnings_data if w['user_id'] == user['id']])

            if warnings_count >= 5:
                status = "🔴 Критично"
            elif warnings_count >= 3:
                status = "⚠ Предупреждение"
            else:
                status = "🟡 Норма"

            text += f"👤 {user['full_name']}\n"
            text += f"🆔 ID: {user['id']}\n"
            text += f"🎯 Роль: {user['role']}\n"
            text += f"⚠ Замечаний: {warnings_count}\n"
            text += f"📊 Статус: {status}\n"
            text += "─" * 30 + "\n"

        return text


# Клавиатуры
class Keyboards:
    @staticmethod
    def main_admin_keyboard():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Управление персоналом", callback_data="admin_manage_staff")],
            [InlineKeyboardButton(text="📊 Отчеты", callback_data="admin_reports")],
            [InlineKeyboardButton(text="💰 Выдать зарплату", callback_data="admin_pay_salary")],
            [InlineKeyboardButton(text="⚠ Выдать замечание", callback_data="admin_give_warning")],
        ])

    @staticmethod
    def main_cashier_keyboard():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Начать смену", callback_data="cashier_start_shift")],
            [InlineKeyboardButton(text="📦 Создать заказ", callback_data="cashier_create_order")],
            [InlineKeyboardButton(text="🏁 Закрыть смену", callback_data="cashier_close_shift")],
            [InlineKeyboardButton(text="📈 Моя статистика", callback_data="cashier_my_stats")]
        ])

    @staticmethod
    def main_senior_cashier_keyboard():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👀 Просмотр заказов", callback_data="senior_view_orders")],
            [InlineKeyboardButton(text="📊 Смены кассиров", callback_data="senior_view_shifts")],
            [InlineKeyboardButton(text="🏁 Закрыть чужую смену", callback_data="senior_close_shift")]
        ])

    @staticmethod
    def staff_management_keyboard():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить кассира", callback_data="admin_add_cashier")],
            [InlineKeyboardButton(text="➕ Добавить старшего кассира", callback_data="admin_add_senior")],
            [InlineKeyboardButton(text="🗑 Удалить сотрудника", callback_data="admin_delete_staff")],
            [InlineKeyboardButton(text="📋 Список сотрудников", callback_data="admin_list_staff")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back_to_main")]
        ])

    @staticmethod
    def reports_keyboard():
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Отчет по заказам", callback_data="report_orders")],
            [InlineKeyboardButton(text="🕐 Отчет по сменам", callback_data="report_shifts")],
            [InlineKeyboardButton(text="👥 Отчет по сотрудникам", callback_data="report_employees")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back_to_main")]
        ])


# Основной класс бота
class EvaDriveBot:
    def __init__(self):
        self.bot = Bot(token=Config.BOT_TOKEN)
        self.storage = MemoryStorage()
        self.dp = Dispatcher(storage=self.storage)
        self.db = Database()
        self.report_service = ReportService()

        self.router = Router()
        self.dp.include_router(self.router)

        self.register_handlers()

    def register_handlers(self):
        # Команды
        self.router.message.register(self.start_command, CommandStart())
        self.router.message.register(self.admin_command, Command("admin"))

        # Админ handlers
        self.router.callback_query.register(self.admin_manage_staff, F.data == "admin_manage_staff")
        self.router.callback_query.register(self.admin_reports, F.data == "admin_reports")
        self.router.callback_query.register(self.admin_pay_salary, F.data == "admin_pay_salary")
        self.router.callback_query.register(self.admin_give_warning, F.data == "admin_give_warning")
        self.router.callback_query.register(self.admin_back_to_main, F.data == "admin_back_to_main")

        # Управление персоналом
        self.router.callback_query.register(self.admin_add_cashier, F.data == "admin_add_cashier")
        self.router.callback_query.register(self.admin_add_senior, F.data == "admin_add_senior")
        self.router.callback_query.register(self.admin_delete_staff, F.data == "admin_delete_staff")
        self.router.callback_query.register(self.admin_list_staff, F.data == "admin_list_staff")

        # Отчеты
        self.router.callback_query.register(self.report_orders, F.data == "report_orders")
        self.router.callback_query.register(self.report_shifts, F.data == "report_shifts")
        self.router.callback_query.register(self.report_employees, F.data == "report_employees")

        # Кассир handlers
        self.router.callback_query.register(self.cashier_start_shift, F.data == "cashier_start_shift")
        self.router.callback_query.register(self.cashier_create_order, F.data == "cashier_create_order")
        self.router.callback_query.register(self.cashier_close_shift, F.data == "cashier_close_shift")
        self.router.callback_query.register(self.cashier_my_stats, F.data == "cashier_my_stats")

        # Старший кассир handlers
        self.router.callback_query.register(self.senior_view_orders, F.data == "senior_view_orders")
        self.router.callback_query.register(self.senior_view_shifts, F.data == "senior_view_shifts")
        self.router.callback_query.register(self.senior_close_shift, F.data == "senior_close_shift")

        # Обработчики для динамических callback данных
        self.router.callback_query.register(self.process_delete_user, F.data.startswith("delete_user_"))
        self.router.callback_query.register(self.process_close_shift, F.data.startswith("close_shift_"))
        self.router.callback_query.register(self.process_give_warning, F.data.startswith("give_warning_"))
        self.router.callback_query.register(self.process_pay_salary, F.data.startswith("pay_salary_"))

        # State handlers
        self.router.message.register(self.process_cashier_id, AdminStates.waiting_for_cashier_id)
        self.router.message.register(self.process_senior_cashier_id, AdminStates.waiting_for_senior_cashier_id)
        self.router.message.register(self.process_warning_reason, AdminStates.waiting_for_warning_reason)
        self.router.message.register(self.process_salary_amount, AdminStates.waiting_for_salary_amount)

        self.router.message.register(self.process_order_customer, CashierStates.waiting_for_order_customer)
        self.router.message.register(self.process_order_car, CashierStates.waiting_for_order_car)
        self.router.message.register(self.process_order_product, CashierStates.waiting_for_order_product)
        self.router.message.register(self.process_order_amount, CashierStates.waiting_for_order_amount)

    async def start_command(self, message: Message):
        user_id = message.from_user.id
        username = message.from_user.username or ""
        full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()

        # Определяем роль пользователя
        user = await self.db.get_user(user_id)
        if user:
            role = user['role']
        else:
            # Если пользователь новый, проверяем админа
            if user_id in Config.ADMIN_IDS:
                role = UserRole.ADMIN.value
            else:
                role = UserRole.CASHIER.value

            await self.db.create_user(user_id, username, full_name, role)

        # Показываем соответствующую клавиатуру
        if role == UserRole.ADMIN.value:
            await message.answer(
                "👋 Добро пожаловать в панель администратора ЕВАПолики!",
                reply_markup=Keyboards.main_admin_keyboard()
            )
        elif role == UserRole.SENIOR_CASHIER.value:
            await message.answer(
                "👋 Добро пожаловать, старший кассир!",
                reply_markup=Keyboards.main_senior_cashier_keyboard()
            )
        else:
            await message.answer(
                "👋 Добро пожаловать, кассир!",
                reply_markup=Keyboards.main_cashier_keyboard()
            )

    async def admin_command(self, message: Message):
        if message.from_user.id not in Config.ADMIN_IDS:
            await message.answer("❌ У вас нет прав администратора!")
            return

        await message.answer(
            "👨‍💼 Панель администратора ЕВАПолики",
            reply_markup=Keyboards.main_admin_keyboard()
        )

    # Админ handlers
    async def admin_manage_staff(self, callback: CallbackQuery):
        await callback.message.edit_text(
            "👥 Управление персоналом",
            reply_markup=Keyboards.staff_management_keyboard()
        )

    async def admin_reports(self, callback: CallbackQuery):
        await callback.message.edit_text(
            "📊 Выберите тип отчета:",
            reply_markup=Keyboards.reports_keyboard()
        )

    async def admin_pay_salary(self, callback: CallbackQuery):
        cashiers = await self.db.get_users_by_role(UserRole.CASHIER.value)
        if not cashiers:
            await callback.message.edit_text("❌ Нет доступных кассиров для выплаты зарплаты")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for cashier in cashiers:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{cashier['full_name']} (ID: {cashier['id']})",
                    callback_data=f"pay_salary_{cashier['id']}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_staff")])

        await callback.message.edit_text(
            "💰 Выберите кассира для выплаты зарплаты:",
            reply_markup=keyboard
        )

    async def admin_give_warning(self, callback: CallbackQuery):
        users = await self.db.get_users_by_role(UserRole.CASHIER.value)
        senior_users = await self.db.get_users_by_role(UserRole.SENIOR_CASHIER.value)
        all_users = users + senior_users

        if not all_users:
            await callback.message.edit_text("❌ Нет доступных сотрудников")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for user in all_users:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{user['full_name']} (ID: {user['id']})",
                    callback_data=f"give_warning_{user['id']}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_staff")])

        await callback.message.edit_text(
            "⚠ Выберите сотрудника для выдачи замечания:",
            reply_markup=keyboard
        )

    async def admin_back_to_main(self, callback: CallbackQuery):
        await callback.message.edit_text(
            "👨‍💼 Панель администратора ЕВАПолики",
            reply_markup=Keyboards.main_admin_keyboard()
        )

    # Управление персоналом
    async def admin_add_cashier(self, callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_text(
            "📝 Введите ID пользователя Telegram для добавления кассира:"
        )
        await state.set_state(AdminStates.waiting_for_cashier_id)

    async def admin_add_senior(self, callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_text(
            "📝 Введите ID пользователя Telegram для добавления старшего кассира:"
        )
        await state.set_state(AdminStates.waiting_for_senior_cashier_id)

    async def process_cashier_id(self, message: Message, state: FSMContext):
        try:
            user_id = int(message.text)
            await self.db.create_user(user_id, "unknown", "Новый кассир", UserRole.CASHIER.value)
            await message.answer("✅ Кассир успешно добавлен!", reply_markup=Keyboards.main_admin_keyboard())
            await state.clear()
        except ValueError:
            await message.answer("❌ Неверный формат ID. Введите числовой ID:")

    async def process_senior_cashier_id(self, message: Message, state: FSMContext):
        try:
            user_id = int(message.text)
            await self.db.create_user(user_id, "unknown", "Новый старший кассир", UserRole.SENIOR_CASHIER.value)
            await message.answer("✅ Старший кассир успешно добавлен!", reply_markup=Keyboards.main_admin_keyboard())
            await state.clear()
        except ValueError:
            await message.answer("❌ Неверный формат ID. Введите числовой ID:")

    async def admin_delete_staff(self, callback: CallbackQuery):
        users = await self.db.get_users_by_role(UserRole.CASHIER.value)
        senior_users = await self.db.get_users_by_role(UserRole.SENIOR_CASHIER.value)
        all_users = users + senior_users

        if not all_users:
            await callback.message.edit_text("❌ Нет доступных сотрудников")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for user in all_users:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"{user['full_name']} ({user['role']}) - ID: {user['id']}",
                    callback_data=f"delete_user_{user['id']}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_manage_staff")])

        await callback.message.edit_text(
            "🗑 Выберите сотрудника для удаления:",
            reply_markup=keyboard
        )

    async def admin_list_staff(self, callback: CallbackQuery):
        users = await self.db.get_users_by_role(UserRole.CASHIER.value)
        senior_users = await self.db.get_users_by_role(UserRole.SENIOR_CASHIER.value)
        all_users = users + senior_users

        if not all_users:
            await callback.message.edit_text("❌ Нет сотрудников в системе")
            return

        text = "📋 Список сотрудников:\n\n"
        for user in all_users:
            warnings = await self.db.get_user_warnings(user['id'])
            warning_count = len(warnings)

            if warning_count >= 5:
                status = "🔴 Критично"
            elif warning_count >= 3:
                status = "⚠ Предупреждение"
            else:
                status = "🟡 Норма"

            text += f"👤 {user['full_name']}\n"
            text += f"🆔 ID: {user['id']}\n"
            text += f"🎯 Роль: {user['role']}\n"
            text += f"⚠ Замечания: {warning_count}\n"
            text += f"📊 Статус: {status}\n\n"

        await callback.message.edit_text(
            text,
            reply_markup=Keyboards.staff_management_keyboard()
        )

    # Отчеты
    async def report_orders(self, callback: CallbackQuery):
        orders = await self.db.get_all_orders()
        report_text = self.report_service.create_orders_report(orders)

        # Разбиваем на части если слишком длинный
        if len(report_text) > 4000:
            parts = [report_text[i:i + 4000] for i in range(0, len(report_text), 4000)]
            for part in parts:
                await callback.message.answer(part)
        else:
            await callback.message.answer(report_text)

    async def report_shifts(self, callback: CallbackQuery):
        shifts = await self.db.get_all_shifts()
        report_text = self.report_service.create_shifts_report(shifts)

        if len(report_text) > 4000:
            parts = [report_text[i:i + 4000] for i in range(0, len(report_text), 4000)]
            for part in parts:
                await callback.message.answer(part)
        else:
            await callback.message.answer(report_text)

    async def report_employees(self, callback: CallbackQuery):
        users = await self.db.get_all_active_users()
        warnings = await self.db.get_all_warnings()
        report_text = self.report_service.create_employees_report(users, warnings)

        if len(report_text) > 4000:
            parts = [report_text[i:i + 4000] for i in range(0, len(report_text), 4000)]
            for part in parts:
                await callback.message.answer(part)
        else:
            await callback.message.answer(report_text)

    # Кассир handlers
    async def cashier_start_shift(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        existing_shift = await self.db.get_open_shift(user_id)

        if existing_shift:
            await callback.message.edit_text("❌ У вас уже есть открытая смена!")
            return

        shift = await self.db.create_shift(user_id)
        await callback.message.edit_text(
            f"✅ Смена #{shift['id']} начата!\n"
            f"🕐 Время начала: {shift['start_time']}",
            reply_markup=Keyboards.main_cashier_keyboard()
        )

        # Уведомление админам
        for admin_id in Config.ADMIN_IDS:
            try:
                await self.bot.send_message(
                    admin_id,
                    f"🔔 Кассир {callback.from_user.full_name} начал смену #{shift['id']}"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

    async def cashier_create_order(self, callback: CallbackQuery, state: FSMContext):
        user_id = callback.from_user.id
        shift = await self.db.get_open_shift(user_id)

        if not shift:
            await callback.message.edit_text("❌ У вас нет открытой смены! Начните смену сначала.")
            return

        await callback.message.edit_text("📝 Введите номер телефона клиента:")
        await state.set_state(CashierStates.waiting_for_order_customer)
        await state.update_data(shift_id=shift['id'])

    async def process_order_customer(self, message: Message, state: FSMContext):
        await state.update_data(customer_phone=message.text)
        await message.answer("🚗 Введите марку машины:")
        await state.set_state(CashierStates.waiting_for_order_car)

    async def process_order_car(self, message: Message, state: FSMContext):
        await state.update_data(car_brand=message.text)
        await message.answer("📦 Введите наименование товара:")
        await state.set_state(CashierStates.waiting_for_order_product)

    async def process_order_product(self, message: Message, state: FSMContext):
        await state.update_data(product=message.text)
        await message.answer("💰 Введите сумму заказа:")
        await state.set_state(CashierStates.waiting_for_order_amount)

    async def process_order_amount(self, message: Message, state: FSMContext):
        try:
            amount = float(message.text)
            data = await state.get_data()

            order = await self.db.create_order(
                data['shift_id'],
                data['customer_phone'],
                data['car_brand'],
                data['product'],
                amount
            )

            await message.answer(
                f"✅ Заказ создан!\n"
                f"📞 Клиент: {data['customer_phone']}\n"
                f"🚗 Авто: {data['car_brand']}\n"
                f"📦 Товар: {data['product']}\n"
                f"💰 Сумма: {amount} руб.",
                reply_markup=Keyboards.main_cashier_keyboard()
            )

            # Уведомление админам
            for admin_id in Config.ADMIN_IDS:
                try:
                    await self.bot.send_message(
                        admin_id,
                        f"🛒 Новый заказ!\n"
                        f"Кассир: {message.from_user.full_name}\n"
                        f"Клиент: {data['customer_phone']}\n"
                        f"Сумма: {amount} руб."
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_id}: {e}")

            await state.clear()
        except ValueError:
            await message.answer("❌ Неверный формат суммы. Введите число:")

    async def cashier_close_shift(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        shift = await self.db.get_open_shift(user_id)

        if not shift:
            await callback.message.edit_text("❌ У вас нет открытой смены!")
            return

        await self.db.close_shift(shift['id'])
        orders = await self.db.get_orders_by_shift(shift['id'])

        text = f"✅ Смена #{shift['id']} закрыта!\n"
        text += f"📦 Заказов: {len(orders)}\n"
        text += f"💰 Общая сумма: {shift['total_amount']} руб.\n\n"
        text += "📋 Детали заказов:\n"

        for order in orders[:10]:  # Показываем первые 10 заказов
            text += f"• {order['product']} - {order['amount']} руб. ({order['customer_phone']})\n"

        if len(orders) > 10:
            text += f"... и еще {len(orders) - 10} заказов"

        await callback.message.edit_text(
            text,
            reply_markup=Keyboards.main_cashier_keyboard()
        )

    async def cashier_my_stats(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        shifts = await self.db.get_all_shifts()
        user_shifts = [s for s in shifts if s['cashier_id'] == user_id]
        warnings = await self.db.get_user_warnings(user_id)
        salaries = await self.db.get_user_salaries(user_id)

        text = f"📊 Статистика {callback.from_user.full_name}\n\n"
        text += f"🕐 Всего смен: {len(user_shifts)}\n"
        text += f"⚠ Замечаний: {len(warnings)}\n"
        text += f"💰 Выплат зарплат: {len(salaries)}\n\n"

        if warnings:
            text += "📝 Последние замечания:\n"
            for warning in warnings[:3]:
                text += f"• {warning['reason']} ({warning['level']})\n"

        if salaries:
            text += f"\n💵 Последняя зарплата: {salaries[0]['amount']} руб."

        await callback.message.edit_text(
            text,
            reply_markup=Keyboards.main_cashier_keyboard()
        )

    # Старший кассир handlers
    async def senior_view_orders(self, callback: CallbackQuery):
        orders = await self.db.get_all_orders()
        if not orders:
            await callback.message.edit_text("❌ Нет заказов в системе")
            return

        text = "📋 Последние заказы:\n\n"
        for order in orders[:10]:  # Показываем последние 10 заказов
            text += f"🆔 #{order['id']}\n"
            text += f"👤 Кассир: {order.get('cashier_name', 'Неизвестно')}\n"
            text += f"📞 Клиент: {order['customer_phone']}\n"
            text += f"🚗 Авто: {order['car_brand']}\n"
            text += f"📦 Товар: {order['product']}\n"
            text += f"💰 Сумма: {order['amount']} руб.\n"
            text += f"🕐 Время: {order['created_at']}\n\n"

        await callback.message.edit_text(
            text,
            reply_markup=Keyboards.main_senior_cashier_keyboard()
        )

    async def senior_view_shifts(self, callback: CallbackQuery):
        shifts = await self.db.get_all_shifts()
        if not shifts:
            await callback.message.edit_text("❌ Нет смен в системе")
            return

        text = "🕐 Последние смены:\n\n"
        for shift in shifts[:10]:  # Показываем последние 10 смен
            status = "🔴 Открыта" if shift['status'] == 'open' else "🟢 Закрыта"
            text += f"🆔 #{shift['id']}\n"
            text += f"👤 Кассир: {shift.get('cashier_name', 'Неизвестно')}\n"
            text += f"📊 Статус: {status}\n"
            text += f"📦 Заказов: {shift['total_orders']}\n"
            text += f"💰 Сумма: {shift['total_amount']} руб.\n"
            text += f"🕐 Начало: {shift['start_time']}\n"
            if shift['end_time']:
                text += f"🕐 Конец: {shift['end_time']}\n"
            text += "\n"

        await callback.message.edit_text(
            text,
            reply_markup=Keyboards.main_senior_cashier_keyboard()
        )

    async def senior_close_shift(self, callback: CallbackQuery):
        open_shifts = await self.db.get_all_shifts()
        open_shifts = [s for s in open_shifts if s['status'] == 'open']

        if not open_shifts:
            await callback.message.edit_text("❌ Нет открытых смен")
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for shift in open_shifts:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"Смена #{shift['id']} - {shift.get('cashier_name', 'Неизвестно')}",
                    callback_data=f"close_shift_{shift['id']}"
                )
            ])
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="senior_view_shifts")])

        await callback.message.edit_text(
            "🏁 Выберите смену для закрытия:",
            reply_markup=keyboard
        )

    # Обработчики callback данных
    async def process_delete_user(self, callback: CallbackQuery):
        user_id = int(callback.data.split('_')[-1])
        await self.db.delete_user(user_id)
        await callback.message.edit_text(
            "✅ Пользователь успешно удален!",
            reply_markup=Keyboards.staff_management_keyboard()
        )

    async def process_close_shift(self, callback: CallbackQuery):
        shift_id = int(callback.data.split('_')[-1])
        shift = await self.db.get_shift_by_id(shift_id)

        if not shift:
            await callback.message.edit_text("❌ Смена не найдена!")
            return

        await self.db.close_shift(shift_id)
        await callback.message.edit_text(
            f"✅ Смена #{shift_id} кассира {shift.get('cashier_name', 'Неизвестно')} закрыта!",
            reply_markup=Keyboards.main_senior_cashier_keyboard()
        )

    async def process_give_warning(self, callback: CallbackQuery, state: FSMContext):
        user_id = int(callback.data.split('_')[-1])
        await state.update_data(warning_user_id=user_id)
        await callback.message.edit_text("📝 Введите причину замечания:")
        await state.set_state(AdminStates.waiting_for_warning_reason)

    async def process_pay_salary(self, callback: CallbackQuery, state: FSMContext):
        user_id = int(callback.data.split('_')[-1])
        await state.update_data(salary_user_id=user_id)
        await callback.message.edit_text("💰 Введите сумму зарплаты:")
        await state.set_state(AdminStates.waiting_for_salary_amount)

    async def process_warning_reason(self, message: Message, state: FSMContext):
        data = await state.get_data()
        user_id = data['warning_user_id']

        await self.db.add_warning(
            user_id=user_id,
            reason=message.text,
            level=WarningLevel.WARNING.value,
            created_by=message.from_user.id
        )

        await message.answer(
            "✅ Замечание успешно выдано!",
            reply_markup=Keyboards.main_admin_keyboard()
        )

        # Уведомляем пользователя
        try:
            user = await self.db.get_user(user_id)
            if user:
                await self.bot.send_message(
                    user_id,
                    f"⚠ Вам выдано замечание!\nПричина: {message.text}"
                )
        except Exception as e:
            logger.error(f"Не удалось уведомить пользователя: {e}")

        await state.clear()

    async def process_salary_amount(self, message: Message, state: FSMContext):
        try:
            amount = float(message.text)
            data = await state.get_data()
            user_id = data['salary_user_id']

            # Устанавливаем период (последние 30 дней)
            period_end = datetime.now().date()
            period_start = period_end - timedelta(days=30)

            await self.db.add_salary(
                user_id=user_id,
                amount=amount,
                period_start=period_start.strftime('%Y-%m-%d'),
                period_end=period_end.strftime('%Y-%m-%d'),
                paid_by=message.from_user.id
            )

            await message.answer(
                f"✅ Зарплата {amount} руб. успешно выдана!",
                reply_markup=Keyboards.main_admin_keyboard()
            )

            # Уведомляем пользователя
            try:
                user = await self.db.get_user(user_id)
                if user:
                    await self.bot.send_message(
                        user_id,
                        f"💰 Вам выплачена зарплата!\nСумма: {amount} руб.\nПериод: {period_start} - {period_end}"
                    )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя: {e}")

            await state.clear()
        except ValueError:
            await message.answer("❌ Неверный формат суммы. Введите число:")

    async def start(self):
        """Запуск бота"""
        await self.db.connect()
        logger.info("Бот ЕВАПолики запущен!")
        await self.dp.start_polling(self.bot)


# Запуск бота
if __name__ == "__main__":
    bot = EvaDriveBot()

    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")
    finally:
        # Закрываем соединение с базой данных
        asyncio.run(bot.db.close())