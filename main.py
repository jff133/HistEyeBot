import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure, OperationFailure
import asyncio

# --- КОНФИГУРАЦИЯ БОТА ---
TOKEN = '7459480012:AAG8DS4Vet7X0uDRjzeJqJoJhUY8JWpclLo'
MONGO_URI = 'mongodb://localhost:27017/'  # <--- ОБНОВЛЕНО: ИСПОЛЬЗУЕМ ИМЯ ХОСТА
DATABASE_NAME = 'history_quiz_db'
QUESTIONS_COLLECTION_NAME = 'questions'

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ MONGODB ---
mongo_client = None
db = None
questions_collection = None

# --- СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЕЙ ---
user_state = {}


# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ (АСИНХРОННЫЕ) ---

async def connect_to_mongodb():
    """Устанавливает асинхронное соединение с MongoDB."""
    global mongo_client, db, questions_collection
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URI)
        await mongo_client.admin.command('ping')
        db = mongo_client[DATABASE_NAME]
        questions_collection = db[QUESTIONS_COLLECTION_NAME]
        logger.info(f"Успешно подключено к MongoDB (асинхронно) - база данных: {DATABASE_NAME}, коллекция: {QUESTIONS_COLLECTION_NAME}, хост: {MONGO_URI}.")
        return True
    except ConnectionFailure as e:
        logger.error(f"Не удалось подключиться к MongoDB (асинхронно): {e}. Убедитесь, что сервер запущен.")
        return False
    except OperationFailure as e:
        logger.error(f"Ошибка аутентификации MongoDB (асинхронно): {e}")
        return False
    except Exception as e:
        logger.error(f"Неизвестная ошибка при подключении к MongoDB (асинхронно): {e}")
        return False


async def load_questions_from_db():
    """Загружает все вопросы из коллекции 'questions' в MongoDB (асинхронно) с логированием."""
    if questions_collection is None:
        logger.error("Коллекция вопросов MongoDB не инициализирована.")
        return []
    try:
        questions = await questions_collection.find().to_list(length=None)
        logger.info(f"Найдено {len(questions)} документов в коллекции '{QUESTIONS_COLLECTION_NAME}'.")
        loaded_questions = []
        for q in questions:
            logger.info(f"Обработка документа из MongoDB: {q}")

            question_text = q.get("question")
            options = q.get("options")
            correct_answer = q.get("correct_answer")
            correct_index = q.get("correct_index")

            is_valid = True
            reasons = []

            # Проверка question_text
            if not isinstance(question_text, str) or not question_text.strip():
                is_valid = False
                reasons.append(f"'question' отсутствует, не строка или пустое: {question_text} (тип: {type(question_text)})")

            # Проверка options
            if not isinstance(options, list):
                is_valid = False
                reasons.append(f"'options' не является списком: {options} (тип: {type(options)})")
            elif not options:
                is_valid = False
                reasons.append("'options' является пустым списком")
            else:
                if not all(isinstance(opt, str) for opt in options):
                    is_valid = False
                    reasons.append("некоторые элементы в 'options' не являются строками")

            # Проверка correct_answer
            if not isinstance(correct_answer, str) or not correct_answer.strip():
                is_valid = False
                reasons.append(f"'correct_answer' отсутствует, не строка или пустое: {correct_answer} (тип: {type(correct_answer)})")

            # Проверка correct_index
            if not isinstance(correct_index, int):
                if isinstance(correct_index, float) and correct_index.is_integer():
                    correct_index = int(correct_index)
                else:
                    is_valid = False
                    reasons.append(f"'correct_index' не является целым числом: {correct_index} (тип: {type(correct_index)})")

            if is_valid:
                if '_id' in q:
                    del q['_id']
                loaded_questions.append(q)
            else:
                logger.warning(f"Пропущен некорректный вопрос (ID: {q.get('_id', 'N/A')}): {' | '.join(reasons)}. Документ: {q}")

        logger.info(f"Загружено {len(loaded_questions)} корректных вопросов после обработки.")
        return loaded_questions
    except OperationFailure as e:
        logger.error(f"Ошибка операции MongoDB при загрузке вопросов (асинхронно): {e}")
        return []
    except Exception as e:
        logger.error(f"Ошибка при загрузке вопросов из базы данных (асинхронно): {e}")
        return []


# --- ОБРАБОТЧИКИ КОМАНД (АСИНХРОННЫЕ) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f'Привет, {user_name}! 👋\n'
        'Добро пожаловать в викторину! 🧠\n'
        'Нажми /quiz, чтобы начать игру.'
    )


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    questions = await load_questions_from_db()
    context.bot_data['quiz_questions'] = questions

    if not questions:
        await update.message.reply_text(
            "Извините, пока нет доступных вопросов для викторины. Пожалуйста, добавьте вопросы в базу данных.")
        return

    user_state[user_id] = {"score": 0, "current_question": 0}
    logger.info(f"Пользователь {user_id} начал викторину.")
    await send_question(update, context)


