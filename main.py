# Импорт необходимых библиотек
import logging # Для логирования событий и ошибок
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup # Основные классы для взаимодействия с Telegram API
from telegram.ext import Application, ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes # Классы для создания и управления ботом
from motor.motor_asyncio import AsyncIOMotorClient # Асинхронный драйвер для MongoDB
from pymongo.errors import ConnectionFailure, OperationFailure # Исключения для обработки ошибок MongoDB
import asyncio # Библиотека для асинхронного программирования

# --- КОНФИГУРАЦИЯ БОТА ---
# Эти параметры определяют, как бот будет подключаться к Telegram и MongoDB.
TOKEN = '7459480012:AAG8DS4Vet7X0uDRjzeJqJoJhUY8JWpclLo' # Уникальный токен вашего бота, полученный от @BotFather в Telegram.
MONGO_URI = 'mongodb://localhost:27017/'  # Адрес сервера MongoDB. 'localhost:27017' - стандартный адрес для локально запущенной MongoDB.
DATABASE_NAME = 'history_quiz_db' # Имя базы данных в MongoDB, где хранятся вопросы.
QUESTIONS_COLLECTION_NAME = 'questions' # Имя коллекции внутри базы данных для документов с вопросами.

# Включаем и настраиваем систему логирования.
# Логи помогают отслеживать работу бота, выявлять ошибки и понимать последовательность событий.
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', # Формат записи лога: время, имя логгера, уровень (INFO, ERROR и т.д.), сообщение.
    level=logging.INFO # Устанавливаем уровень логирования. INFO означает, что будут записываться информационные сообщения, предупреждения и ошибки.
)
logger = logging.getLogger(__name__) # Создаем экземпляр логгера для текущего модуля.

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ MONGODB ---
# Эти переменные будут хранить объекты для работы с MongoDB после успешного подключения.
mongo_client = None # Клиент для подключения к серверу MongoDB.
db = None # Объект базы данных.
questions_collection = None # Объект коллекции вопросов.

# --- СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЕЙ ---
# Словарь для хранения текущего состояния викторины для каждого пользователя.
# Ключ - ID пользователя, значение - словарь с его счетом и номером текущего вопроса.
# Это простое хранилище в памяти; при перезапуске бота данные из user_state будут потеряны.
user_state = {}


# --- ФУНКЦИИ ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ (АСИНХРОННЫЕ) ---

async def connect_to_mongodb():
    """
    Устанавливает асинхронное соединение с MongoDB.
    Инициализирует глобальные переменные mongo_client, db, questions_collection.
    Возвращает True в случае успеха, False в случае ошибки.
    """
    global mongo_client, db, questions_collection # Указываем, что мы будем изменять глобальные переменные.
    try:
        # Создаем асинхронный клиент MongoDB.
        mongo_client = AsyncIOMotorClient(MONGO_URI)
        # Проверяем соединение, отправив команду 'ping' на сервер.
        # 'await' приостанавливает выполнение функции до получения ответа от сервера.
        await mongo_client.admin.command('ping')
        # Если 'ping' успешен, получаем доступ к нашей базе данных и коллекции.
        db = mongo_client[DATABASE_NAME]
        questions_collection = db[QUESTIONS_COLLECTION_NAME]
        logger.info(f"Успешно подключено к MongoDB (асинхронно) - база данных: {DATABASE_NAME}, коллекция: {QUESTIONS_COLLECTION_NAME}, хост: {MONGO_URI}.")
        return True
    except ConnectionFailure as e: # Ошибка: не удалось подключиться к серверу.
        logger.error(f"Не удалось подключиться к MongoDB (асинхронно): {e}. Убедитесь, что сервер запущен.")
        return False
    except OperationFailure as e: # Ошибка: проблема с операцией, часто связана с аутентификацией.
        logger.error(f"Ошибка аутентификации MongoDB (асинхронно): {e}")
        return False
    except Exception as e: # Любая другая непредвиденная ошибка.
        logger.error(f"Неизвестная ошибка при подключении к MongoDB (асинхронно): {e}")
        return False


