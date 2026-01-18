import logging
import uuid
import qrcode
import aiohttp
import re
import aiohttp
import hashlib
import json
import base64
import asyncio
import os

from urllib.parse import urlencode
from hmac import compare_digest
from functools import wraps
from yookassa import Payment
from io import BytesIO
from datetime import datetime, timedelta
from aiosend import CryptoPay, TESTNET
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict

from pytonconnect import TonConnect
from pytonconnect.exceptions import UserRejectsError

from aiogram import Bot, Router, F, types, html
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatMemberStatus
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode

from shop_bot.bot import keyboards
from shop_bot.modules import xui_api
from shop_bot.data_manager.database import (
    get_user, add_new_key, get_user_keys, update_user_stats,
    register_user_if_not_exists, get_next_key_number, get_key_by_id,
    update_key_info, set_trial_used, set_terms_agreed, get_setting, get_all_hosts,
    get_plans_for_host, get_plan_by_id, log_transaction, get_referral_count, create_pending_transaction, get_all_users, grant_referral_bonus
)

from shop_bot.config import (
    get_profile_text, get_vpn_active_text, VPN_INACTIVE_TEXT, VPN_NO_DATA_TEXT,
    get_key_info_text, CHOOSE_PAYMENT_METHOD_MESSAGE, get_purchase_success_text
)

TELEGRAM_BOT_USERNAME = None
PAYMENT_METHODS = None
ADMIN_ID = None
CRYPTO_BOT_TOKEN = get_setting("cryptobot_token")

logger = logging.getLogger(__name__)
admin_router = Router()
user_router = Router()

class KeyPurchase(StatesGroup):
    waiting_for_host_selection = State()
    waiting_for_plan_selection = State()

class Onboarding(StatesGroup):
    waiting_for_subscription_and_agreement = State()

class PaymentProcess(StatesGroup):
    waiting_for_email = State()
    waiting_for_payment_method = State()

class Broadcast(StatesGroup):
    waiting_for_message = State()
    waiting_for_button_option = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_confirmation = State()

class WithdrawStates(StatesGroup):
    waiting_for_details = State()

def is_valid_email(email: str) -> bool:
    pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(pattern, email) is not None


