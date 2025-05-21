import logging
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.utils.markdown import hbold
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta, timezone

from app.db.crud import user_crud
from app.ui.keyboards import (
    get_main_menu_keyboard,
    get_onboarding_welcome_keyboard,
    get_onboarding_explanation_keyboard,
    get_onboarding_trial_offer_keyboard,
    get_main_inline_menu_keyboard,
    get_back_to_main_menu_keyboard,
    OnboardingCallback
)
from app.db.models import SubscriptionStatus, UserRole
from app.core.config import settings
from app.states.feedback_states import FeedbackStates

logger = logging.getLogger(__name__)
user_onboarding_router = Router(name="user_onboarding_handlers")

WELCOME_TEXT = """👋 Привет! Я виртуальный помощник для тренировки навыков КПТ-психотерапевтов.

Здесь ты сможешь:
✨ Получать уникальные кейсы, сгенерированные ИИ.
✨ Предлагать свои решения.
✨ Получать подробную обратную связь.
✨ Отслеживать свой прогресс.

Готов начать оттачивать мастерство?
"""

EXPLANATION_TEXT = """📝 Как все устроено:

1️⃣ Ты запрашиваешь новый кейс.
2️⃣ Внимательно изучаешь описание ситуации клиента.
3️⃣ Формулируешь и отправляешь свое решение или план терапии.
4️⃣ Виртуальный помощник анализирует твой ответ и дает развернутую обратную связь, подсвечивая сильные стороны и зоны роста.

Это отличная возможность практиковаться в безопасной среде и получать конструктивные замечания для улучшения твоих навыков!
"""

TRIAL_OFFER_TEXT = """🚀 Отлично!

Чтобы ты мог полноценно оценить все возможности, мы предлагаем тебе начать с <b>бесплатного 7-дневного пробного периода</b>.
В течение недели тебе будут доступны все функции бота без ограничений.

После окончания пробного периода ты сможешь выбрать подходящий тариф для продолжения практики.
"""

TRIAL_STARTED_TEXT = """🎉 Твой 7-дневный бесплатный пробный период начался! ({start_date} - {end_date})

Теперь тебе доступны все функции бота. Используй кнопки ниже, чтобы начать."""
WELCOME_BACK_TEXT = """👋 С возвращением! Рад снова тебя видеть."""
HELP_TEXT = """👋 <b>Раздел Помощи</b>

Давайте быстро пробежимся по основным функциям:

🎲 <b>Новый кейс</b> - Получить свежий терапевтический случай для разбора и практики!

📊 <b>Мой прогресс</b> - Посмотреть вашу статистику!

💬 <b>Оставить отзыв</b> - Поделиться мыслями о боте, предложить идею или сообщить о неполадке!

💳 <b>Тарифы и подписка</b> - Узнать актуальные тарифы!


"""
PROFILE_TEXT = """👤 Ваш профиль:

Telegram ID: {user_id}
Роль: {role}
Статус подписки: {subscription_status}
Пробный период до: {trial_end_date_str}
Подписка до: {subscription_expires_at_str}"""