async def load_questions_from_db():
    """
    Асинхронно загружает все вопросы из коллекции 'questions' в MongoDB.
    Проводит валидацию каждого вопроса.
    Возвращает список корректных вопросов или пустой список в случае ошибки.
    """
    if questions_collection is None: # Проверка, что соединение с коллекцией установлено.
        logger.error("Коллекция вопросов MongoDB не инициализирована.")
        return []
    try:
        # Ищем все документы в коллекции. find() возвращает курсор.
        # to_list(length=None) асинхронно извлекает все документы из курсора в список.
        questions_cursor = questions_collection.find()
        questions = await questions_cursor.to_list(length=None)
        logger.info(f"Найдено {len(questions)} документов в коллекции '{QUESTIONS_COLLECTION_NAME}'.")

        loaded_questions = [] # Список для хранения валидных вопросов.
        for q_doc in questions: # Обрабатываем каждый документ из базы данных.
            logger.info(f"Обработка документа из MongoDB: {q_doc}")

            # Извлекаем поля из документа, используя .get() для безопасного доступа (вернет None, если поля нет).
            question_text = q_doc.get("question")
            options = q_doc.get("options")
            correct_answer = q_doc.get("correct_answer")
            correct_index = q_doc.get("correct_index")

            is_valid = True # Флаг, указывающий, прошел ли вопрос валидацию.
            reasons = [] # Список для сообщений об ошибках валидации.

            # Валидация: текст вопроса должен быть непустой строкой.
            if not isinstance(question_text, str) or not question_text.strip():
                is_valid = False
                reasons.append(f"'question' отсутствует, не строка или пустое: {question_text} (тип: {type(question_text)})")

            # Валидация: варианты ответов должны быть непустым списком строк.
            if not isinstance(options, list):
                is_valid = False
                reasons.append(f"'options' не является списком: {options} (тип: {type(options)})")
            elif not options:
                is_valid = False
                reasons.append("'options' является пустым списком")
            else:
                if not all(isinstance(opt, str) for opt in options): # Проверяем, что все элементы списка - строки.
                    is_valid = False
                    reasons.append("некоторые элементы в 'options' не являются строками")

            # Валидация: текст правильного ответа должен быть непустой строкой.
            if not isinstance(correct_answer, str) or not correct_answer.strip():
                is_valid = False
                reasons.append(f"'correct_answer' отсутствует, не строка или пустое: {correct_answer} (тип: {type(correct_answer)})")

            # Валидация: индекс правильного ответа должен быть целым числом.
            # Допускается, если пришло число с плавающей точкой, но оно целое (например, 2.0).
            if not isinstance(correct_index, int):
                if isinstance(correct_index, float) and correct_index.is_integer():
                    correct_index = int(correct_index) # Преобразуем в int.
                    q_doc["correct_index"] = correct_index # Обновляем значение в исходном словаре, чтобы сохранить правильный тип.
                else:
                    is_valid = False
                    reasons.append(f"'correct_index' не является целым числом: {correct_index} (тип: {type(correct_index)})")

            if is_valid:
                if '_id' in q_doc: # MongoDB автоматически добавляет поле _id.
                    del q_doc['_id'] # Удаляем его, так как оно не используется в логике бота.
                loaded_questions.append(q_doc) # Добавляем валидный вопрос.
            else:
                # Если вопрос не прошел валидацию, логируем предупреждение.
                logger.warning(f"Пропущен некорректный вопрос (ID: {q_doc.get('_id', 'N/A')}): {' | '.join(reasons)}. Документ: {q_doc}")

        logger.info(f"Загружено {len(loaded_questions)} корректных вопросов после обработки.")
        return loaded_questions
    except OperationFailure as e: # Ошибка операции с MongoDB.
        logger.error(f"Ошибка операции MongoDB при загрузке вопросов (асинхронно): {e}")
        return []
    except Exception as e: # Другие ошибки.
        logger.error(f"Ошибка при загрузке вопросов из базы данных (асинхронно): {e}")
        return []


# --- ОБРАБОТЧИКИ КОМАНД (АСИНХРОННЫЕ) ---
# Эти функции вызываются, когда пользователь отправляет боту определенную команду (например, /start).

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /start. Отправляет приветственное сообщение.
    'update' - объект, содержащий информацию о входящем сообщении (от кого, текст и т.д.).
    'context' - словарь для обмена данными между обработчиками или хранения данных бота.
    """
    user_name = update.effective_user.first_name # Получаем имя пользователя.
    # Отправляем ответное сообщение пользователю. 'await' используется для асинхронной отправки.
    await update.message.reply_text(
        f'Привет, {user_name}! 👋\n'
        'Добро пожаловать в викторину! 🧠\n'
        'Нажми /quiz, чтобы начать игру.'
    )


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик команды /quiz. Начинает новую викторину для пользователя.
    """
    user_id = update.effective_user.id # Уникальный идентификатор пользователя.
    # Получаем предварительно загруженные вопросы из context.bot_data.
    # Это эффективнее, чем загружать их из БД каждый раз при вызове /quiz.
    questions = context.bot_data.get('quiz_questions', [])

    if not questions: # Если вопросы не загружены (например, БД пуста или ошибка при старте).
        await update.message.reply_text(
            "Извините, пока нет доступных вопросов для викторины. Пожалуйста, добавьте вопросы в базу данных."
        )
        return # Завершаем обработку, если нет вопросов.

    # Инициализируем или сбрасываем состояние викторины для данного пользователя.
    user_state[user_id] = {"score": 0, "current_question": 0}
    logger.info(f"Пользователь {user_id} начал викторину.")
    # Вызываем функцию для отправки первого вопроса.
    await send_question(update, context)


