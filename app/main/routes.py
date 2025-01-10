import datetime
import json
import requests
from sqlalchemy.testing.plugin.plugin_base import logging
from flask_socketio import emit
from app import db, sess, redis_client, mail
from app.chat.routes import emit_response
from app.main import bp
from flask import render_template, redirect, request, Response, jsonify, session
from app.models import User, load_user, Message, UserVacancy
from flask_login import login_user, logout_user, current_user
from app.yagpt.yagpt import YAGPT
from config import Config
import logging
import os
import mimetypes
from flask_mail import Message as MailMessage


@bp.get('/')
def index_main():
    if current_user.is_authenticated:
        # проверить наличие персонального ассистента
        if not current_user.ya_assistant_id:
            # создать персонального ассистента
            current_user.create_personal_ya_assistant()

    return render_template(template_name_or_list='main/index.html',
                           vk_redirect_uri=Config.VK_ID_REDIRECT_URI)

@bp.get('/ping')
def test():
    # ya = YAGPT()
    # ya.ask_assistant('Расскажи о банке', current_user)
    return 'pong'

@bp.get('/cv')
def cv():
    from weasyprint import HTML
    import pdfkit
    from app.chat import texts
    response = texts.get_cv_fields(current_user)
    info = json.loads(response)

    # проверяем нужные поля на то, что там текст, а не список
    for item in info['education']:
        if type(item['profession']) is list:
            item['profession'] = ', '.join(item['profession'])

    # путь сохранения резюме пользователя
    cv_saving_path = os.path.join(Config.STATIC_FOLDER, 'users', str(current_user.id))
    cv_file_path = os.path.join(cv_saving_path, 'cv.pdf')
    if not os.path.exists(cv_saving_path):
        os.makedirs(cv_saving_path)

    # генерация с weasyprint
    # HTML(string=render_template('main/cv.html', info=info)).write_pdf(cv_file_path)

    # Генерация PDF с использованием pdfkit
    html_content = render_template('main/cv.html', info=info)
    pdfkit.from_string(html_content, cv_file_path)

    # добавить файл с резюме в файлы ассистента
    ya_gpt_client = YAGPT()
    try:
        ya_gpt_client.del_search_index(current_user.current_search_index['id'])
        current_user.current_search_index = None
        logging.info(f'Удален неактуальный поисковый индекс пользователя {current_user.id}')
    except Exception as e:
        logging.info(f'Не удалось удалить поисковый индекс пользователя {current_user.id} {e}')
    try:
        ya_gpt_client.del_assistant(current_user.ya_assistant_id)
        current_user.ya_assistant_id = ''
        logging.info(f'Удален неактуальный ассистент пользователя {current_user.id}')
    except Exception as e:
        logging.info(f'Не удалось удалить старый ассистент пользователя {current_user.id} {e}')

    current_user.current_ya_thread = ''
    db.session.commit()
    current_user.create_personal_ya_assistant()

    # отправляем резюме на почту пользователю
    try:
        msg = MailMessage(
            subject='CV',
            sender=Config.MAIL_DEFAULT_SENDER,
            recipients=[current_user.email]
        )
        msg.body = texts.CV_SENT_BY_EMAIL_EMAIL_BODY

        # Добавление файла как вложения
        with open(cv_file_path, 'rb') as f:
            msg.attach(
                filename="cv_by_Naita.pdf",
                content_type="application/pdf",
                data=f.read()
            )

        mail.send(msg)
    except Exception as e:
        logging.info(f'Не удалось отправить email c CV. {e}')

    return render_template('main/cv.html', info=info)

@bp.get('/recommendations')
def recommendations():
    from weasyprint import HTML
    from app.chat import texts
    mv = current_user.get_main_vacancy()
    user_vacancy = UserVacancy.query.filter(UserVacancy.user_id == current_user.id,
                                            UserVacancy.vacancy_id == mv.id).first()
    from dadata import Dadata
    token = Config.DADATA_TOKEN
    secret = Config.DADATA_SECRET
    dadata = Dadata(token, secret)
    result = dadata.clean("name", current_user.name)

    info = {
        'name': current_user.name,
        'name_rod': result.get('result_genitive', current_user.name),
        'vacancy': mv.name,
        'negative': user_vacancy.negative,
        'positive': user_vacancy.positive,
        'recommendations': user_vacancy.recommendations,
        'date': datetime.datetime.now().strftime("%d.%m.%Y")
    }

    # путь сохранения рекомендаций
    recommendations_saving_path = os.path.join(Config.STATIC_FOLDER, 'users', str(current_user.id), 'recommendations')
    recommendations_file_path = os.path.join(recommendations_saving_path, f'{mv.name}.pdf')
    if not os.path.exists(recommendations_saving_path):
        os.makedirs(recommendations_saving_path)

    HTML(string=render_template('main/recommendations.html', info=info)).write_pdf(recommendations_file_path)

    # отправить рекомендации пользователю на почту
    try:
        msg = MailMessage(
            subject=f'Рекомендации по вакансии {mv.name}',
            sender=Config.MAIL_DEFAULT_SENDER,
            recipients=[current_user.email]
        )
        msg.body = texts.RECOMMENDATIONS_SENT_BY_EMAIL_EMAIL_BODY

        # Добавление файла как вложения
        with open(recommendations_file_path, 'rb') as f:
            msg.attach(
                filename=f'{mv.name}.pdf',
                content_type="application/pdf",
                data=f.read()
            )

        mail.send(msg)
    except Exception as e:
        logging.info(f'Не удалось отправить email c рекомендациями. {e}')

    return render_template('main/recommendations.html', info=info)