@user_onboarding_router.message(CommandStart())
async def handle_start(message: types.Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    user = message.from_user
    db_user = await user_crud.get_user_by_telegram_id(session, telegram_id=user.id)

    current_time = datetime.now(timezone.utc)
    user_data_for_create_or_update = {
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "last_seen": current_time,
    }

    if not db_user:
        db_user = await user_crud.create_user(
            session,
            telegram_id=user.id,
            role=UserRole.USER,
            subscription_status=SubscriptionStatus.NONE,
            **user_data_for_create_or_update
        )
        logger.info(f"New user {db_user.telegram_id} created. Starting onboarding.")
        await message.answer(WELCOME_TEXT, reply_markup=get_onboarding_welcome_keyboard())
        return
    else:
        update_payload = {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "last_active_at": current_time
        }
        db_user = await user_crud.update_user(session, telegram_id=db_user.telegram_id, update_data=update_payload)
        if not db_user:
             logger.error(f"Failed to update existing user {user.id}")
             await message.answer("Произошла ошибка. Попробуйте позже.")
             return
        logger.info(f"User {db_user.telegram_id} exists. Activity updated.")

    if db_user.is_blocked:
        await message.answer("Ваш аккаунт заблокирован. Обратитесь к администратору.")
        logger.warning(f"Blocked user {user.id} tried to use /start.")
        return

    trial_active = db_user.subscription_status == SubscriptionStatus.TRIAL and db_user.trial_end_date and db_user.trial_end_date > current_time
    subscription_active = db_user.subscription_status == SubscriptionStatus.ACTIVE and db_user.subscription_expires_at and db_user.subscription_expires_at > current_time
    trial_was_ever_used = db_user.trial_start_date is not None
    
    if not trial_active and not subscription_active:
        if trial_was_ever_used:
            # Trial was used and is not currently active, and no active subscription
            logger.info(f"User {db_user.telegram_id} has no active sub/trial, but trial was used before. Guiding to /menu.")
            await message.answer(
                "Пробный период уже был использован. Вы можете выбрать платный тариф или посмотреть другие опции в меню.", 
                reply_markup=get_main_inline_menu_keyboard()
            )
        else:
            # No active trial, no active subscription, and trial was never used
            logger.info(f"User {db_user.telegram_id} has no active sub/trial and trial never used. Starting onboarding flow.")
            await message.answer(WELCOME_TEXT, reply_markup=get_onboarding_welcome_keyboard())
    else:
        # User has an active trial or active subscription
        logger.info(f"User {db_user.telegram_id} has active sub/trial. Sending to main menu (inline).")
        await message.answer(WELCOME_BACK_TEXT, reply_markup=get_main_inline_menu_keyboard())

@user_onboarding_router.callback_query(OnboardingCallback.filter(F.action == "tell_me_more"))
async def cq_onboarding_tell_me_more(query: types.CallbackQuery, callback_data: OnboardingCallback, session: AsyncSession):
    await query.message.edit_text(EXPLANATION_TEXT, reply_markup=get_onboarding_explanation_keyboard())
    await query.answer()

@user_onboarding_router.callback_query(OnboardingCallback.filter(F.action == "how_to_start"))
async def cq_onboarding_how_to_start(query: types.CallbackQuery, callback_data: OnboardingCallback, session: AsyncSession):
    await query.message.edit_text(TRIAL_OFFER_TEXT, reply_markup=get_onboarding_trial_offer_keyboard())
    await query.answer()

@user_onboarding_router.callback_query(OnboardingCallback.filter(F.action == "start_trial"))
async def cq_onboarding_start_trial(query: types.CallbackQuery, callback_data: OnboardingCallback, session: AsyncSession):
    telegram_user_id = query.from_user.id
    db_user = await user_crud.get_user_by_telegram_id(session, telegram_id=telegram_user_id)
    current_time = datetime.now(timezone.utc)

    if not db_user:
        logger.error(f"User {telegram_user_id} not found in DB during start_trial callback.")
        await query.answer("Произошла ошибка. Пожалуйста, попробуйте /start снова.", show_alert=True)
        return

    has_active_subscription = db_user.subscription_status == SubscriptionStatus.ACTIVE and \
                              db_user.subscription_expires_at and \
                              db_user.subscription_expires_at > current_time
    
    trial_was_used = db_user.trial_start_date is not None

    if has_active_subscription or trial_was_used:
        message_text = "Похоже, у вас уже есть активная подписка." if has_active_subscription else "Похоже, вы уже использовали пробный период."
        logger.info(f"User {db_user.telegram_id} tried to start trial but: active_sub={has_active_subscription}, trial_used={trial_was_used}.")
        await query.message.edit_text(message_text, reply_markup=None)
        await query.message.answer(f"Вот ваше главное меню, {hbold(query.from_user.first_name or '')}!", reply_markup=get_main_menu_keyboard(user_role=db_user.role))
        await query.answer()
        return

    trial_starts_at = current_time
    trial_expires_at = trial_starts_at + timedelta(days=settings.TRIAL_PERIOD_DAYS)
    
    updated_user_data = {
        "subscription_status": SubscriptionStatus.TRIAL,
        "trial_start_date": trial_starts_at,
        "trial_end_date": trial_expires_at,
        "last_active_at": current_time
    }
    updated_user = await user_crud.update_user(
        session,
        telegram_id=db_user.telegram_id, 
        update_data=updated_user_data
    )

    if updated_user:
        start_date_str = trial_starts_at.strftime("%d.%m.%Y")
        end_date_str = trial_expires_at.strftime("%d.%m.%Y")
        logger.info(f"User {updated_user.telegram_id} started trial period until {end_date_str}.")
        await query.message.edit_text(
            TRIAL_STARTED_TEXT.format(start_date=start_date_str, end_date=end_date_str),
            reply_markup=None # Remove old keyboard
        )
        await query.message.answer(f"Добро пожаловать, {hbold(updated_user.first_name or '')}! Используйте /menu для доступа к функциям.", reply_markup=get_main_inline_menu_keyboard()) # Show inline menu
    else:
        logger.error(f"Failed to update user {db_user.telegram_id} to start trial period via update_user.")
        await query.message.edit_text("Не удалось активировать пробный период. Пожалуйста, свяжитесь с поддержкой.")
    
    await query.answer()

@user_onboarding_router.message(Command("help"))
@user_onboarding_router.message(F.text == "ℹ️ Помощь")
async def handle_help_command(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP_TEXT, disable_web_page_preview=True, parse_mode='HTML')

@user_onboarding_router.message(Command("profile"))
async def handle_profile_command(message: types.Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    telegram_user_id = message.from_user.id
    db_user = await user_crud.get_user_by_telegram_id(session, telegram_id=telegram_user_id)
    if db_user:
        trial_end_str = db_user.trial_end_date.strftime('%d.%m.%Y') if db_user.trial_end_date else 'не использовался'
        sub_expires_str = db_user.subscription_expires_at.strftime('%d.%m.%Y') if db_user.subscription_expires_at else 'нет'
        status_description = "Нет подписки"
        if db_user.subscription_status == SubscriptionStatus.TRIAL:
            status_description = f"Пробный период (до {trial_end_str})"
        elif db_user.subscription_status == SubscriptionStatus.ACTIVE:
            status_description = f"Активна (до {sub_expires_str})"
        elif db_user.subscription_status == SubscriptionStatus.EXPIRED:
            status_description = "Истекла"
            if db_user.subscription_expires_at:
                status_description += f" ({db_user.subscription_expires_at.strftime('%d.%m.%Y')})"
        role_map = {
            UserRole.USER: "Пользователь",
            UserRole.ADMIN: "Администратор"
        }
        profile_info = PROFILE_TEXT.format(
            user_id=db_user.telegram_id,
            role=role_map.get(db_user.role, str(db_user.role)),
            subscription_status=status_description,
            trial_end_date_str=trial_end_str,
            subscription_expires_at_str=sub_expires_str
        )
        await message.answer(profile_info)
    else:
        await message.answer("Не удалось найти ваш профиль. Попробуйте /start")

@user_onboarding_router.message(Command("menu"))
async def handle_menu_command(message: types.Message, session: AsyncSession, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    db_user = await user_crud.get_user_by_telegram_id(session, telegram_id=user_id)
    if not db_user:
        await message.answer("Похоже, вы еще не начали диалог со мной. Пожалуйста, используйте /start.")
        return
    if db_user.is_blocked:
        await message.answer("Ваш аккаунт заблокирован.")
        return
    await message.answer("Выберите опцию из меню:", reply_markup=get_main_inline_menu_keyboard())

@user_onboarding_router.callback_query(F.data == "main_menu:show")
async def cq_show_main_menu(query: types.CallbackQuery, session: AsyncSession, state: FSMContext):
    current_state = await state.get_state()
    if current_state == FeedbackStates.awaiting_feedback_text:
        await state.clear()
        logger.info(f"User {query.from_user.id} went back to main menu, cleared FeedbackStates.awaiting_feedback_text state.")

    await query.message.edit_text(
        "Выберите опцию из меню:", 
        reply_markup=get_main_inline_menu_keyboard()
    )
    await query.answer()
