import json

from flask import request, jsonify, session, Response
from flask_login import current_user, login_user
from flask_socketio import emit, join_room, leave_room
from sqlalchemy.testing.plugin.plugin_base import logging
from sqlalchemy.testing.suite.test_reflection import users
from app import socketio, login, Config, db
from app.models import Message, Resume, outgoing_message, incoming_message, UserData, Vacancy, UserVacancy
from app.chat import bp as chat_bp
from app.chat import texts
from app.chat.openai_proxy import OpenAIProxy
from app.models import User
import random
import logging
# from time import sleep, thread_time
from openai import OpenAI
import re
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.sqltypes import Boolean
from parse_hh_data import download, parse as hh_parse
from app.yagpt.yagpt import YAGPT
import threading


openai = OpenAI(api_key=Config.OPENAI_API_KEY)
openai_proxy_client = OpenAIProxy()

@socketio.on('connect', namespace='/secure_chat')
def handle_connect_secure():
    if current_user.is_authenticated:
        if current_user.first_name and current_user.last_name:
            emit('response', {'message': texts.welcome(current_user), 'type': 'text'})
        else:
            emit('fillInfo')
    else:
        emit('response', {'message': texts.HELLO_LOGOUT, 'type': 'text'})

@socketio.on('disconnect', namespace='/secure_chat')
def handle_disconnect_secure():
    logging.info('Пользователь отключился')

@socketio.on('message', namespace='/chat')
def handle_message(data):
    message_type = data.get('type', '')
    message_text = data.get('content', '')
    message_callback = data.get('callback', '')
    email = data.get('email', '')
    if current_user.is_authenticated:
        message = Message()
        message.text = message_text
        message.message_type = message_type
        message.sender_id = current_user.id
        db.session.add(message)
        db.session.commit()

        # если пришла ссылка
        if hh_links:=extract_and_validate_hh_resume_link(message_text):
            emit('hh_resume_reviewing')
            if cv_id:=extract_hh_resume_id(hh_links[0]):
                try:
                    hh_cv = download.resume(cv_id)
                    hh_cv = hh_parse.resume(hh_cv)
                    if not (resume:=Resume.query.filter(Resume.user==current_user.id, Resume.source=='hh').first()):
                        resume = Resume()
                    resume.user = current_user.id
                    resume.source = 'hh'

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
                    emit('response', {'message': texts.RESUME_SAVED, 'type': 'text'})
                except Exception as e:
                    logging.error('Не удалось сохранить резюме с ХХ')
                    emit('response', {'message': texts.RESUME_NOT_SAVED, 'type': 'text'})
            return

        emit('typing')

        # если в сообщении пришел callback
        if message_callback:
            if attr:=getattr(User, message_callback, None):
                try:
                    setattr(current_user, message_callback, message_text)
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    message = Message()
                    message.text = 'ваш ответ не понятен, напишите еще раз, пожалуйста'
                    message.message_type = 'text'
                    message.receiver_id = current_user.id
                    message.callback = message_callback
                    db.session.add(message)
                    db.session.commit()
                    emit('response', {'message': 'ваш ответ не понятен, напишите еще раз, пожалуйста', 'type': 'text', 'callback': message_callback})
                    logging.warning(f'Не удалось сохранить данные пользователя: поле {message_callback}, данные: {message_text}.\n {e}')

        # проверяем, что у пользователя заполнен профиль.
        # если не заполнен запрашиваем данные
        if field:=current_user.get_first_unfilled_field():
            message = Message()
            message.text = field.get('question')
            message.message_type = 'text'
            message.receiver_id = current_user.id
            message.callback = field.get('field')
            db.session.add(message)
            db.session.commit()
            return emit('response', {'message': field.get('question'), 'type': 'text', 'callback': field.get('field')})

        # если заполнен - передаем запрос в AI
        # если профиль заполнен и не проведен скрининг - проводим скрининг
        if not current_user.profile_assessment:
            emit('analytics')
            # response = current_user.check_candidate_v1()
            response = current_user.check_candidate_v2()
            emit('response', {'message': response, 'type': 'text'})
            return Response(status=200)

        # тут обрабатываем входящее сообщение в зависимости от типа: текст или файл+-текст

        assistant_id = Config.OPENAI_GPT_ASSISTANT_ID
        thread_id = current_user.gpt_thread
        if not thread_id:
            thread_id = openai_proxy_client.create_thread()
            current_user.gpt_thread = thread_id
            db.session.commit()
        content = message_text + f'\n\n{current_user.get_profile_txt()}'

        response = openai_proxy_client.ask_assistant(assistant_id, content, thread_id)
        if response:
            message = Message()
            message.text = response
            message.message_type = 'text'
            message.receiver_id = current_user.id
            db.session.add(message)
            db.session.commit()
            emit('response', {'message': response, 'type': 'text'})
        return
    else:
        emit('response', {'message': texts.HELLO_LOGOUT, 'type': 'text'})