@bp.post('/verify_email')
def verify_email():
    try:
        if not (user:= User.query.filter(User.email == request.json['email']).first()):
            user = User()
            user.email = request.json['email']
            db.session.add(user)
            db.session.commit()

        user.set_auth_code()
        user.send_auth_code()
        session['user_email'] = user.email
        return Response(status=200)
    except Exception as e:
        logging.error(f'Не удалось создать/найти пользователя по электронной почте. {e}')
        return Response(status=500)

@bp.post('/verify_email_code')
def verify_email_code():
    try:
        if not (user:= User.query.filter(User.email == session['user_email']).first()):
            return Response(status=404)

        if user.verify_auth_code(request.json['code']):
            login_user(user, remember=True)
            return Response(status=200)
        return Response(status=403)
    except Exception as e:
        logging.error(f'Не удалось залогинить пользователя по электронной почте. {e}')
        return Response(status=500)

@bp.post('/vk_login')
def index_vk_login():
    try:
        url = 'https://id.vk.com/oauth2/user_info'
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        payload = {
            'access_token': request.json['access_token'],
            'client_id': Config.VK_APP_ID
        }

        response = requests.post(url=url, headers=headers, data=payload)
        vk_user = response.json()

        user = User.query.filter(User.email == vk_user['user']['email']).first()
        if not user:
            user = User()
        if not user.name:
            user.name = f"{vk_user['user']['first_name']} {vk_user['user']['last_name']}"
        user.email = vk_user['user']['email']
        user.sex = vk_user['user']['sex']
        user.birthday = vk_user['user']['birthday']
        user.vk_user_id = vk_user['user']['user_id']
        user.vk_first_name = vk_user['user']['first_name']
        user.vk_last_name = vk_user['user']['last_name']
        user.vk_verified = vk_user['user']['verified']
        user.vk_avatar = vk_user['user']['avatar']
        if user not in db.session:
            db.session.add(user)
        db.session.commit()

        # Скачивание аватара и сохранение в папке пользователя
        avatar_url = vk_user['user']['avatar']
        avatar_response = requests.get(avatar_url)
        if avatar_response.status_code == 200:
            avatar_path = os.path.join(Config.STATIC_FOLDER, 'users', str(user.id))
            if not os.path.exists(avatar_path):
                os.makedirs(avatar_path)
            with open(os.path.join(avatar_path, 'avatar.jpg'), 'wb') as avatar_file:
                avatar_file.write(avatar_response.content)

        login_user(user, remember=True)

        return Response(status=200)
    except Exception as e:
        logging.error(f'Не удалось залогинить пользователя {e}')
    return Response(status=500)

@bp.get('/logout')
def logout():
    logout_user()
    return Response(status=200)

@bp.post('/chat_event')
def chat_event():
    data = request.json
    print(data)
    if data and 'data' in data and 'message' in data['data']:
        message = data['data']['message']
        user_id = data['data']['sender']['uid']
        # Ответить на сообщение
        send_response_message(user_id, "Спасибо за ваше сообщение!")
    return jsonify({"status": "received"}), 200

def send_response_message(user_id, message_text):
    import requests

    url = "https://api.cometchat.com/v3/messages"
    headers = {
        "Content-Type": "application/json",
        "appID": "2663259e5d42e08d",
        "authKey": "2dd644a436ad7090ccde658a0d36dd37b630c6b0"
    }
    payload = {
        "receiver": user_id,
        "receiverType": "user",
        "text": message_text,
        "category": "message",
        "type": "text"
    }

    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        print("Сообщение отправлено успешно.")
    else:
        print("Ошибка при отправке сообщения:", response.status_code, response.text)