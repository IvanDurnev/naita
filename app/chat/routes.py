from flask import request, jsonify, session, Response
from flask_login import current_user, login_user
from flask_socketio import emit, join_room, leave_room
from sqlalchemy.testing.plugin.plugin_base import logging
from sqlalchemy.testing.suite.test_reflection import users
from app import socketio, login, Config, db
from app.models import Message
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


openai = OpenAI(api_key=Config.OPENAI_API_KEY)
openai_proxy_client = OpenAIProxy()

@socketio.on('connect', namespace='/chat')
def handle_connect():
    if current_user.is_authenticated:
        emit('response', {'message': texts.welcome(current_user), 'type': 'text'})
    else:
        emit('response', {'message': texts.LOGIN_PLEASE, 'type': 'text'})

@socketio.on('disconnect', namespace='/chat')
def handle_connect():
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
            emit('response', {'message': response, 'type': 'text'})
        return
    else:
        if message_type == 'text' and is_email_address(message_text):
            user = User.query.filter(User.email == message_text.lower()).first()
            if not user:
                user = User()
                user.email = message_text.lower()
                db.session.commit()
            user.set_auth_code()
            user.send_auth_code()
            session['user_email'] = user.email
            emit('response', {'message': texts.EMAIL_CODE, 'type': 'text', 'email': user.email})
            return
        if message_type == 'text' and is_5digit_code(message_text) and email and not current_user.is_authenticated:
            # проверяем 5-тизначный код из email
            user = User.query.filter(User.email == session['user_email']).first()
            # user = User.query.filter(User.email == email).first()
            if user and user.verify_auth_code(message_text):
                # Авторизация успешна
                emit('response', {'message': 'Логиним', 'type': 'text'})
                login_user(user, remember=True)
                session['logged_in'] = True
                session.modified = True  # Убедитесь, что сессия обновлена
                emit('reload')
                return "Успешная авторизация", 200
            else:
                emit('response', {'message': texts.WRONG_AUTH_CODE, 'type': 'text'})
            return

        emit('response', {'message': texts.LOGIN_PLEASE, 'type': 'text'})

@socketio.on('delMyMessages', namespace='/chat')
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

    messages = Message.query.filter((Message.sender_id == current_user.id) | (Message.receiver_id == current_user.id)).all()
    for message in messages:
        db.session.delete(message)
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

def is_email_address(text):
    return bool(re.match(r'^(?:(?!.*\.\.)([a-zA-Z0-9!#$%&\'*+/=?^_`{|}~-]+(?:\.[a-zA-Z0-9!#$%&\'*+/=?^_`{|}~-]+)*)|(\"(?:[\x01-\x08\x0b\x0c\x0e-\x1f\x21\x23-\x5b\x5d-\x7f]|\\\\[\x01-\x09\x0b\x0c\x0e-\x7f])\"))@(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}|(?:\[(?:(?:25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[0-1]?[0-9][0-9]?)\]))$', text))

def is_5digit_code(text):
    return bool(re.match(r'^\d{5}$', text))