@socketio.on('message', namespace='/secure_chat')
@outgoing_message
def handle_message_secure(data):
    ya_gpt_client = YAGPT()
    if current_user.is_authenticated:
        # нет первоначальной инфы - даем модалку для ввода имени, фамилии и ссылки на резюме
        if not (current_user.first_name and current_user.last_name):
            return emit('fillInfo')

        # если пришла ссылка
        if hh_links := extract_and_validate_hh_resume_link(data["content"]):
            emit('naitaAction', {'text': 'анализирует профиль на ХХ...'})
            if save_hh_resume(hh_links[0]):
                return emit('response', {'message': texts.RESUME_SAVED, 'type': 'text'})
            return emit('response', {'message': texts.RESUME_NOT_SAVED, 'type': 'text'})

        emit('naitaAction', {'text': 'читает...'})

        # если в сессии есть текущий id инфы о пользователе - записываем туда ответ пользователя
        if ud_id:=session.get('current_user_data_id', None):
            ud = UserData.query.get(int(ud_id))
            ud.text = data["content"]
            db.session.commit()

        # анализируем в Я ГПТ текст, разносим его по разным полям, возвращяем в JSON
        response = ya_gpt_client.completion(texts.clearing_and_isolating(data["content"])).replace("```", "").strip()
        if response:
            emit('naitaAction', {'text': 'анализирует...'})
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
                    get_vacancies_coincidences()
                    emit('coincidences-done')

            emit('naitaAction', {'text': 'печатает...'})
            content = f'Запрос: {data.get("secure_request", "")}\n\nПользователь: {current_user.get_user_data()}'
            response = f'{openai_proxy_client.ask_assistant(content, current_user)}\n\n\n{question if question else ""}'.strip()

            emit_response({'message': final_clean_text(response), 'type': 'text'})
    else:
        emit('naitaAction', {'text': 'печатает...'})
        emit('response', {'message': texts.REGISTER_PLEASE, 'type': 'text'})

@socketio.on('fillInfo', namespace='/secure_chat')
def secure_chat_fill_info(data):
    # print(data)
    current_user.first_name = data.get('first_name', '')
    current_user.last_name = data.get('last_name', '')
    current_user.pers_data_consent = True
    db.session.commit()
    # парсим резюме, если ссылка есть
    if resume_link:=data.get('cv_link', None):
        if save_hh_resume(resume_link):
            emit('response', {'message': texts.RESUME_SAVED, 'type': 'text'})
        else:
            emit('response', {'message': texts.RESUME_NOT_SAVED, 'type': 'text'})

    emit('response', {'message': texts.welcome(current_user), 'type': 'text'})

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

    messages = Message.query.filter((Message.sender_id == current_user.id) | (Message.receiver_id == current_user.id)).all()
    for message in messages:
        db.session.delete(message)

    user_data = UserData.query.filter(UserData.user_id == current_user.id).all()
    for ud in user_data:
        db.session.delete(ud)

    resumes = Resume.query.filter(Resume.user == current_user.id).all()
    for resume in resumes:
        db.session.delete(resume)

    db.session.commit()
    emit('response', {'message': 'Перезагрузи страницу', 'type': 'text'})
    return Response(status=200)

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
                'message_type': message.message_type,
                'text': message.text,
                'content': message.content,
                'sent': message.sent.isoformat() if message.sent else None
            }
            for message in messages
        ]
    return jsonify(messages_json), 200

@incoming_message
def emit_response(data):
    emit('response', data)

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