async def send_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Отправляет текущий вопрос викторины пользователю или сообщение о завершении викторины.
    Эта функция вызывается как при начале викторины, так и после ответа на предыдущий вопрос.
    """
    # `update.effective_user.id` и `update.effective_chat.id` безопасно предоставляют ID пользователя и чата,
    # независимо от того, был ли это прямой вызов команды или callback от кнопки.
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id # Получаем ID чата, чтобы знать, куда отправлять сообщение.

    questions = context.bot_data.get('quiz_questions') # Список всех вопросов викторины.

    # Проверяем, есть ли активная викторина для пользователя и не закончились ли вопросы.
    if user_id not in user_state or user_state[user_id]["current_question"] >= len(questions):
        # Викторина завершена или не была начата для этого пользователя.
        final_score = user_state.get(user_id, {}).get('score', 0) # Получаем итоговый счет.
        total_questions = len(questions) if questions else 0
        # Отправляем сообщение о завершении.
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Викторина завершена! 🎉\n"
                 f"Ваш итоговый счет: {final_score} из {total_questions}."
        )
        if user_id in user_state: # Удаляем состояние пользователя, так как викторина окончена.
            del user_state[user_id]
        logger.info(f"Викторина для пользователя {user_id} завершена. Счет: {final_score}")
        return

    # Получаем индекс текущего вопроса для пользователя.
    current_question_index = user_state[user_id]["current_question"]
    question_data = questions[current_question_index] # Получаем данные текущего вопроса.
    question_text = question_data.get("question", "Ошибка: нет текста вопроса.")
    options = question_data.get("options", [])

    if not options: # Обработка случая, если у вопроса нет вариантов ответа (ошибка в данных).
        logger.error(f"Нет вариантов ответа для вопроса: {question_text} (ID пользователя: {user_id}). Пропускаем вопрос.")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Произошла ошибка при загрузке вариантов ответа для этого вопроса. Переходим к следующему."
        )
        user_state[user_id]["current_question"] += 1 # Переходим к следующему вопросу.
        await send_question(update, context) # Рекурсивно вызываем отправку следующего вопроса.
        return

    # Создаем inline-клавиатуру с вариантами ответов.
    # Inline-кнопки отображаются под сообщением.
    keyboard = []
    for i, option_text in enumerate(options):
        # Каждая кнопка содержит текст (option_text) и callback_data (индекс варианта 'i').
        # callback_data будет отправлена боту при нажатии на кнопку.
        keyboard.append([InlineKeyboardButton(option_text, callback_data=str(i))])

    reply_markup = InlineKeyboardMarkup(keyboard) # Объект клавиатуры.

    # Отправляем вопрос пользователю вместе с клавиатурой.
    # Каждый новый вопрос отправляется новым сообщением.
    await context.bot.send_message(
        chat_id=chat_id,
        text=question_text,
        reply_markup=reply_markup
    )
    logger.info(f"Вопрос {current_question_index + 1} отправлен пользователю {user_id}.")


async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обработчик нажатий на inline-кнопки (ответы на вопросы викторины).
    """
    query = update.callback_query # Объект, содержащий информацию о нажатой кнопке.
    user_id = query.from_user.id # ID пользователя, нажавшего кнопку.

    # Обязательно нужно ответить на callback_query, чтобы клиент Telegram
    # понял, что нажатие обработано (исчезнут "часики" на кнопке).
    await query.answer()

    if user_id not in user_state: # Если состояние пользователя не найдено (например, викторина не начата).
        # Редактируем сообщение, к которому была прикреплена кнопка.
        await query.edit_message_text("Викторина не запущена. Нажмите /quiz, чтобы начать новую игру.")
        return

    current_question_index = user_state[user_id]["current_question"]
    questions = context.bot_data.get('quiz_questions')

    # Проверка, не отвечает ли пользователь на вопрос, который уже "пройден" или если викторина завершена.
    if current_question_index >= len(questions):
        await query.edit_message_text(
            f"Викторина уже завершена! Ваш счет: {user_state[user_id]['score']} из {len(questions)}."
        )
        return

    question_data = questions[current_question_index] # Данные текущего вопроса.
    correct_answer_index = question_data.get("correct_index") # Правильный индекс.
    correct_answer_text = question_data.get("correct_answer") # Текст правильного ответа.
    selected_answer_index = int(query.data) # Индекс, выбранный пользователем (из callback_data).

    response_message_suffix = "" # Дополнение к сообщению с вопросом (результат ответа).
    if selected_answer_index == correct_answer_index: # Если ответ правильный.
        user_state[user_id]["score"] += 1 # Увеличиваем счет.
        response_message_suffix = f"✅ Правильно!"
        logger.info(f"Пользователь {user_id} ответил правильно на вопрос {current_question_index + 1}.")
    else: # Если ответ неправильный.
        # Markdown используется для выделения правильного ответа (*текст* -> курсив).
        response_message_suffix = f"❌ Неправильно. Правильный ответ: *{correct_answer_text}*."
        logger.info(f"Пользователь {user_id} ответил неправильно на вопрос {current_question_index + 1}.")

    # Формируем полный текст для отредактированного сообщения.
    current_score_text = f"Ваш текущий счет: {user_state[user_id]['score']} из {len(questions)}."
    full_response_text = f"{query.message.text}\n\n{response_message_suffix}\n{current_score_text}"

    try:
        # Редактируем сообщение с вопросом:
        # - Добавляем результат ответа.
        # - Убираем кнопки, передавая пустую InlineKeyboardMarkup.
        await query.edit_message_text(
            text=full_response_text,
            reply_markup=InlineKeyboardMarkup([]), # Пустая клавиатура, чтобы убрать кнопки после ответа.
            parse_mode='Markdown' # Включаем разбор Markdown для форматирования.
        )
    except Exception as e:
        # Если редактирование не удалось (например, сообщение слишком старое),
        # просто отправляем результат новым сообщением.
        logger.warning(f"Не удалось отредактировать сообщение после ответа (пользователь {user_id}): {e}. Отправляем результат новым сообщением.")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"{response_message_suffix}\n{current_score_text}", # Отправляем только результат.
            parse_mode='Markdown'
        )

    user_state[user_id]["current_question"] += 1 # Переходим к следующему вопросу.
    # Отправляем следующий вопрос или сообщение о завершении викторины.
    # `update` (CallbackQuery) передается, чтобы `send_question` мог использовать `update.effective_chat.id` и `update.effective_user.id`.
    await send_question(update, context)