async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Отправляет текущий вопрос викторины с вариантами ответов в виде inline-кнопок.
    Если это первый вопрос (после команды), отправляет новое сообщение.
    Если это следующий вопрос (после ответа на предыдущий), всегда отправляет новое сообщение.
    """
    user_id = update.effective_user.id
    questions = context.bot_data.get('quiz_questions')

    if user_id not in user_state or user_state[user_id]["current_question"] >= len(questions):
        final_score = user_state.get(user_id, {}).get('score', 0)
        total_questions = len(questions)
        await context.bot.send_message( # Всегда отправляем новое сообщение для завершения викторины
            chat_id=update.effective_chat.id,
            text=f"Викторина завершена! 🎉\n"
                 f"Ваш итоговый счет: {final_score} из {total_questions}."
        )
        if user_id in user_state:
            del user_state[user_id]
        logger.info(f"Викторина для пользователя {user_id} завершена. Счет: {final_score}")
        return

    current_question_index = user_state[user_id]["current_question"]
    question_data = questions[current_question_index]
    question_text = question_data.get("question", "Ошибка: нет текста вопроса.")
    options = question_data.get("options", [])

    if not options:
        logger.error(f"Нет вариантов ответа для вопроса: {question_text}. Пропускаем вопрос.")
        await context.bot.send_message( # Всегда отправляем новое сообщение
            chat_id=update.effective_chat.id,
            text="Произошла ошибка при загрузке вариантов ответа для этого вопроса. Переходим к следующему."
        )
        user_state[user_id]["current_question"] += 1
        await send_question(update, context)
        return

    keyboard = []
    for i, option in enumerate(options):
        keyboard.append([InlineKeyboardButton(option, callback_data=str(i))])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Всегда отправляем новый вопрос как новое сообщение
    await context.bot.send_message(
        chat_id=update.effective_chat.id, # Используем effective_chat.id для надежности
        text=question_text,
        reply_markup=reply_markup
    )
    logger.info(f"Вопрос {current_question_index + 1} отправлен пользователю {user_id}.")



async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()  # Оставляем, чтобы пользователь видел быстрое уведомление

    if user_id not in user_state:
        await query.edit_message_text("Викторина не запущена. Нажмите /quiz, чтобы начать новую игру.")
        return

    current_question_index = user_state[user_id]["current_question"]
    questions = context.bot_data.get('quiz_questions')

    if current_question_index >= len(questions):
        await query.edit_message_text(
            f"Викторина уже завершена! Ваш счет: {user_state[user_id]['score']} из {len(questions)}."
        )
        return

    question_data = questions[current_question_index]
    correct_answer_index = question_data.get("correct_index")
    correct_answer_text = question_data.get("correct_answer")
    selected_answer_index = int(query.data)

    response_text = ""
    if selected_answer_index == correct_answer_index:
        user_state[user_id]["score"] += 1
        response_text = f"✅ Правильно! Ваш текущий счет: {user_state[user_id]['score']} из {len(questions)}."
        logger.info(f"Пользователь {user_id} ответил правильно на вопрос {current_question_index + 1}.")
    else:
        response_text = f"❌ Неправильно. Правильный ответ: *{correct_answer_text}*. Ваш текущий счет: {user_state[user_id]['score']} из {len(questions)}."
        logger.info(f"Пользователь {user_id} ответил неправильно на вопрос {current_question_index + 1}.")

    try:
        # 1. Отредактируем сообщение с вопросом, добавив результат и убрав кнопки
        keyboard = []
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"{query.message.text}\n\n{response_text}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение после ответа: {e}.")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=response_text,
            parse_mode='Markdown'
        )

    user_state[user_id]["current_question"] += 1
    # 2. Отправим следующий вопрос новым сообщением
    await send_question(update, context)


async def post_init_setup(application: Application) -> None:
    """
    Асинхронная функция для выполнения настройки после инициализации Application.
    Используется для подключения к БД и загрузки вопросов с логированием.
    """
    if not await connect_to_mongodb():
        logger.critical("Не удалось подключиться к MongoDB. Бот не будет запущен.")
        return

    application.bot_data['quiz_questions'] = await load_questions_from_db()
    if not application.bot_data['quiz_questions']:
        logger.warning("Нет корректных вопросов в базе данных. Викторина будет недоступна.")


def main() -> None:
    """Запускает бота."""
    application = ApplicationBuilder().token(TOKEN).post_init(post_init_setup).build()

    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("quiz", quiz))

    # Регистрируем обработчик для нажатий на inline-кнопки
    application.add_handler(CallbackQueryHandler(check_answer))

    logger.info("Бот запущен. Ожидание обновлений...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
