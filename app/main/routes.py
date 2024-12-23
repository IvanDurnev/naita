from __future__ import annotations
import json
import requests
from sqlalchemy.testing.plugin.plugin_base import logging
from app import db, sess
from app.main import bp
from flask import render_template, redirect, request, Response, jsonify, session
from app.models import User, load_user, Message
from flask_login import login_user, logout_user, current_user
from app.yagpt.yagpt import YAGPT
from config import Config
import logging
import os


@bp.get('/')
def index_main():
    return render_template(template_name_or_list='main/index.html',
                           vk_redirect_uri=Config.VK_ID_REDIRECT_URI)

@bp.get('/test')
def test():
    import pathlib

    from yandex_cloud_ml_sdk import YCloudML
    from yandex_cloud_ml_sdk.search_indexes import (
        StaticIndexChunkingStrategy,
        TextSearchIndexType,
    )

    mypath = os.path.join(Config.STATIC_FOLDER, 'knowledge_base')

    sdk = YCloudML(
        folder_id=Config.YANDEX_CATALOG_ID,
        auth=Config.YC_API_KEY
    )

    assistant = sdk.assistants.create(
        'yandexgpt',
        ttl_days=1,
        expiration_policy='static',
        temperature=0.5,
        max_prompt_tokens=50,
    )
    print(f"{assistant=}")

    return 'Куку'


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