async def post_init_setup(application: Application) -> None:
    """
    Асинхронная функция, выполняемая один раз после инициализации Application,
    но перед началом приема обновлений от Telegram.
    Используется для подключения к БД и предварительной загрузки данных (вопросов).
    """
    logger.info("Выполняется post_init_setup...")
    if not await connect_to_mongodb(): # Пытаемся подключиться к MongoDB.
        logger.critical("Критическая ошибка: не удалось подключиться к MongoDB при запуске. Викторина не будет работать с вопросами из БД.")
        # Бот продолжит работу, но вопросы не будут загружены.
        application.bot_data['quiz_questions'] = [] # Устанавливаем пустой список вопросов.
        return

    # Загружаем вопросы из БД и сохраняем их в application.bot_data.
    # application.bot_data - это словарь, доступный во всех обработчиках через context.bot_data.
    # Это позволяет загрузить вопросы один раз при старте, а не при каждом вызове /quiz.
    loaded_questions = await load_questions_from_db()
    application.bot_data['quiz_questions'] = loaded_questions
    if not loaded_questions:
        logger.warning("Внимание: нет корректных вопросов в базе данных или не удалось их загрузить. Викторина будет пуста.")
    else:
        logger.info(f"Успешно загружено {len(loaded_questions)} вопросов в bot_data при запуске.")


def main() -> None:
    """
    Основная функция. Запускает бота.
    """
    logger.info("Инициализация приложения бота...")
    # Создаем экземпляр Application (основной объект бота) с помощью ApplicationBuilder.
    # .token(TOKEN) - устанавливает токен бота.
    # .post_init(post_init_setup) - регистрирует функцию, которая выполнится после инициализации, но до запуска поллинга.
    application = ApplicationBuilder().token(TOKEN).post_init(post_init_setup).build()

    # Регистрируем обработчики команд.
    # CommandHandler("start", start) означает: когда бот получит команду /start, вызвать функцию start.
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("quiz", quiz))

    # Регистрируем обработчик для нажатий на inline-кнопки.
    # CallbackQueryHandler(check_answer) означает: при любом нажатии на inline-кнопку вызвать функцию check_answer.
    application.add_handler(CallbackQueryHandler(check_answer))

    logger.info("Бот запущен и готов принимать обновления...")
    # Запускаем бота. Он начинает постоянно опрашивать серверы Telegram на наличие новых сообщений (поллинг).
    # allowed_updates=Update.ALL_TYPES - бот будет получать все типы обновлений.
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    # Эта стандартная конструкция Python означает, что функция main() будет вызвана,
    # только если этот скрипт запущен напрямую (а не импортирован как модуль в другой скрипт).
    main()
