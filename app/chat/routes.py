from app.models import Message, Resume, outgoing_message, incoming_message, UserData, Vacancy, UserVacancy
from flask import request, jsonify, session, Response
from flask_login import current_user
from flask_socketio import emit
from sqlalchemy.testing.plugin.plugin_base import logging
from app import socketio, Config, db
from app.chat import bp as chat_bp
from app.chat import texts
from app.chat.openai_proxy import OpenAIProxy
from app.models import User
import logging
from openai import OpenAI
from parse_hh_data import download, parse as hh_parse
from app.yagpt.yagpt import YAGPT
import threading
import json
import re


openai = OpenAI(api_key=Config.OPENAI_API_KEY)
openai_proxy_client = OpenAIProxy()

@socketio.on('connect', namespace='/secure_chat')
def handle_connect_secure():
    if current_user.is_authenticated:
        if current_user.first_name and current_user.last_name:
            if not current_user.get_main_vacancy():
                return emit_vacancies_menu()
            else:
                emit('response', {'text': texts.welcome(current_user), 'type': 'text'})
        else:
            emit('fillInfo')
    else:
        emit('response', {'text': texts.HELLO_LOGOUT, 'type': 'text'})

@socketio.on('disconnect', namespace='/secure_chat')
def handle_disconnect_secure():
    logging.info('Пользователь отключился')

@socketio.on('message', namespace='/secure_chat')
@outgoing_message
def handle_message_secure(data):
    if current_user.is_authenticated:
        ya_gpt_client = YAGPT()
        emitNaitaAction('читает...')

        # нет первоначальной информации - даем модальное окно для ввода имени, фамилии и ссылки на резюме
        if not (current_user.first_name and current_user.last_name):
            return emit('fillInfo')

        # если у пользователя нет текущей вакансии
        if not current_user.get_main_vacancy():
            return emit_vacancies_menu()

        # если пришла ссылка
        if hh_links := extract_and_validate_hh_resume_link(data["content"]):
            emit('naitaAction', {'text': 'анализирует профиль на ХХ...'})
            if save_hh_resume(hh_links[0]):
                return emit('response', {'text': texts.RESUME_SAVED, 'type': 'text'})
            return emit('response', {'text': texts.RESUME_NOT_SAVED, 'type': 'text'})

        # если в сессии есть текущий id информации о пользователе - записываем туда ответ пользователя
        if ud_id:=session.get('current_user_data_id', None):
            ud = UserData.query.get(int(ud_id))
            ud.text = data["content"]
            db.session.commit()

        # анализируем в Я ГПТ текст, разносим его по разным полям, возвращаем в JSON
        response = ya_gpt_client.completion(texts.clearing_and_isolating(data["content"])).replace("```", "").strip()
        if response:
            emitNaitaAction('анализирует...')
            data = json.loads(response)
            try:
                current_user.add_user_data(data) # сохраняем данные о пользователе
            except Exception as e:
                logging.warning(f'Не удалось сохранить данные о пользователе. {e}')

            # ya gpt проверяет, какой инфы не хватает у юзера
            additional_info = json.loads(ya_gpt_client.completion(f'{texts.YA_GPT_DATA_REQUEST}\n\n{current_user.get_user_data()}').replace("```", "").strip())
            # print(additional_info)
            question = ''
            if additional_info:
                try:
                    question = additional_info.get('question_text', '')
                    ud = current_user.add_user_data_question(additional_info)
                    session['current_user_data_id'] = str(ud)
                except Exception as e:
                    pass
            else:
                try:
                    session.pop('current_user_data_id', None)
                except Exception as e:
                    pass
            if additional_info.get('filled', False) or current_user.profile_filled:
                if not current_user.profile_filled:
                    current_user.profile_filled = True
                    emit('profile-filled')
                    db.session.commit()
                try:
                    session.pop('current_user_data_id', None)
                except Exception as e:
                    pass
                question = ''

                if not current_user.coincidences_done:
                    emit('coincidences-done')
                    # get_vacancies_coincidences()
                    return get_main_vacancy_coincidence()

            emitNaitaAction('печатает...')
            content = f'Запрос: {data.get("secure_request", "")}\n\nПользователь: {current_user.get_user_data()}'
            response = f'{openai_proxy_client.ask_assistant(content, current_user)}\n\n\n{question if question else ""}'.strip()

            emit_response({'text': final_clean_text(response), 'type': 'text'})
    else:
        emitNaitaAction('печатает...')
        emit('response', {'text': texts.REGISTER_PLEASE, 'type': 'text'})

@socketio.on('fillInfo', namespace='/secure_chat')
def secure_chat_fill_info(data):
    current_user.first_name = data.get('first_name', '')
    current_user.last_name = data.get('last_name', '')
    current_user.pers_data_consent = True
    db.session.commit()

    # парсим резюме, если ссылка есть
    if resume_link:=data.get('cv_link', None):
        if save_hh_resume(resume_link):
            emit_response({'text': texts.RESUME_SAVED, 'type': 'text'})
        else:
            emit_response({'text': texts.RESUME_NOT_SAVED, 'type': 'text'})

    if not current_user.get_main_vacancy():
        return emit_vacancies_menu()
    else:
        emit_response({'text': texts.lets_continue_with_vacancy(current_user), 'type': 'text'})