async def show_main_menu(
    message: types.Message,
    edit_message: bool = False,
    with_photo: bool = False,
    delete_old: bool = True,   # 🔹 новое: удалять старое сообщение при возврате
):
    user_id = message.chat.id
    user_db_data = get_user(user_id)
    user_keys = get_user_keys(user_id)
    trial_available = not (user_db_data and user_db_data.get("trial_used"))
    is_admin = str(user_id) == ADMIN_ID

    text = "🏠 <b>Главное меню</b>\n\nВыберите действие:"
    keyboard = keyboards.create_main_menu_keyboard(user_keys, trial_available, is_admin)
    welcomekeyboard = keyboards.create_welcome_main_menu_keyboard(user_keys, trial_available, is_admin)

    # --- Если нужно отправить главное меню с фото (например, при /start)
    if with_photo:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        photo_path = os.path.join(base_dir, "media", "main_menu.jpg")

        if not os.path.exists(photo_path):
            print(f"[show_main_menu] ⚠️ Файл не найден: {photo_path}")
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=welcomekeyboard)
            return

        photo = types.FSInputFile(photo_path)
        await message.answer_photo(
            photo=photo,
            caption=text,
            parse_mode=ParseMode.HTML,
            reply_markup=welcomekeyboard,
        )
        return

    # --- Попытка отредактировать сообщение
    if edit_message:
        try:
            # если сообщение с фото — Telegram не даёт редактировать текст → удаляем
            if message.photo:
                await message.delete()
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                await message.edit_text(
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            return
        except TelegramBadRequest as e:
            # если сообщение уже удалено, просто отправляем новое
            if "message to edit not found" in str(e) or "message can't be edited" in str(e):
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            else:
                print(f"[show_main_menu] error while editing: {e}")
            return

    # --- Если редактирование не нужно — создаём новое
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

def registration_required(f):
    @wraps(f)
    async def decorated_function(event: types.Update, *args, **kwargs):
        user_id = event.from_user.id
        user_data = get_user(user_id)
        if user_data:
            return await f(event, *args, **kwargs)
        else:
            message_text = "Перед началом работы отправьте /start"
            if isinstance(event, types.CallbackQuery):
                await event.answer(message_text, show_alert=True)
            else:
                await event.answer(message_text)
    return decorated_function

def get_user_router() -> Router:

    user_router = Router()

##############################333

    @user_router.callback_query(F.data == "show_profile_photo")
    @registration_required
    async def show_profile_photo(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        user_id = callback.from_user.id
        user_db_data = get_user(user_id)
        user_keys = get_user_keys(user_id)
        if not user_db_data:
            await callback.answer("Не удалось получить данные профиля.", show_alert=True)
            return
        username = html.bold(user_db_data.get('username', 'Пользователь'))
        total_spent, total_months = user_db_data.get('total_spent', 0), user_db_data.get('total_months', 0)
        now = datetime.now()
        active_keys = [key for key in user_keys if datetime.fromisoformat(key['expiry_date']) > now]
        if active_keys:
            latest_key = max(active_keys, key=lambda k: datetime.fromisoformat(k['expiry_date']))
            latest_expiry_date = datetime.fromisoformat(latest_key['expiry_date'])
            time_left = latest_expiry_date - now
            vpn_status_text = get_vpn_active_text(time_left.days, time_left.seconds // 3600)
        elif user_keys: vpn_status_text = VPN_INACTIVE_TEXT
        else: vpn_status_text = VPN_NO_DATA_TEXT
        final_text = get_profile_text(username, total_spent, total_months, vpn_status_text)
        await callback.message.answer(final_text, reply_markup=keyboards.create_back_to_menu_keyboard())

    '''@user_router.callback_query(F.data == "get_trial_photo")
    @registration_required
    async def get_trial_photo(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await callback.message.delete()
        user_id = callback.from_user.id
        user_db_data = get_user(user_id)
        if user_db_data and user_db_data.get('trial_used'):
            await callback.answer("Вы уже использовали бесплатный пробный период.", show_alert=True)
            return

        hosts = get_all_hosts()
        if not hosts:
            await callback.message.answer("❌ В данный момент нет доступных серверов для создания пробного ключа.")
            return
            
        if len(hosts) == 1:
            await callback.answer()
            await process_trial_key_creation(callback.message, hosts[0]['host_name'])
        else:
            await callback.answer()
            await callback.message.answer(
                "Выберите сервер, на котором хотите получить пробный ключ:",
                reply_markup=keyboards.create_host_selection_keyboard(hosts, action="trial")
            )'''

    @user_router.callback_query(F.data == "manage_keys_photo")
    @registration_required
    async def manage_keys_photo(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        user_id = callback.from_user.id
        user_keys = get_user_keys(user_id)
        await callback.message.answer(
            "Ваши ключи:" if user_keys else "У вас пока нет ключей.",
            reply_markup=keyboards.create_keys_management_keyboard(user_keys)
        )

    @user_router.callback_query(F.data == "show_referral_program_photo")
    @registration_required
    async def referral_program_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        user_id = callback.from_user.id
        bot_username = (await callback.bot.get_me()).username
        
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        referral_count = get_referral_count(user_id)

        text = (
            "🤝 <b>Реферальная программа</b>\n\n"
            "Приглашайте друзей и получайте бонусные дни за каждое приглашение!\n\n"
            f"<b>Ваша реферальная ссылка:</b>\n<code>{referral_link}</code>\n\n"
            f"<b>Приглашено пользователей:</b> {referral_count}"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад", callback_data="back_to_main_menu")
        await callback.message.answer(
            text, reply_markup=builder.as_markup()
        )
    
    @user_router.callback_query(F.data == "show_help_photo")
    @registration_required
    async def show_help_photo(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        support_user = get_setting("support_user")
        support_text = get_setting("support_text")

        if support_user == None and support_text == None:
            await callback.message.answer(
                "Информация о поддержке не установлена. Установите её в админ-панели.",
                reply_markup=keyboards.create_back_to_menu_keyboard()
            )
        elif support_text == None:
            await callback.message.answer(
                "Для связи с поддержкой используйте кнопку ниже.",
                reply_markup=keyboards.create_support_keyboard(support_user)
            )
        else:
            await callback.message.answer(
                support_text + "\n\n",
                reply_markup=keyboards.create_support_keyboard(support_user)
            )

    @user_router.callback_query(F.data == "start_broadcast_photo")
    @registration_required
    async def start_broadcast_handler_photo(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await callback.message.delete()
        if str(callback.from_user.id) != ADMIN_ID:
            await callback.answer("У вас нет прав.", show_alert=True)
            return
        
        await callback.answer()
        await callback.message.answer(
            "Пришлите сообщение, которое вы хотите разослать всем пользователям.\n"
            "Вы можете использовать форматирование (<b>жирный</b>, <i>курсив</i>).\n"
            "Также поддерживаются фото, видео и документы.\n",
            reply_markup=keyboards.create_broadcast_cancel_keyboard()
        )
        await state.set_state(Broadcast.waiting_for_message)

    @user_router.callback_query(F.data == "show_about_photo")
    @registration_required
    async def show_about_photo(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        about_text = get_setting("about_text")
        terms_url = get_setting("terms_url")
        privacy_url = get_setting("privacy_url")
        channel_url = get_setting("channel_url")

        final_text = about_text if about_text else "Информация о проекте не добавлена."

        keyboard = keyboards.create_about_keyboard(channel_url, terms_url, privacy_url)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        photo_path = os.path.join(base_dir, "media", "main_menu.jpg")

        if not os.path.exists(photo_path):
            print(f"[show_main_menu] ⚠️ Файл не найден: {photo_path}")
            await callback.message.answer(final_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return

        photo = types.FSInputFile(photo_path)
        await callback.message.answer_photo(
            photo=photo,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        return
    
    @user_router.callback_query(F.data.startswith("select_host_new_"))
    @registration_required
    async def select_host_for_purchase_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()

        host_name = callback.data[len("select_host_new_"):]
        plans = get_plans_for_host(host_name)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        photo_path = os.path.join(base_dir, "media", "tariffs.jpg")
        photo = types.FSInputFile(photo_path)

        if not plans:
            await callback.message.answer_photo(
            photo=photo,
            caption=f"❌ Для сервера \"{host_name}\" не настроены тарифы.")
            return
        await callback.message.answer_photo(
            photo=photo,
            caption="Выберите тариф для нового ключа:", 
            reply_markup=keyboards.create_plans_keyboard(plans, action="new", host_name=host_name)
        )

    @user_router.callback_query(F.data == "buy_new_key")
    @registration_required
    async def buy_new_key_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        hosts = get_all_hosts()
        if not hosts:
            await callback.message.answer("❌ В данный момент нет доступных серверов для покупки.")
            return
        
        await callback.message.answer(
            "Выберите сервер, на котором хотите приобрести ключ:",
            reply_markup=keyboards.create_host_selection_keyboard(hosts, action="new")
        )

####################################3

    @user_router.message(CommandStart())
    async def start_handler(message: types.Message, state: FSMContext, bot: Bot, command: CommandObject):
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name
        referrer_id = None

        if command.args and command.args.startswith('ref_'):
            try:
                potential_referrer_id = int(command.args.split('_')[1])
                if potential_referrer_id != user_id:
                    referrer_id = potential_referrer_id
                    logger.info(f"New user {user_id} was referred by {referrer_id}")
            except (IndexError, ValueError):
                logger.warning(f"Invalid referral code received: {command.args}")
                
        register_user_if_not_exists(user_id, username, referrer_id)
        
        # Начисляем бонус рефереру, если новый пользователь пришёл по реферальной ссылке
        if referrer_id:
            try:
                await grant_referral_bonus(referrer_id)
                logger.info(f"Referral bonus granted to user {referrer_id} for new referral {user_id}")
            except Exception as e:
                logger.error(f"Error granting referral bonus to {referrer_id}: {e}", exc_info=True)
        
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.full_name
        user_data = get_user(user_id)

        if user_data and user_data.get('agreed_to_terms'):
            await message.answer(
                f"👋 Снова здравствуйте, {html.bold(message.from_user.full_name)}!",
                reply_markup=keyboards.main_reply_keyboard
            )
            await show_main_menu(message, with_photo=True)
            return

        terms_url = get_setting("terms_url")
        privacy_url = get_setting("privacy_url")
        channel_url = get_setting("channel_url")

        if not channel_url or not terms_url or not privacy_url:
            set_terms_agreed(user_id)
            await show_main_menu(message, with_photo=True)
            return

        is_subscription_forced = get_setting("force_subscription") == "true"
        
        show_welcome_screen = (is_subscription_forced and channel_url) or (terms_url and privacy_url)

        if not show_welcome_screen:
            set_terms_agreed(user_id)
            await show_main_menu(message, with_photo=True)
            return

        welcome_parts = ["<b>Добро пожаловать!</b>\n"]
        
        if is_subscription_forced and channel_url:
            welcome_parts.append("Для доступа ко всем функциям, пожалуйста, подпишитесь на наш канал.\n")
        

        welcome_parts.append("\nПосле этого нажмите кнопку ниже.")
        final_text = "\n".join(welcome_parts)
        
        await message.answer(
            final_text,
            reply_markup=keyboards.create_welcome_keyboard(
                channel_url=channel_url,
                is_subscription_forced=is_subscription_forced,
                terms_url=terms_url,
                privacy_url=privacy_url
            ),
            disable_web_page_preview=True
        )
        await state.set_state(Onboarding.waiting_for_subscription_and_agreement)

    @user_router.callback_query(Onboarding.waiting_for_subscription_and_agreement, F.data == "check_subscription_and_agree")
    async def check_subscription_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        user_id = callback.from_user.id
        channel_url = get_setting("channel_url")
        is_subscription_forced = get_setting("force_subscription") == "true"

        if not is_subscription_forced or not channel_url:
            await process_successful_onboarding(callback, state)
            return
            
        try:
            if '@' not in channel_url and 't.me/' not in channel_url:
                logger.error(f"Неверный формат URL канала: {channel_url}. Пропускаем проверку подписки.")
                await process_successful_onboarding(callback, state)
                return

            channel_id = '@' + channel_url.split('/')[-1] if 't.me/' in channel_url else channel_url
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            
            if member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                await process_successful_onboarding(callback, state)
            else:
                await callback.answer("Вы еще не подписались на канал. Пожалуйста, подпишитесь и попробуйте снова.", show_alert=True)

        except Exception as e:
            logger.error(f"Ошибка при проверке подписки для user_id {user_id} на канал {channel_url}: {e}")
            await callback.answer("Не удалось проверить подписку. Убедитесь, что бот является администратором канала. Попробуйте позже.", show_alert=True)

    @user_router.message(Onboarding.waiting_for_subscription_and_agreement)
    async def onboarding_fallback_handler(message: types.Message):
        await message.answer("Пожалуйста, выполните требуемые действия и нажмите на кнопку в сообщении выше.")

    @user_router.message(F.text == "🏠 Главное меню")
    @registration_required
    async def main_menu_handler(message: types.Message):
        await show_main_menu(message)

    @user_router.callback_query(F.data == "back_to_main_menu")
    @registration_required
    async def back_to_main_menu_handler(callback: types.CallbackQuery):
        await callback.answer()
        await show_main_menu(callback.message, edit_message=True)

    @user_router.callback_query(F.data == "show_profile")
    @registration_required
    async def profile_handler_callback(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        user_db_data = get_user(user_id)
        user_keys = get_user_keys(user_id)
        if not user_db_data:
            await callback.answer("Не удалось получить данные профиля.", show_alert=True)
            return
        username = html.bold(user_db_data.get('username', 'Пользователь'))
        total_spent, total_months = user_db_data.get('total_spent', 0), user_db_data.get('total_months', 0)
        now = datetime.now()
        active_keys = [key for key in user_keys if datetime.fromisoformat(key['expiry_date']) > now]
        if active_keys:
            latest_key = max(active_keys, key=lambda k: datetime.fromisoformat(k['expiry_date']))
            latest_expiry_date = datetime.fromisoformat(latest_key['expiry_date'])
            time_left = latest_expiry_date - now
            vpn_status_text = get_vpn_active_text(time_left.days, time_left.seconds // 3600)
        elif user_keys: vpn_status_text = VPN_INACTIVE_TEXT
        else: vpn_status_text = VPN_NO_DATA_TEXT
        final_text = get_profile_text(username, total_spent, total_months, vpn_status_text)
        await callback.message.edit_text(final_text, reply_markup=keyboards.create_back_to_menu_keyboard())

    @user_router.callback_query(F.data == "start_broadcast")
    @registration_required
    async def start_broadcast_handler(callback: types.CallbackQuery, state: FSMContext):
        if str(callback.from_user.id) != ADMIN_ID:
            await callback.answer("У вас нет прав.", show_alert=True)
            return
        
        await callback.answer()
        await callback.message.edit_text(
            "Пришлите сообщение, которое вы хотите разослать всем пользователям.\n"
            "Вы можете использовать форматирование (<b>жирный</b>, <i>курсив</i>).\n"
            "Также поддерживаются фото, видео и документы.\n",
            reply_markup=keyboards.create_broadcast_cancel_keyboard()
        )
        await state.set_state(Broadcast.waiting_for_message)

    @user_router.message(Broadcast.waiting_for_message)
    async def broadcast_message_received_handler(message: types.Message, state: FSMContext):
        await state.update_data(message_to_send=message.model_dump_json())
        
        await message.answer(
            "Сообщение получено. Хотите добавить к нему кнопку со ссылкой?",
            reply_markup=keyboards.create_broadcast_options_keyboard()
        )
        await state.set_state(Broadcast.waiting_for_button_option)

    @user_router.callback_query(Broadcast.waiting_for_button_option, F.data == "broadcast_add_button")
    async def add_button_prompt_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await callback.message.edit_text(
            "Хорошо. Теперь отправьте мне текст для кнопки.",
            reply_markup=keyboards.create_broadcast_cancel_keyboard()
        )
        await state.set_state(Broadcast.waiting_for_button_text)

    @user_router.message(Broadcast.waiting_for_button_text)
    async def button_text_received_handler(message: types.Message, state: FSMContext):
        await state.update_data(button_text=message.text)
        await message.answer(
            "Текст кнопки получен. Теперь отправьте ссылку (URL), куда она будет вести.",
            reply_markup=keyboards.create_broadcast_cancel_keyboard()
        )
        await state.set_state(Broadcast.waiting_for_button_url)

    @user_router.message(Broadcast.waiting_for_button_url)
    async def button_url_received_handler(message: types.Message, state: FSMContext, bot: Bot):
        url_to_check = message.text

        is_valid = await is_url_reachable(url_to_check)
        
        if not is_valid:
            await message.answer(
                "❌ **Ссылка не прошла проверку.**\n\n"
                "Пожалуйста, убедитесь, что:\n"
                "1. Ссылка начинается с `http://` или `https://`.\n"
                "2. Доменное имя корректно (например, `example.com`).\n"
                "3. Сайт доступен в данный момент.\n\n"
                "Попробуйте еще раз."
            )
            return

        await state.update_data(button_url=url_to_check)
        await show_broadcast_preview(message, state, bot)

    @user_router.callback_query(Broadcast.waiting_for_button_option, F.data == "broadcast_skip_button")
    async def skip_button_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        await callback.answer()
        await state.update_data(button_text=None, button_url=None)
        await show_broadcast_preview(callback.message, state, bot)

    async def show_broadcast_preview(message: types.Message, state: FSMContext, bot: Bot):
        data = await state.get_data()
        message_json = data.get('message_to_send')
        original_message = types.Message.model_validate_json(message_json)
        
        button_text = data.get('button_text')
        button_url = data.get('button_url')
        
        preview_keyboard = None
        if button_text and button_url:
            builder = InlineKeyboardBuilder()
            builder.button(text=button_text, url=button_url)
            preview_keyboard = builder.as_markup()

        await message.answer(
            "Вот так будет выглядеть ваше сообщение. Отправляем?",
            reply_markup=keyboards.create_broadcast_confirmation_keyboard()
        )
        
        await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=original_message.chat.id,
            message_id=original_message.message_id,
            reply_markup=preview_keyboard
        )

        await state.set_state(Broadcast.waiting_for_confirmation)

    @user_router.callback_query(Broadcast.waiting_for_confirmation, F.data == "confirm_broadcast")
    async def confirm_broadcast_handler(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
        await callback.message.edit_text("⏳ Начинаю рассылку... Это может занять некоторое время.")
        
        data = await state.get_data()
        message_json = data.get('message_to_send')
        original_message = types.Message.model_validate_json(message_json)
        
        button_text = data.get('button_text')
        button_url = data.get('button_url')
        
        final_keyboard = None
        if button_text and button_url:
            builder = InlineKeyboardBuilder()
            builder.button(text=button_text, url=button_url)
            final_keyboard = builder.as_markup()

        await state.clear()
        
        users = get_all_users()
        logger.info(f"Broadcast: Starting to iterate over {len(users)} users.")

        sent_count = 0
        failed_count = 0
        banned_count = 0

        for user in users:
            user_id = user['telegram_id']
            if user.get('is_banned'):
                banned_count += 1
                continue
            
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=original_message.chat.id,
                    message_id=original_message.message_id,
                    reply_markup=final_keyboard
                )

                sent_count += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                failed_count += 1
                logger.warning(f"Failed to send broadcast message to user {user_id}: {e}")
        
        await callback.message.answer(
            f"✅ Рассылка завершена!\n\n"
            f"👍 Отправлено: {sent_count}\n"
            f"👎 Не удалось отправить: {failed_count}\n"
            f"🚫 Пропущено (забанены): {banned_count}"
        )
        await show_main_menu(callback.message)

    @user_router.callback_query(StateFilter(Broadcast), F.data == "cancel_broadcast")
    async def cancel_broadcast_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Рассылка отменена.")
        await state.clear()
        await show_main_menu(callback.message, edit_message=True)

    @user_router.callback_query(F.data == "show_referral_program")
    @registration_required
    async def referral_program_handler(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        bot_username = (await callback.bot.get_me()).username
        
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        referral_count = get_referral_count(user_id)

        text = (
            "🤝 <b>Реферальная программа</b>\n\n"
            "Приглашайте друзей и получайте бонусные дни за каждое приглашение!\n\n"
            f"<b>Ваша реферальная ссылка:</b>\n<code>{referral_link}</code>\n\n"
            f"<b>Приглашено пользователей:</b> {referral_count}"
        )

        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Назад", callback_data="back_to_main_menu")
        await callback.message.edit_text(
            text, reply_markup=builder.as_markup()
        )

    @user_router.callback_query(F.data == "show_about")
    @registration_required
    async def about_handler(callback: types.CallbackQuery):
        await callback.answer()
        
        about_text = get_setting("about_text")
        terms_url = get_setting("terms_url")
        privacy_url = get_setting("privacy_url")
        channel_url = get_setting("channel_url")

        final_text = about_text if about_text else "Информация о проекте не добавлена."

        keyboard = keyboards.create_about_keyboard(channel_url, terms_url, privacy_url)

        await callback.message.edit_text(
            final_text,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )

    @user_router.callback_query(F.data == "show_help")
    @registration_required
    async def about_handler(callback: types.CallbackQuery):
        await callback.answer()

        support_user = get_setting("support_user")
        support_text = get_setting("support_text")

        if support_user == None and support_text == None:
            await callback.message.edit_text(
                "Информация о поддержке не установлена. Установите её в админ-панели.",
                reply_markup=keyboards.create_back_to_menu_keyboard()
            )
        elif support_text == None:
            await callback.message.edit_text(
                "Для связи с поддержкой используйте кнопку ниже.",
                reply_markup=keyboards.create_support_keyboard(support_user)
            )
        else:
            await callback.message.edit_text(
                support_text + "\n\n",
                reply_markup=keyboards.create_support_keyboard(support_user)
            )

    @user_router.callback_query(F.data == "manage_keys")
    @registration_required
    async def manage_keys_handler(callback: types.CallbackQuery):
        await callback.answer()
        user_id = callback.from_user.id
        user_keys = get_user_keys(user_id)
        await callback.message.edit_text(
            "Ваши ключи:" if user_keys else "У вас пока нет ключей.",
            reply_markup=keyboards.create_keys_management_keyboard(user_keys)
        )

    @user_router.callback_query(F.data == "get_trial")
    @registration_required
    async def trial_period_handler(callback: types.CallbackQuery, state: FSMContext):
        user_id = callback.from_user.id
        user_db_data = get_user(user_id)
        if user_db_data and user_db_data.get('trial_used'):
            await callback.message.delete()
            await callback.answer("Вы уже использовали бесплатный пробный период.", show_alert=True)
            return

        hosts = get_all_hosts()
        if not hosts:
            await callback.message.answer("❌ В данный момент нет доступных серверов для создания пробного ключа.")
            return
            
        if len(hosts) == 1:
            await callback.answer()
            await process_trial_key_creation(callback.message, hosts[0]['host_name'])
        else:
            await callback.answer()
            await callback.message.answer(
                "Выберите сервер, на котором хотите получить пробный ключ:",
                reply_markup=keyboards.create_host_selection_keyboard(hosts, action="trial")
            )

    @user_router.callback_query(F.data.startswith("select_host_trial_"))
    @registration_required
    async def trial_host_selection_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        host_name = callback.data[len("select_host_trial_"):]
        await process_trial_key_creation(callback.message, host_name)

    async def process_trial_key_creation(message: types.Message, host_name: str):
        user_id = message.chat.id
        trial_days = int(get_setting('trial_duration_days'))
        await message.answer(f"Отлично! Создаю для вас бесплатный ключ на {trial_days} дня на сервере \"{host_name}\"...")

        try:
            # Добавляем логирование перед вызовом API
            logger.info(f"DEBUG: Calling xui_api with days_to_add = {trial_days}")
            
            result = await xui_api.create_or_update_key_on_host(
                host_name=host_name,
                email=f"user{user_id}-key{get_next_key_number(user_id)}-trial@telegram.bot",
                days_to_add=trial_days
            )
            
            # Добавляем логирование после вызова API
            if result:
                logger.info(f"DEBUG: xui_api returned expiry_timestamp_ms = {result['expiry_timestamp_ms']}")
            else:
                logger.info(f"DEBUG: xui_api returned None or invalid result")
                await message.answer("❌ Не удалось создать пробный ключ. Ошибка на сервере.")
                return

            set_trial_used(user_id)
            
            new_key_id = add_new_key(
                user_id=user_id,
                email=f"user_{user_id}@trial.com",
                host_name=host_name,
                xui_client_uuid=result['client_uuid'],
                days=trial_days,
                key_email=result['email'],
                expiry_timestamp_ms=result['expiry_timestamp_ms']
            )
            
            # Удаляем сообщение только после успешного создания ключа и его сохранения
            try:
                await message.delete()
            except Exception as delete_error:
                logger.warning(f"Could not delete message for user {user_id}: {delete_error}")
            
            new_expiry_date = datetime.fromtimestamp(result['expiry_timestamp_ms'] / 1000)
            # Добавляем логирование вычисленной даты
            logger.info(f"DEBUG: Calculated expiry date is {new_expiry_date.isoformat()}")
            
            final_text = get_purchase_success_text("готов", get_next_key_number(user_id) -1, new_expiry_date, result['connection_string'])
            await message.answer(text=final_text, reply_markup=keyboards.create_key_info_keyboard(new_key_id))

        except Exception as e:
            logger.error(f"Error creating trial key for user {user_id} on host {host_name}: {e}", exc_info=True)
            try:
                await message.answer("❌ Произошла ошибка при создании пробного ключа.")
            except Exception as msg_error:
                logger.error(f"Could not send error message to user {user_id}: {msg_error}")

    @user_router.callback_query(F.data.startswith("show_key_"))
    @registration_required
    async def show_key_handler(callback: types.CallbackQuery):
        key_id_to_show = int(callback.data.split("_")[2])
        await callback.message.edit_text("Загружаю информацию о ключе...")
        user_id = callback.from_user.id
        key_data = get_key_by_id(key_id_to_show)

        if not key_data or key_data['user_id'] != user_id:
            await callback.message.edit_text("❌ Ошибка: ключ не найден.")
            return
            
        try:
            details = await xui_api.get_key_details_from_host(key_data)
            if not details or not details['connection_string']:
                await callback.message.edit_text("❌ Ошибка на сервере. Не удалось получить данные ключа.")
                return

            connection_string = details['connection_string']
            expiry_date = datetime.fromisoformat(key_data['expiry_date'])
            created_date = datetime.fromisoformat(key_data['created_date'])
            
            all_user_keys = get_user_keys(user_id)
            key_number = next((i + 1 for i, key in enumerate(all_user_keys) if key['key_id'] == key_id_to_show), 0)
            
            final_text = get_key_info_text(key_number, expiry_date, created_date, connection_string)
            
            await callback.message.edit_text(
                text=final_text,
                reply_markup=keyboards.create_key_info_keyboard(key_id_to_show)
            )
        except Exception as e:
            logger.error(f"Error showing key {key_id_to_show}: {e}")
            await callback.message.edit_text("❌ Произошла ошибка при получении данных ключа.")


    @user_router.callback_query(F.data.startswith("show_qr_"))
    @registration_required
    async def show_qr_handler(callback: types.CallbackQuery):
        await callback.answer("Генерирую QR-код...")
        key_id = int(callback.data.split("_")[2])
        key_data = get_key_by_id(key_id)
        if not key_data or key_data['user_id'] != callback.from_user.id: return
        
        try:
            details = await xui_api.get_key_details_from_host(key_data)
            if not details or not details['connection_string']:
                await callback.answer("Ошибка: Не удалось сгенерировать QR-код.", show_alert=True)
                return

            connection_string = details['connection_string']
            qr_img = qrcode.make(connection_string)
            bio = BytesIO(); qr_img.save(bio, "PNG"); bio.seek(0)
            qr_code_file = BufferedInputFile(bio.read(), filename="vpn_qr.png")
            await callback.message.answer_photo(photo=qr_code_file)
        except Exception as e:
            logger.error(f"Error showing QR for key {key_id}: {e}")

    @user_router.callback_query(F.data.startswith("howto_vless_"))
    @registration_required
    async def show_instruction_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        key_id = int(callback.data.split("_")[2])

        await callback.message.answer(
            "Выберите вашу платформу для инструкции по подключению VLESS:",
            reply_markup=keyboards.create_howto_vless_keyboard_key(key_id),
            disable_web_page_preview=True
        )
    
    @user_router.callback_query(F.data.startswith("howto_vless"))
    @registration_required
    async def show_instruction_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.delete()
        await callback.message.answer(
            "Выберите вашу платформу для инструкции по подключению VLESS:",
            reply_markup=keyboards.create_howto_vless_keyboard(),
            disable_web_page_preview=True
        )

    @user_router.callback_query(F.data == "howto_android")
    @registration_required
    async def howto_android_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на Android</b>\n\n"
            "1. <b>Установите приложение V2RayTun:</b> Загрузите и установите приложение V2RayTun из Google Play Store.\n"
            "2. <b>Скопируйте свой ключ (vless://)</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "3. <b>Импортируйте конфигурацию:</b>\n"
            "   • Откройте V2RayTun.\n"
            "   • Нажмите на значок + в правом нижнем углу.\n"
            "   • Выберите «Импортировать конфигурацию из буфера обмена» (или аналогичный пункт).\n"
            "4. <b>Выберите сервер:</b> Выберите появившийся сервер в списке.\n"
            "5. <b>Подключитесь к VPN:</b> Нажмите на кнопку подключения (значок «V» или воспроизведения). Возможно, потребуется разрешение на создание VPN-подключения.\n"
            "6. <b>Проверьте подключение:</b> После подключения проверьте свой IP-адрес, например, на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    @user_router.callback_query(F.data == "howto_ios")
    @registration_required
    async def howto_ios_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на iOS (iPhone/iPad)</b>\n\n"
            "1. <b>Установите приложение V2RayTun:</b> Загрузите и установите приложение V2RayTun из App Store.\n"
            "2. <b>Скопируйте свой ключ (vless://):</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "3. <b>Импортируйте конфигурацию:</b>\n"
            "   • Откройте V2RayTun.\n"
            "   • Нажмите на значок +.\n"
            "   • Выберите «Импортировать конфигурацию из буфера обмена» (или аналогичный пункт).\n"
            "4. <b>Выберите сервер:</b> Выберите появившийся сервер в списке.\n"
            "5. <b>Подключитесь к VPN:</b> Включите главный переключатель в V2RayTun. Возможно, потребуется разрешить создание VPN-подключения.\n"
            "6. <b>Проверьте подключение:</b> После подключения проверьте свой IP-адрес, например, на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    @user_router.callback_query(F.data == "howto_windows")
    @registration_required
    async def howto_windows_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на Windows</b>\n\n"
            "1. <b>Установите приложение Nekoray:</b> Загрузите Nekoray с https://github.com/MatsuriDayo/Nekoray/releases. Выберите подходящую версию (например, Nekoray-x64.exe).\n"
            "2. <b>Распакуйте архив:</b> Распакуйте скачанный архив в удобное место.\n"
            "3. <b>Запустите Nekoray.exe:</b> Откройте исполняемый файл.\n"
            "4. <b>Скопируйте свой ключ (vless://)</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "5. <b>Импортируйте конфигурацию:</b>\n"
            "   • В Nekoray нажмите «Сервер» (Server).\n"
            "   • Выберите «Импортировать из буфера обмена».\n"
            "   • Nekoray автоматически импортирует конфигурацию.\n"
            "6. <b>Обновите серверы (если нужно):</b> Если серверы не появились, нажмите «Серверы» → «Обновить все серверы».\n"
            "7. Сверху включите пункт 'Режим TUN' ('Tun Mode')\n"
            "8. <b>Выберите сервер:</b> В главном окне выберите появившийся сервер.\n"
            "9. <b>Подключитесь к VPN:</b> Нажмите «Подключить» (Connect).\n"
            "10. <b>Проверьте подключение:</b> Откройте браузер и проверьте IP на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    @user_router.callback_query(F.data == "howto_linux")
    @registration_required
    async def howto_linux_handler(callback: types.CallbackQuery):
        await callback.answer()
        await callback.message.edit_text(
            "<b>Подключение на Linux</b>\n\n"
            "1. <b>Скачайте и распакуйте Nekoray:</b> Перейдите на https://github.com/MatsuriDayo/Nekoray/releases и скачайте архив для Linux. Распакуйте его в удобную папку.\n"
            "2. <b>Запустите Nekoray:</b> Откройте терминал, перейдите в папку с Nekoray и выполните <code>./nekoray</code> (или используйте графический запуск, если доступен).\n"
            "3. <b>Скопируйте свой ключ (vless://)</b> Перейдите в раздел «Моя подписка» в нашем боте и скопируйте свой ключ.\n"
            "4. <b>Импортируйте конфигурацию:</b>\n"
            "   • В Nekoray нажмите «Сервер» (Server).\n"
            "   • Выберите «Импортировать из буфера обмена».\n"
            "   • Nekoray автоматически импортирует конфигурацию.\n"
            "5. <b>Обновите серверы (если нужно):</b> Если серверы не появились, нажмите «Серверы» → «Обновить все серверы».\n"
            "6. Сверху включите пункт 'Режим TUN' ('Tun Mode')\n"
            "7. <b>Выберите сервер:</b> В главном окне выберите появившийся сервер.\n"
            "8. <b>Подключитесь к VPN:</b> Нажмите «Подключить» (Connect).\n"
            "9. <b>Проверьте подключение:</b> Откройте браузер и проверьте IP на https://whatismyipaddress.com/. Он должен отличаться от вашего реального IP.",
        reply_markup=keyboards.create_howto_vless_keyboard(),
        disable_web_page_preview=True
    )

    '''@user_router.callback_query(F.data == "buy_new_key")
    @registration_required
    async def buy_new_key_handler(callback: types.CallbackQuery):
        await callback.answer()
        hosts = get_all_hosts()
        if not hosts:
            await callback.message.edit_text("❌ В данный момент нет доступных серверов для покупки.")
            return
        
        await callback.message.edit_text(
            "Выберите сервер, на котором хотите приобрести ключ:",
            reply_markup=keyboards.create_host_selection_keyboard(hosts, action="new")
        )

    @user_router.callback_query(F.data.startswith("select_host_new_"))
    @registration_required
    async def select_host_for_purchase_handler(callback: types.CallbackQuery):
        await callback.answer()
        host_name = callback.data[len("select_host_new_"):]
        plans = get_plans_for_host(host_name)
        if not plans:
            await callback.message.edit_text(f"❌ Для сервера \"{host_name}\" не настроены тарифы.")
            return
        await callback.message.edit_text(
            "Выберите тариф для нового ключа:", 
            reply_markup=keyboards.create_plans_keyboard(plans, action="new", host_name=host_name)
        )'''

    @user_router.callback_query(F.data.startswith("extend_key_"))
    @registration_required
    async def extend_key_handler(callback: types.CallbackQuery):
        await callback.answer()

        try:
            key_id = int(callback.data.split("_")[2])
        except (IndexError, ValueError):
            await callback.message.edit_text("❌ Произошла ошибка. Неверный формат ключа.")
            return

        key_data = get_key_by_id(key_id)

        if not key_data or key_data['user_id'] != callback.from_user.id:
            await callback.message.edit_text("❌ Ошибка: Ключ не найден или не принадлежит вам.")
            return
        
        host_name = key_data.get('host_name')
        if not host_name:
            await callback.message.edit_text("❌ Ошибка: У этого ключа не указан сервер. Обратитесь в поддержку.")
            return

        plans = get_plans_for_host(host_name)

        if not plans:
            await callback.message.edit_text(
                f"❌ Извините, для сервера \"{host_name}\" в данный момент не настроены тарифы для продления."
            )
            return

        await callback.message.edit_text(
            f"Выберите тариф для продления ключа на сервере \"{host_name}\":",
            reply_markup=keyboards.create_plans_keyboard(
                plans=plans,
                action="extend",
                host_name=host_name,
                key_id=key_id
            )
        )

    @user_router.callback_query(F.data.startswith("buy_"))
    @registration_required
    async def plan_selection_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await callback.message.delete()
        
        parts = callback.data.split("_")[1:]
        action = parts[-2]
        key_id = int(parts[-1])
        plan_id = int(parts[-3])
        host_name = "_".join(parts[:-3])

        await state.update_data(
            action=action, key_id=key_id, plan_id=plan_id, host_name=host_name
        )
        
        await callback.message.answer(
            "📧 Пожалуйста, введите ваш email для отправки чека об оплате.\n\n"
            "Если вы не хотите указывать почту, нажмите кнопку ниже.",
            reply_markup=keyboards.create_skip_email_keyboard()
        )
        await state.set_state(PaymentProcess.waiting_for_email)

    @user_router.callback_query(PaymentProcess.waiting_for_email, F.data == "back_to_plans")
    async def back_to_plans_handler(callback: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        
        action = data.get('action')

        if action == 'new':
            await buy_new_key_handler(callback)
        elif action == 'extend':
            await extend_key_handler(callback)
        else:
            await back_to_main_menu_handler(callback)

    @user_router.message(PaymentProcess.waiting_for_email)
    async def process_email_handler(message: types.Message, state: FSMContext):
        if is_valid_email(message.text):
            await state.update_data(customer_email=message.text)
            await message.answer(f"✅ Email принят: {message.text}")

            data = await state.get_data()
            await message.answer(
                CHOOSE_PAYMENT_METHOD_MESSAGE,
                reply_markup=keyboards.create_payment_method_keyboard(
                    payment_methods=PAYMENT_METHODS,
                    action=data.get('action'),
                    key_id=data.get('key_id')
                )
            )
            await state.set_state(PaymentProcess.waiting_for_payment_method)
            logger.info(f"User {message.chat.id}: State set to waiting_for_payment_method")
        else:
            await message.answer("❌ Неверный формат email. Попробуйте еще раз.")

    @user_router.callback_query(PaymentProcess.waiting_for_email, F.data == "skip_email")
    async def skip_email_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer()
        await state.update_data(customer_email=None)

        data = await state.get_data()
        await callback.message.edit_text(
            CHOOSE_PAYMENT_METHOD_MESSAGE,
            reply_markup=keyboards.create_payment_method_keyboard(
                payment_methods=PAYMENT_METHODS,
                action=data.get('action'),
                key_id=data.get('key_id')
            )
        )
        await state.set_state(PaymentProcess.waiting_for_payment_method)
        logger.info(f"User {callback.from_user.id}: State set to waiting_for_payment_method")

    async def show_payment_options(message: types.Message, state: FSMContext):
        data = await state.get_data()
        user_data = get_user(message.chat.id)
        plan = get_plan_by_id(data.get('plan_id'))
        
        if not plan:
            await message.edit_text("❌ Ошибка: Тариф не найден.")
            await state.clear()
            return

        price = Decimal(str(plan['price']))
        final_price = price
        discount_applied = False
        message_text = CHOOSE_PAYMENT_METHOD_MESSAGE


        await message.edit_text(
            message_text,
            reply_markup=keyboards.create_payment_method_keyboard(
                payment_methods=PAYMENT_METHODS,
                action=data.get('action'),
                key_id=data.get('key_id')
            )
        )
        await state.set_state(PaymentProcess.waiting_for_payment_method)
        
    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "back_to_email_prompt")
    async def back_to_email_prompt_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.message.edit_text(
            "📧 Пожалуйста, введите ваш email для отправки чека об оплате.\n\n"
            "Если вы не хотите указывать почту, нажмите кнопку ниже.",
            reply_markup=keyboards.create_skip_email_keyboard()
        )
        await state.set_state(PaymentProcess.waiting_for_email)


    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_yookassa")
    async def create_yookassa_payment_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю ссылку на оплату...")
        
        data = await state.get_data()
        user_data = get_user(callback.from_user.id)
        
        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)

        if not plan:
            await callback.message.answer("Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        base_price = Decimal(str(plan['price']))
        price_rub = base_price

        if user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)
            if discount_percentage > 0:
                discount_amount = (base_price * discount_percentage / 100).quantize(Decimal("0.01"))
                price_rub = base_price - discount_amount

        plan_id = data.get('plan_id')
        customer_email = data.get('customer_email')
        host_name = data.get('host_name')
        action = data.get('action')
        key_id = data.get('key_id')
        
        if not customer_email:
            customer_email = get_setting("receipt_email")

        plan = get_plan_by_id(plan_id)
        if not plan:
            await callback.message.answer("Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        months = plan['months']
        user_id = callback.from_user.id

        try:
            price_str_for_api = f"{price_rub:.2f}"
            price_float_for_metadata = float(price_rub)

            receipt = None
            if customer_email and is_valid_email(customer_email):
                receipt = {
                    "customer": {"email": customer_email},
                    "items": [{
                        "description": f"Подписка на {months} мес.",
                        "quantity": "1.00",
                        "amount": {"value": price_str_for_api, "currency": "RUB"},
                        "vat_code": "1"
                    }]
                }
            payment_payload = {
                "amount": {"value": price_str_for_api, "currency": "RUB"},
                "confirmation": {"type": "redirect", "return_url": f"https://t.me/{TELEGRAM_BOT_USERNAME}"},
                "capture": True,
                "description": f"Подписка на {months} мес.",
                "metadata": {
                    "user_id": user_id, "months": months, "price": price_float_for_metadata, 
                    "action": action, "key_id": key_id, "host_name": host_name,
                    "plan_id": plan_id, "customer_email": customer_email,
                    "payment_method": "YooKassa"
                }
            }
            if receipt:
                payment_payload['receipt'] = receipt

            payment = Payment.create(payment_payload, uuid.uuid4())
            
            await state.clear()
            
            await callback.message.edit_text(
                "Нажмите на кнопку ниже для оплаты:",
                reply_markup=keyboards.create_payment_keyboard(payment.confirmation.confirmation_url)
            )
        except Exception as e:
            logger.error(f"Failed to create YooKassa payment: {e}", exc_info=True)
            await callback.message.answer("Не удалось создать ссылку на оплату.")
            await state.clear()

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_cryptobot")
    async def create_cryptobot_invoice_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю счет в Crypto Pay...")
        
        data = await state.get_data()
        user_data = get_user(callback.from_user.id)
        
        plan_id = data.get('plan_id')
        user_id = data.get('user_id', callback.from_user.id)
        customer_email = data.get('customer_email')
        host_name = data.get('host_name')
        action = data.get('action')
        key_id = data.get('key_id')

        cryptobot_token = get_setting('cryptobot_token')
        if not cryptobot_token:
            logger.error(f"Attempt to create Crypto Pay invoice failed for user {user_id}: cryptobot_token is not set.")
            await callback.message.edit_text("❌ Оплата криптовалютой временно недоступна. (Администратор не указал токен).")
            await state.clear()
            return

        plan = get_plan_by_id(plan_id)
        if not plan:
            logger.error(f"Attempt to create Crypto Pay invoice failed for user {user_id}: Plan with id {plan_id} not found.")
            await callback.message.edit_text("❌ Произошла ошибка при выборе тарифа.")
            await state.clear()
            return
        
        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)

        if not plan:
            await callback.message.answer("Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        base_price = Decimal(str(plan['price']))
        price_rub = base_price

        if user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)
            if discount_percentage > 0:
                discount_amount = (base_price * discount_percentage / 100).quantize(Decimal("0.01"))
                price_rub = base_price - discount_amount
        months = plan['months']
        
        try:
            exchange_rate = await get_usdt_rub_rate()

            if not exchange_rate:
                logger.warning("Failed to get live exchange rate. Falling back to the rate from settings.")
                if not exchange_rate:
                    await callback.message.edit_text("❌ Не удалось получить курс валют. Попробуйте позже.")
                    await state.clear()
                    return

            margin = Decimal("1.03")
            price_usdt = (price_rub / exchange_rate * margin).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            
            logger.info(f"Creating Crypto Pay invoice for user {user_id}. Plan price: {price_rub} RUB. Converted to: {price_usdt} USDT.")

            crypto = CryptoPay(cryptobot_token)
            
            payload_data = f"{user_id}:{months}:{float(price_rub)}:{action}:{key_id}:{host_name}:{plan_id}:{customer_email}:CryptoBot"

            invoice = await crypto.create_invoice(
                currency_type="fiat",
                fiat="RUB",
                amount=float(price_rub),
                description=f"Подписка на {months} мес.",
                payload=payload_data,
                expires_in=3600
            )
            
            if not invoice or not invoice.pay_url:
                raise Exception("Failed to create invoice or pay_url is missing.")

            await callback.message.edit_text(
                "Нажмите на кнопку ниже для оплаты:",
                reply_markup=keyboards.create_payment_keyboard(invoice.pay_url)
            )
            await state.clear()

        except Exception as e:
            logger.error(f"Failed to create Crypto Pay invoice for user {user_id}: {e}", exc_info=True)
            await callback.message.edit_text(f"❌ Не удалось создать счет для оплаты криптовалютой.\n\n<pre>Ошибка: {e}</pre>")
            await state.clear()
        
    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_heleket")
    async def create_heleket_invoice_handler(callback: types.CallbackQuery, state: FSMContext):
        await callback.answer("Создаю счет Heleket...")
        
        data = await state.get_data()
        plan = get_plan_by_id(data.get('plan_id'))
        user_data = get_user(callback.from_user.id)
        
        if not plan:
            await callback.message.edit_text("❌ Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        plan_id = data.get('plan_id')
        plan = get_plan_by_id(plan_id)

        if not plan:
            await callback.message.answer("Произошла ошибка при выборе тарифа.")
            await state.clear()
            return

        base_price = Decimal(str(plan['price']))
        price_rub_decimal = base_price

        if user_data.get('referred_by') and user_data.get('total_spent', 0) == 0:
            discount_percentage_str = get_setting("referral_discount") or "0"
            discount_percentage = Decimal(discount_percentage_str)
            if discount_percentage > 0:
                discount_amount = (base_price * discount_percentage / 100).quantize(Decimal("0.01"))
                price_rub_decimal = base_price - discount_amount
        months = plan['months']
        
        final_price_float = float(price_rub_decimal)

        pay_url = await _create_heleket_payment_request(
            user_id=callback.from_user.id,
            price=final_price_float,
            months=plan['months'],
            host_name=data.get('host_name'),
            state_data=data
        )
        
        if pay_url:
            await callback.message.edit_text(
                "Нажмите на кнопку ниже для оплаты:",
                reply_markup=keyboards.create_payment_keyboard(pay_url)
            )
            await state.clear()
        else:
            await callback.message.edit_text("❌ Не удалось создать счет Heleket. Попробуйте другой способ оплаты.")

    @user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == "pay_tonconnect")
    async def create_ton_invoice_handler(callback: types.CallbackQuery, state: FSMContext):
        logger.info(f"User {callback.from_user.id}: Entered create_ton_invoice_handler.")
        data = await state.get_data()
        user_id = callback.from_user.id
        wallet_address = get_setting("ton_wallet_address")
        plan = get_plan_by_id(data.get('plan_id'))
        
        if not wallet_address or not plan:
            await callback.message.edit_text("❌ Оплата через TON временно недоступна.")
            await state.clear()
            return

        await callback.answer("Создаю ссылку и QR-код для TON Connect...")
            
        price_rub = Decimal(str(data.get('final_price', plan['price'])))

        usdt_rub_rate = await get_usdt_rub_rate()
        ton_usdt_rate = await get_ton_usdt_rate()

        if not usdt_rub_rate or not ton_usdt_rate:
            await callback.message.edit_text("❌ Не удалось получить курс TON. Попробуйте позже.")
            await state.clear()
            return

        price_ton = (price_rub / usdt_rub_rate / ton_usdt_rate).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        amount_nanoton = int(price_ton * 1_000_000_000)
        
        payment_id = str(uuid.uuid4())
        metadata = {
            "user_id": user_id, "months": plan['months'], "price": float(price_rub),
            "action": data.get('action'), "key_id": data.get('key_id'),
            "host_name": data.get('host_name'), "plan_id": data.get('plan_id'),
            "customer_email": data.get('customer_email'), "payment_method": "TON Connect"
        }
        create_pending_transaction(payment_id, user_id, float(price_rub), metadata)

        transaction_payload = {
            'messages': [{'address': wallet_address, 'amount': str(amount_nanoton), 'payload': payment_id}],
            'valid_until': int(datetime.now().timestamp()) + 600
        }

        try:
            connect_url = await _start_ton_connect_process(user_id, transaction_payload)
            
            qr_img = qrcode.make(connect_url)
            bio = BytesIO()
            qr_img.save(bio, "PNG")
            qr_file = BufferedInputFile(bio.getvalue(), "ton_qr.png")

            await callback.message.delete()
            await callback.message.answer_photo(
                photo=qr_file,
                caption=(
                    f"💎 **Оплата через TON Connect**\n\n"
                    f"Сумма к оплате: `{price_ton}` **TON**\n\n"
                    f"✅ **Способ 1 (на телефоне):** Нажмите кнопку **'Открыть кошелек'** ниже.\n"
                    f"✅ **Способ 2 (на компьютере):** Отсканируйте QR-код кошельком.\n\n"
                    f"После подключения кошелька подтвердите транзакцию."
                ),
                parse_mode="Markdown",
                reply_markup=keyboards.create_ton_connect_keyboard(connect_url)
            )
            await state.clear()

        except Exception as e:
            logger.error(f"Failed to generate TON Connect link for user {user_id}: {e}", exc_info=True)
            await callback.message.answer("❌ Не удалось создать ссылку для TON Connect. Попробуйте позже.")
            await state.clear()

        @user_router.message(F.text)
        @registration_required
        async def unknown_message_handler(message: types.Message):
            if message.text.startswith('/'):
                await message.answer("Такой команды не существует. Попробуйте /start.")
            else:
                await message.answer("Я не понимаю эту команду. Пожалуйста, используйте кнопки меню.")
    return user_router

_user_connectors: Dict[int, TonConnect] = {}
_listener_tasks: Dict[int, asyncio.Task] = {}

async def _get_ton_connect_instance(user_id: int) -> TonConnect:
    if user_id not in _user_connectors:
        manifest_url = 'https://raw.githubusercontent.com/ton-blockchain/ton-connect/main/requests-responses.json'
        _user_connectors[user_id] = TonConnect(manifest_url=manifest_url)
    return _user_connectors[user_id]

async def _listener_task(connector: TonConnect, user_id: int, transaction_payload: dict):
    try:
        wallet_connected = False
        for _ in range(120):
            if connector.connected:
                wallet_connected = True
                break
            await asyncio.sleep(1)

        if not wallet_connected:
            logger.warning(f"TON Connect: Timeout waiting for wallet connection from user {user_id}.")
            return

        logger.info(f"TON Connect: Wallet connected for user {user_id}. Address: {connector.account.address}")
        
        logger.info(f"TON Connect: Sending transaction request to user {user_id} with payload: {transaction_payload}")
        await connector.send_transaction(transaction_payload)
        
        logger.info(f"TON Connect: Transaction request sent successfully for user {user_id}.")

    except UserRejectsError:
        logger.warning(f"TON Connect: User {user_id} rejected the transaction.")
    except Exception as e:
        logger.error(f"TON Connect: An error occurred in the listener task for user {user_id}: {e}", exc_info=True)
    finally:
        if user_id in _user_connectors:
            del _user_connectors[user_id]
        if user_id in _listener_tasks:
            del _listener_tasks[user_id]

async def _start_ton_connect_process(user_id: int, transaction_payload: dict) -> str:
    if user_id in _listener_tasks and not _listener_tasks[user_id].done():
        _listener_tasks[user_id].cancel()

    connector = await _get_ton_connect_instance(user_id)
    
    task = asyncio.create_task(
        _listener_task(connector, user_id, transaction_payload)
    )
    _listener_tasks[user_id] = task

    wallets = connector.get_wallets()
    return await connector.connect(wallets[0])

async def process_successful_onboarding(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("✅ Спасибо! Доступ предоставлен.")
    set_terms_agreed(callback.from_user.id)
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Приятного использования!", reply_markup=keyboards.main_reply_keyboard)
    await show_main_menu(callback.message, with_photo=True)

async def is_url_reachable(url: str) -> bool:
    pattern = re.compile(
        r'^(https?://)'
        r'(([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,})'
        r'(/.*)?$'
    )
    if not re.match(pattern, url):
        return False

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.head(url, allow_redirects=True) as response:
                return response.status < 400
    except Exception as e:
        logger.warning(f"URL validation failed for {url}. Error: {e}")
        return False

async def notify_admin_of_purchase(bot: Bot, metadata: dict):
    if not ADMIN_ID:
        logger.warning("Admin notification skipped: ADMIN_ID is not set.")
        return

    try:
        user_id = metadata.get('user_id')
        months = metadata.get('months')
        price = float(metadata.get('price'))
        host_name = metadata.get('host_name')
        plan_id = metadata.get('plan_id')
        payment_method = metadata.get('payment_method', 'Unknown')
        
        user_info = get_user(user_id)
        plan_info = get_plan_by_id(plan_id)

        username = user_info.get('username', 'N/A') if user_info else 'N/A'
        plan_name = plan_info.get('plan_name', f'{months} мес.') if plan_info else f'{months} мес.'

        message_text = (
            "🎉 **Новая покупка!** 🎉\n\n"
            f"👤 **Пользователь:** @{username} (ID: `{user_id}`)\n"
            f"🌍 **Сервер:** {host_name}\n"
            f"📄 **Тариф:** {plan_name}\n"
            f"💰 **Сумма:** {price:.2f} RUB\n"
            f"💳 **Способ оплаты:** {payment_method}"
        )

        await bot.send_message(
            chat_id=ADMIN_ID,
            text=message_text,
            parse_mode='Markdown'
        )
        logger.info(f"Admin notification sent for a new purchase by user {user_id}.")

    except Exception as e:
        logger.error(f"Failed to send admin notification for purchase: {e}", exc_info=True)

async def _create_heleket_payment_request(user_id: int, price: float, months: int, host_name: str, state_data: dict) -> str | None:
    merchant_id = get_setting("heleket_merchant_id")
    api_key = get_setting("heleket_api_key")
    bot_username = get_setting("telegram_bot_username")
    domain = get_setting("domain")

    if not all([merchant_id, api_key, bot_username, domain]):
        logger.error("Heleket Error: Not all required settings are configured.")
        return None

    redirect_url = f"https://t.me/{bot_username}"
    order_id = str(uuid.uuid4())
    
    metadata = {
        "user_id": user_id, "months": months, "price": float(price),
        "action": state_data.get('action'), "key_id": state_data.get('key_id'),
        "host_name": host_name, "plan_id": state_data.get('plan_id'),
        "customer_email": state_data.get('customer_email'), "payment_method": "Heleket"
    }

    payload = {
        "amount": f"{price:.2f}",
        "currency": "RUB",
        "order_id": order_id,
        "description": json.dumps(metadata),
        "url_return": redirect_url,
        "url_success": redirect_url,
        "url_callback": f"https://{domain}/heleket-webhook",
        "lifetime": 1800,
        "is_payment_multiple": False
    }
    
    headers = {
        "merchant": merchant_id,
        "sign": _generate_heleket_signature(json.dumps(payload), api_key),
        "Content-Type": "application/json",
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            url = "https://api.heleket.com/v1/payment"
            async with session.post(url, json=payload, headers=headers) as response:
                result = await response.json()
                if response.status == 200 and result.get("result", {}).get("url"):
                    return result["result"]["url"]
                else:
                    logger.error(f"Heleket API Error: Status {response.status}, Result: {result}")
                    return None
    except Exception as e:
        logger.error(f"Heleket request failed: {e}", exc_info=True)
        return None

def _generate_heleket_signature(data, api_key: str) -> str:
    if isinstance(data, dict):
        data_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    else:
        data_str = str(data)
    base64_encoded = base64.b64encode(data_str.encode()).decode()
    raw_string = f"{base64_encoded}{api_key}"
    return hashlib.md5(raw_string.encode()).hexdigest()

async def get_usdt_rub_rate() -> Decimal | None:
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": "USDTRUB"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                price_str = data.get('price')
                if price_str:
                    logger.info(f"Got USDT RUB: {price_str}")
                    return Decimal(price_str)
                logger.error("Can't find 'price' in Binance response.")
                return None
    except Exception as e:
        logger.error(f"Error getting USDT RUB Binance rate: {e}", exc_info=True)
        return None
    
async def get_ton_usdt_rate() -> Decimal | None:
    url = "https://api.binance.com/api/v3/ticker/price"
    params = {"symbol": "TONUSDT"}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                price_str = data.get('price')
                if price_str:
                    logger.info(f"Got TON USDT: {price_str}")
                    return Decimal(price_str)
                logger.error("Can't find 'price' in Binance response.")
                return None
    except Exception as e:
        logger.error(f"Error getting TON USDT Binance rate: {e}", exc_info=True)
        return None

async def process_successful_payment(bot: Bot, metadata: dict):
    try:
        user_id = int(metadata['user_id'])
        months = int(metadata['months'])
        price = float(metadata['price'])
        action = metadata['action']
        key_id = int(metadata['key_id'])
        host_name = metadata['host_name']
        plan_id = int(metadata['plan_id'])
        customer_email = metadata.get('customer_email')
        payment_method = metadata.get('payment_method')

        chat_id_to_delete = metadata.get('chat_id')
        message_id_to_delete = metadata.get('message_id')
        
        # Логируем полученные метаданные
        logger.info(f"Processing successful payment for user {user_id}, host {host_name}, action {action}, key_id {key_id}")
        logger.info(f"Payment metadata: user_id={user_id}, months={months}, price={price}, action={action}, key_id={key_id}, host_name={host_name}, plan_id={plan_id}, payment_method={payment_method}")
        
    except (ValueError, TypeError) as e:
        logger.error(f"FATAL: Could not parse metadata. Error: {e}. Metadata: {metadata}")
        return

    if chat_id_to_delete and message_id_to_delete:
        try:
            await bot.delete_message(chat_id=chat_id_to_delete, message_id=message_id_to_delete)
        except TelegramBadRequest as e:
            logger.warning(f"Could not delete payment message: {e}")

    processing_message = await bot.send_message(
        chat_id=user_id,
        text=f"✅ Оплата получена! Обрабатываю ваш запрос на сервере \"{host_name}\"..."
    )
    try:
        email = ""
        if action == "new":
            key_number = get_next_key_number(user_id)
            email = f"user{user_id}-key{key_number}@{host_name.replace(' ', '').lower()}.bot"
            logger.info(f"Generated email for new key: {email}")
        elif action == "extend":
            key_data = get_key_by_id(key_id)
            if not key_data or key_data['user_id'] != user_id:
                await processing_message.edit_text("❌ Ошибка: ключ для продления не найден.")
                return
            email = key_data['key_email']
            logger.info(f"Using existing email for key extension: {email}")
        
        # Вычисляем точное количество дней, учитывая количество месяцев
        import calendar
        
        start_date = datetime.now().date()
        current_date = start_date
        
        # Вычисляем дату окончания, добавляя месяцы
        for _ in range(months):
            if current_date.month == 12:
                current_date = current_date.replace(year=current_date.year + 1, month=1)
            else:
                next_month = current_date.month + 1
                # Находим последний день следующего месяца
                max_day = calendar.monthrange(current_date.year, next_month)[1]
                # Используем минимальное значение между текущим днем и максимальным днем следующего месяца
                day = min(current_date.day, max_day)
                current_date = current_date.replace(month=next_month, day=day)
        
        # Вычисляем количество дней между начальной и конечной датой
        days_to_add = (current_date - start_date).days
        logger.info(f"Calculated days_to_add: {days_to_add} for {months} months from {start_date} to {current_date}")
        
        logger.info(f"Calling xui_api.create_or_update_key_on_host with host_name={host_name}, email={email}, days_to_add={days_to_add}")
        
        result = await xui_api.create_or_update_key_on_host(
            host_name=host_name,
            email=email,
            days_to_add=days_to_add
        )

        logger.info(f"xui_api.create_or_update_key_on_host returned result: {result is not None}")
        if result:
            logger.info(f"Result details - type: {type(result)}, keys: {result.keys() if isinstance(result, dict) else 'N/A'}")

        # Проверяем, что результат не None и является словарем
        if not result or not isinstance(result, dict):
            logger.error(f"xui_api.create_or_update_key_on_host returned invalid result (None or not dict) for user {user_id}, host {host_name}, email {email}")
            error_msg = "❌ Произошла ошибка на стороне сервера ключей. Не удалось создать/обновить ключ. Пожалуйста, свяжитесь с поддержкой."
            await processing_message.edit_text(error_msg)
            # Отправляем сообщение администратору
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"⚠️ Ошибка при выдаче ключа пользователю {user_id} на сервере {host_name}. API вернул неверный результат: {result}"
                    )
                except Exception as admin_notify_error:
                    logger.error(f"Could not notify admin about API error: {admin_notify_error}")
            return

        # Проверяем наличие необходимых ключей в ответе
        required_keys = ['client_uuid', 'email', 'expiry_timestamp_ms', 'connection_string']
        missing_keys = [key for key in required_keys if key not in result]
        if missing_keys:
            logger.error(f"xui_api.create_or_update_key_on_host returned result without required keys: {missing_keys} for user {user_id}, host {host_name}, email {email}. Result: {result}")
            error_msg = "❌ Сервер ключей вернул некорректный ответ. Пожалуйста, свяжитесь с поддержкой."
            await processing_message.edit_text(error_msg)
            # Отправляем сообщение администратору
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"⚠️ Ошибка при выдаче ключа пользователю {user_id} на сервере {host_name}. Отсутствуют ключи: {missing_keys}. Результат: {result}"
                    )
                except Exception as admin_notify_error:
                    logger.error(f"Could not notify admin about missing keys error: {admin_notify_error}")
            return

        if action == "new":
            logger.info(f"Adding new key to database for user {user_id}, host {host_name}, email {email}")
            logger.info(f"Key data to be saved: user_id={user_id}, host_name={host_name}, client_uuid={result['client_uuid']}, email={result['email']}, expiry_timestamp_ms={result['expiry_timestamp_ms']}")
            try:
                key_id = add_new_key(
                    user_id=user_id,
                    email=email,
                    xui_client_uuid=result['client_uuid'],
                    host_name=host_name,
                    days=days_to_add,
                    key_email=result['email'],
                    expiry_timestamp_ms=result['expiry_timestamp_ms']
                )
                logger.info(f"New key added with ID: {key_id}")
            except Exception as db_error:
                logger.error(f"Database error when adding new key for user {user_id}: {db_error}", exc_info=True)
                error_msg = "❌ Ошибка при сохранении ключа в базе данных. Пожалуйста, свяжитесь с поддержкой."
                await processing_message.edit_text(error_msg)
                # Отправляем сообщение администратору
                if ADMIN_ID:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⚠️ Ошибка БД при добавлении ключа для пользователя {user_id} на сервере {host_name}. Ошибка: {db_error}"
                        )
                    except Exception as admin_notify_error:
                        logger.error(f"Could not notify admin about DB error: {admin_notify_error}")
                return
        elif action == "extend":
            logger.info(f"Updating existing key in database with ID {key_id}")
            logger.info(f"Update data: client_uuid={result['client_uuid']}, expiry_timestamp_ms={result['expiry_timestamp_ms']}")
            try:
                update_key_info(key_id, result['client_uuid'], result['expiry_timestamp_ms'])
                logger.info(f"Key {key_id} updated successfully")
            except Exception as db_error:
                logger.error(f"Database error when updating key {key_id} for user {user_id}: {db_error}", exc_info=True)
                error_msg = "❌ Ошибка при обновлении ключа в базе данных. Пожалуйста, свяжитесь с поддержкой."
                await processing_message.edit_text(error_msg)
                # Отправляем сообщение администратору
                if ADMIN_ID:
                    try:
                        await bot.send_message(
                            chat_id=ADMIN_ID,
                            text=f"⚠️ Ошибка БД при обновлении ключа {key_id} для пользователя {user_id} на сервере {host_name}. Ошибка: {db_error}"
                        )
                    except Exception as admin_notify_error:
                        logger.error(f"Could not notify admin about DB error: {admin_notify_error}")
                return
        
        price = float(metadata.get('price'))


        logger.info(f"Updating user stats for user {user_id}, adding price {price}, months {months}")
        try:
            update_user_stats(user_id, price, months)
        except Exception as db_error:
            logger.error(f"Database error when updating user stats for user {user_id}: {db_error}", exc_info=True)
            # Продолжаем выполнение, так как это не критично для основного функционала
        
        user_info = get_user(user_id)
        logger.info(f"User info retrieved: {user_info is not None}")

        internal_payment_id = str(uuid.uuid4())
        
        log_username = user_info.get('username', 'N/A') if user_info else 'N/A'
        log_status = 'paid'
        log_amount_rub = float(price)
        log_method = metadata.get('payment_method', 'Unknown')
        
        log_metadata = json.dumps({
            "plan_id": metadata.get('plan_id'),
            "plan_name": get_plan_by_id(metadata.get('plan_id')).get('plan_name', 'Unknown') if metadata.get('plan_id') and get_plan_by_id(metadata.get('plan_id')) else 'Unknown',
            "host_name": metadata.get('host_name'),
            "customer_email": metadata.get('customer_email')
        })

        logger.info(f"Logging transaction with username={log_username}, payment_id={internal_payment_id}, user_id={user_id}, amount={log_amount_rub}, method={log_method}")
        try:
            log_transaction(
                username=log_username,
                transaction_id=None,
                payment_id=internal_payment_id,
                user_id=user_id,
                status=log_status,
                amount_rub=log_amount_rub,
                amount_currency=None,
                currency_name=None,
                payment_method=log_method,
                metadata=log_metadata
            )
        except Exception as db_error:
            logger.error(f"Database error when logging transaction for user {user_id}: {db_error}", exc_info=True)
            # Продолжаем выполнение, так как это не критично для основного функционала
        
        # Удаляем сообщение только после успешного выполнения всех операций
        try:
            await processing_message.delete()
        except Exception as delete_error:
            logger.warning(f"Could not delete processing message for user {user_id}: {delete_error}")
        
        connection_string = result['connection_string']
        new_expiry_date = datetime.fromtimestamp(result['expiry_timestamp_ms'] / 1000)
        
        all_user_keys = get_user_keys(user_id)
        key_number = next((i + 1 for i, key in enumerate(all_user_keys) if key['key_id'] == key_id), len(all_user_keys))

        final_text = get_purchase_success_text(
            action="создан" if action == "new" else "продлен",
            key_number=key_number,
            expiry_date=new_expiry_date,
            connection_string=connection_string
        )
        
        logger.info(f"Sending success message to user {user_id}")
        try:
            await bot.send_message(
                chat_id=user_id,
                text=final_text,
                reply_markup=keyboards.create_key_info_keyboard(key_id)
            )
        except Exception as send_error:
            logger.error(f"Could not send success message to user {user_id}: {send_error}")
            # Если не удалось отправить сообщение напрямую, пробуем отправить через редактирование
            # Но в данном случае processing_message уже удалено, поэтому просто логируем ошибку
            logger.error(f"Could not send success message to user {user_id} after processing: {send_error}")

        logger.info(f"Sending admin notification for user {user_id}")
        try:
            await notify_admin_of_purchase(bot, metadata)
        except Exception as admin_error:
            logger.error(f"Could not send admin notification for user {user_id}: {admin_error}")
        
    except Exception as e:
        logger.error(f"Error processing payment for user {user_id} on host {host_name}: {e}", exc_info=True)
        logger.error(f"Full error traceback:", exc_info=True)
        try:
            await processing_message.edit_text("❌ Ошибка при выдаче ключа.")
        except Exception as edit_error:
            logger.error(f"Could not edit processing message for user {user_id}: {edit_error}")
            try:
                await bot.send_message(user_id, "❌ Произошла ошибка при выдаче ключа.")
            except Exception as msg_error:
                logger.error(f"Could not send error message to user {user_id}: {msg_error}")