@socketio.on('delMyMessages', namespace='/secure_chat')
def del_messages_history():
    current_user.first_name = ''
    current_user.second_name = ''
    current_user.last_name = ''
    current_user.phone = ''
    current_user.city = ''
    current_user.relocation_ready = ''
    current_user.remote_ready = ''
    current_user.professional_experience = ''
    current_user.skills = ''
    current_user.education = ''
    current_user.profile_assessment = ''
    current_user.profile_filled = False
    current_user.coincidences_done = False

    for message in Message.query.filter((Message.sender_id == current_user.id) | (Message.receiver_id == current_user.id)).all():
        db.session.delete(message)

    for ud in UserData.query.filter(UserData.user_id == current_user.id).all():
        db.session.delete(ud)

    for uv in UserVacancy.query.filter(UserVacancy.user_id == current_user.id).all():
        db.session.delete(uv)

    for resume in Resume.query.filter(Resume.user == current_user.id).all():
        db.session.delete(resume)

    db.session.commit()
    emit('response', {'text': 'Перезагрузи страницу', 'type': 'text'})
    return Response(status=200)

@socketio.on('messageBtnClick', namespace='/secure_chat')
def handle_message_btn_click(data):
    # ответить пользователю обязательный текст
    emit('response', {'text': texts.VACANCY_SELECTED, 'type': 'text', 'disable_input': True})

    message: Message = Message.query.get(int(data['mid']))
    message.btns = None
    db.session.commit()
    emit('messageBtnClickReceived', {'mid': message.id})
    if callback:=data.get('callback'):
        if callback == 'vacancy':
            vacancy = Vacancy.query.filter(Vacancy.name == data.get('text')).first()
            user_vacancy = UserVacancy.query.filter(UserVacancy.user_id == current_user.id,
                                                    UserVacancy.vacancy_id == vacancy.id).first()
            if not user_vacancy:
                user_vacancy = UserVacancy()
                user_vacancy.user_id = current_user.id
                user_vacancy.vacancy_id = vacancy.id
                db.session.add(user_vacancy)
            user_vacancy.is_main = True
            db.session.commit()

@chat_bp.get('/messages')
def messages_history():
    messages_json = []
    if current_user.is_authenticated:
        messages = Message.query.filter(
            (Message.receiver_id == current_user.id) | (Message.sender_id == current_user.id)).order_by(
            Message.sent).all()
        # Convert the list of messages to a list of JSON objects
        messages_json = [
            {
                'id': message.id,
                'sender_id': message.sender_id,
                'receiver_id': message.receiver_id,
                'type': message.message_type,
                'text': message.text,
                'content': message.content,
                'callback': message.callback,
                'btns': message.btns,
                'sent': message.sent.isoformat() if message.sent else None
            }
            for message in messages
        ]
    return jsonify(messages_json), 200

@incoming_message
def emit_response(data, message=None):
    if message:
        data['id'] = message.id
    emit('response', data)

def emitNaitaAction(text):
    emit('naitaAction', {'text': text})

def is_email_address(text):
    return bool(re.match(r'^(?:(?!.*\.\.)([a-zA-Z0-9!#$%&\'*+/=?^_`{|}~-]+(?:\.[a-zA-Z0-9!#$%&\'*+/=?^_`{|}~-]+)*)|(\"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\\\[\x01-\x09\x0b\x0c\x0e-\x7f])\"))@(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}|(?:\[(?:(?:25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\]))$', text))

def is_5digit_code(text):
    return bool(re.match(r'^\d{5}$', text))

def extract_and_validate_hh_resume_link(text):
    pattern = r"https://hh\.ru/resume/[a-zA-Z0-9_-]+(?:\?.*)?"
    matches = re.findall(pattern, text)
    return matches

def extract_hh_resume_id(url):
    pattern = r"https://hh\.ru/resume/([a-zA-Z0-9_-]+)"
    match = re.search(pattern, url)
    if match:
        return match.group(1)  # Возвращаем ID
    return None  # Если ID не найден

def save_hh_resume(link):
    if cv_id := extract_hh_resume_id(link):
        try:
            hh_cv = download.resume(cv_id)
            hh_cv = hh_parse.resume(hh_cv)

            if not (
                    resume := Resume.query.filter(Resume.user == current_user.id, Resume.source == 'hh').first()):
                resume = Resume()
            resume.user = current_user.id
            resume.source = 'hh'
            resume.link = link

            resume.data = json.dumps({})
            if not resume in db.session:
                db.session.add(resume)
            db.session.commit()

            resume.data = hh_cv
            db.session.commit()
            message = Message()
            message.text = texts.RESUME_SAVED
            message.message_type = 'text'
            message.receiver_id = current_user.id
            db.session.add(message)
            db.session.commit()
            return True
        except Exception as e:
            logging.error('Не удалось сохранить резюме с ХХ')
    return False

def final_clean_text(text):
    # Используем регулярное выражение для удаления вхождений
    cleaned_content = re.sub(r'\u3010.*?\u3011', '', text)

    # Удаляем сами символы 【 и 】
    cleaned_content = re.sub(r'[\u3010\u3011]', '', cleaned_content)

    return cleaned_content

def get_main_vacancy_coincidence():
    ya_gpt_client = YAGPT()
    emitNaitaAction('анализирует соответствие запрошенной вакансии...')
    vacancy = current_user.get_main_vacancy()
    user_info = current_user.get_user_data()

    prompt = f'''Посмотри информацию о вакансии, на которую я претендую:
{vacancy.get_json()}

Посмотри информацию обо мне:
{user_info}

Оцени уровень соответствия меня этой вакансии по шкале от 1 до 10?

Верни ответ в виде JSON объекта:
{{"vid": id вакансии,    
"name": название вакансии,
"value": оценка соответствия меня вакансии по шкале от 1 до 10,
"positive": объяснение почему я соответствую этой вакансии (в формате markdown),
"negative": объяснение чего мне не хватает для полного соответствия этой вакансии (в формате markdown),
"recommendations": рекомендации на будущее (в формате markdown)}}

Обращайся ко мне на ты.
        '''

    response = ya_gpt_client.completion(prompt).replace("```", "").strip()
    if response:
        coincidence = json.loads(response)
        user_vacancy: UserVacancy = UserVacancy.query.filter(
            UserVacancy.user_id == current_user.id,
            UserVacancy.vacancy_id == vacancy.id).first()
        user_vacancy.value = int(coincidence['value'])
        user_vacancy.positive = coincidence['positive']
        user_vacancy.negative = coincidence['negative']
        user_vacancy.recommendations = coincidence['recommendations']

        current_user.coincidences_done = True
        db.session.commit()

        return send_main_vacancy_coincidence_analitics_result(user_vacancy)

def send_main_vacancy_coincidence_analitics_result(user_vacancy):
    emit_response({
        'text': f'##### Результаты анализа\n\n###### Положительные стороны:\n{user_vacancy.positive}\n\n###### Отрицательные нюансы:\n{user_vacancy.negative}',
        'type': 'text',
        'disabled_input': True,
    })
    if user_vacancy.value >= Config.MIN_COINCEDENCE_VALUE:
        emit_response({
            'text': texts.main_vacancy_coincedence_analysis_success(current_user),
            'type': 'text'
        })
    else:
        emit_response({
            'text': texts.main_vacancy_coincedence_analysis_fail(current_user, user_vacancy),
            'type': 'text'
        })

def get_vacancies_coincidences():
    vacansies_list = [v.get_json() for v in Vacancy.query.all()]
    user_info = current_user.get_user_data()

    prompt = f'''Посмотри информацию об открытых вакансиях:
{vacansies_list}
    
Посмотри информацию обо мне:
{user_info}

На какие из вакансий я подхожу и с каким уровнем соответствия по шкале от 1 до 10?

Верни ответ в виде массива JSON объектов:
[
{{"vid": id вакансии,
"name": название вакансии,
"value": оценка соответствия меня вакансии по шкале от 1 до 10,
"positive": объяснение почему я соответствую этой вакансии,
"negative": объяснение чего мне не хватает для полного соответствия этой вакансии}}
]

Обращайся ко мне на ты.
    '''

    thread = threading.Thread(target=get_vacancies_coincidences_background, args=(prompt, current_user.id, ))
    thread.start()
    return

def get_vacancies_coincidences_background(prompt, uid):
    from app import create_app
    ya_gpt_client = YAGPT()
    response = ya_gpt_client.completion(prompt).replace("```", "").strip()

    if response:
        coincidences = json.loads(response)

        with create_app(Config).app_context():
            for c in coincidences:
                try:
                    user_vacancy = UserVacancy()
                    user_vacancy.vacancy_id = c['vid']
                    user_vacancy.user_id = uid
                    user_vacancy.positive = c['positive']
                    user_vacancy.negative = c['negative']
                    user_vacancy.value = int(c['value'])
                    db.session.add(user_vacancy)
                    db.session.commit()
                except Exception as e:
                    logging.error(f'Не удалось сохранить соответствие вакансии пользователю {uid}, {e}')
            user: User = User.query.get(uid)
            user.coincidences_done = True
            db.session.commit()
            logging.info(f'Для пользователя {uid} сохранены соответствия вакансиям')
            return
    logging.info(f'Для пользователя {uid} не удалось сохранить соответствие вакансиям.')

def emit_vacancies_menu():
    vacancies = [v.name for v in Vacancy.query.all()]
    return emit_response({'text': texts.SELECT_VACANCIES, 'type': 'text', 'btns': vacancies, 'callback': 'vacancy'})