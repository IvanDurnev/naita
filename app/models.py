import json
from flask import url_for
from flask_mail import Message as MailMessage
from app import db, login, Config, mail, redis_client
from flask_login import UserMixin, login_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import jwt
import random
from time import time
import os
import requests
from sqlalchemy import Text, Integer, Column, DateTime, Boolean, BIGINT, ForeignKey, Enum, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.cursor import CursorResult
import logging
import openai
import functools
import copy
from app.yagpt.yagpt import YAGPT, KnowledgeBase
import mimetypes


@login.user_loader
def load_user(id):
    return User.query.get(int(id))

class User(UserMixin, db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, default='')
    email = Column(Text, nullable=False, unique=True)
    is_admin = Column(Boolean, default=False)
    sex = Column(Text)
    pers_data_consent = Column(Boolean, default=False)
    birthday = Column(Text)
    vk_user_id = Column(BIGINT)
    vk_first_name = Column(Text)
    vk_last_name = Column(Text)
    vk_avatar = Column(Text)
    vk_verified = Column(Boolean)
    gpt_thread = Column(Text, default='')

    # код авторизации
    auth_code = db.Column(String(5), nullable=True, default='')
    auth_code_expiry = db.Column(DateTime, nullable=True, default=datetime.now)

    # профиль
    first_name = Column(Text, info={"check_unfilled": True, "question": "Напишите, пожалуйста, ваше имя."}, default='')
    second_name = Column(Text, info={"check_unfilled": True, "question": "Напишите, пожалуйста, ваше отчество."}, default='')
    last_name = Column(Text, info={"check_unfilled": True, "question": "Напишите, пожалуйста, вашу фамилию."}, default='')
    phone = Column(Text, info={"check_unfilled": True, "question": "Напишите, пожалуйста, ваш номер телефона."}, default='')
    city = Column(Text, info={"check_unfilled": True, "question": "Напишите, пожалуйста, ваш город."}, default='')
    # relocation_ready = Column(Text, comment='Готовность к релокации', info={"check_unfilled": True, "question": "Готовы ли вы к релокации?"}, default='')
    # remote_ready = Column(Text, comment='Готовность к удаленной работе', info={"check_unfilled": True, "question": "Готовы ли вы к удаленной работе?"}, default='')
    # professional_experience = Column(Text, comment='Опыт работы', info={"check_unfilled": True, "question": "Опишите ваш опыт"}, default='')
    # skills = Column(Text, comment='Описание навыков', info={"check_unfilled": True, "question": "Напишите, пожалуйста, подробно ваши профессиональные навыки."}, default='')
    # education = Column(Text, comment='Образования', info={"check_unfilled": True, "question": "Напишите, пожалуйста, какое у вас образование (учебное заведение, специальность, год окончания, ученая степень)?"}, default='')
    # profile_assessment = Column(Text, default='')
    profile_filled = Column(Boolean, default=False)
    coincidences_done = Column(Boolean, default=False)

    ya_assistant_id = Column(Text)
    current_ya_thread = Column(Text)
    current_search_index = Column(JSONB)
    ya_user_id = Column(Text)

    profile = Column(JSONB)

    resume_received = Column(Boolean, default=False)

    # Отношения для полученных и отправленных сообщений
    sent_messages = db.relationship("Message", foreign_keys='Message.sender_id', back_populates="sender", cascade="all, delete-orphan")
    received_messages = db.relationship("Message", foreign_keys='Message.receiver_id', back_populates="receiver", cascade="all, delete-orphan")

    # Отношение для данных пользователя
    user_data = db.relationship("UserData", foreign_keys='UserData.user_id', back_populates="user", cascade="all, delete-orphan")

    # Отношение для вакансий подходящих пользователю
    vacancies = db.relationship("Vacancy", secondary="user_vacancy", back_populates="users")
    # user_vacancies = db.relationship("UserVacancy", back_populates="user", cascade="all, delete-orphan")

    def get_avatar(self):
        avatar_path = os.path.join(Config.STATIC_FOLDER, 'users', str(self.id), 'avatar.jpg')
        if os.path.exists(avatar_path):
            return url_for('static', filename=f'users/{str(self.id)}/avatar.jpg')
        else:
            return url_for('static', filename=f'users/default/ava.svg')

    def get_avatar_external(self):
        avatar_path = os.path.join(Config.STATIC_FOLDER, 'users', str(self.id), 'avatar.jpg')
        if os.path.exists(avatar_path):
            return url_for('static', filename=f'users/{str(self.id)}/avatar.jpg', _external=True)
        else:
            return url_for('static', filename=f'users/default/avatar_pixar.jpg', _external=True)

    def set_auth_code(self):
        # Сохранение кода в базе данных с ограничением по времени
        self.auth_code = str(random.randint(10000, 99999))
        self.auth_code_expiry = datetime.now() + timedelta(minutes=10)
        db.session.commit()

    def send_auth_code(self):
        try:
            msg = MailMessage(
                'НАЙТА, код авторизации.',
                sender=Config.MAIL_DEFAULT_SENDER,
                recipients=[self.email]
            )
            msg.body = f'Your authorization code is: {self.auth_code}'
            mail.send(msg)
            return True
        except Exception as e:
            logging.info(f'Не удалось отправить email c кодом авторизации. {e}')
        return False

    def verify_auth_code(self, code):
        if self.auth_code and self.auth_code_expiry and self.auth_code == code and self.auth_code_expiry > datetime.now():
            login_user(self, remember=True)
            return True
        return False

    # deprecated
    def get_profile_txt(self):
        return f''' Профиль пользователя:
Имя: {self.first_name or "-"},
Отчество: {self.second_name or "-"},
Фамилия: {self.last_name or "-"},
Телефон: {self.phone or "-"},
Город: {self.city or "-"},
Готовность к релокации: {self.relocation_ready or "-"},
Готовность к удаленной работе: {self.remote_ready or "-"},

Образование:
{self.education or "-"}

Опыт работы:
{self.professional_experience or "-"}

Профессиональные навыки:
{self.skills or "-"}
'''

    # deprecated
    def profile_complete(self):
        return self.first_name and self.second_name and self.last_name and self.phone and self.city and self.relocation_ready and self.remote_ready and self.education and self.professional_experience and self.skills

    # deprecated
    def get_first_unfilled_field(self):
        """
        Возвращает имя первого незаполненного поля, помеченного в info={"check_unfilled": True}.
        """
        # Проверяем обычные столбцы
        for column in self.__table__.columns:
            if column.info.get("check_unfilled", False):
                value = getattr(self, column.name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    return {'field':column.name, 'question': column.info.get("question", '')}

        # Проверяем отношения (relationship)
        # for relationship in self.__mapper__.relationships:
        #     if relationship.info.get("check_unfilled", False):
        #         value = getattr(self, relationship.key)
        #         if not value:  # Пустая коллекция или None
        #             return {'field': relationship.key, 'question': relationship.info.get("question", '')}

        return None  # Все отмеченные поля заполнены

    # deprecated
    def check_candidate_v1(self):
        assistant = openai.beta.assistants.retrieve(
            assistant_id=Config.OPENAI_GPT_ASSISTANT_ID
        )
        thread = openai.beta.threads.create()
        # добавляем сообщение от пользователя
        messages = openai.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=f'''Пройдись с этим профилем по всем вакансиям \n\n{self.get_profile_txt()}
Верни в табличном виде (markdown):
ФИО, вакансия, подходящие под эту вакансию навыки пользователя, подходящее образование, подходящий опыт, общая оценка совместимости от 1 до 10, резюме, разъяснение оценки
'''
        )

        run = openai.beta.threads.runs.create_and_poll(
            thread_id=thread.id,
            assistant_id=assistant.id
        )

        if run.status == 'completed':
            messages = openai.beta.threads.messages.list(
                thread_id=thread.id
            )

            message = Message()
            message.text = messages.data[0].content[0].text.value.strip()
            message.message_type = 'text'
            message.receiver_id = self.id
            db.session.add(message)
            db.session.commit()

            self.profile_assessment = message.text

            return message.text
        else:
            print(run.status)

    def check_candidate_v2(self):
        from app.chat.openai_proxy import OpenAIProxy
        openai_proxy_client = OpenAIProxy()
        content =  f'''Пройдись с этим профилем по всем вакансиям \n\n{self.get_profile_txt()}
Верни в табличном виде на русском языке с разметкой markdown без ссылок на файлы:
вакансия, подходящие под эту вакансию навыки пользователя, подходящее образование, подходящий опыт, общая оценка совместимости от 1 до 10, разъяснение оценки
'''

        if response := openai_proxy_client.ask_assistant(Config.OPENAI_GPT_ASSISTANT_ID, content):
            message = Message()
            message.text = response
            message.message_type = 'text'
            message.receiver_id = self.id
            db.session.add(message)
            db.session.commit()

            self.profile_assessment = message.text
            db.session.commit()
            return message.text

        logging.error('Не удалось получить скрининг кандидата')
        return False

    def check_profile_filled(self):
        user_data = UserData.query.filter(UserData.user_id == self.id).all()
        fields = set([ud.type for ud in user_data if ud.text])
        reference_profile_fields = set(['main_education', 'add_education', 'experience', 'hard_skills', 'soft_skills', 'awards'])
        return len(reference_profile_fields-fields) == 0

    def get_user_data(self):
        user_data = UserData.query.filter(UserData.user_id == self.id).all()
        resumes = Resume.query.filter(Resume.user == self.id).all()
        text = ''
        for ud in user_data:
            if ud.text:
                if ud.question:
                    text += f'{ud.type}: Вопрос: {ud.question} Ответ:{ud.text}\n'
                else:
                    text += f'{ud.type}: {ud.text}\n'
        for resume in resumes:
            text += f'{json.dumps(resume.data, ensure_ascii=False)}\n'
        return text

    def get_user_data_for_pesonal_assistant(self):
        return f'''Кандидата зовут {self.name}, вот информация о нем: {self.get_user_data()}'''

    def get_user_messages_history(self):
        messages = self.get_ya_thread_messages()
        history = []
        try:
            for message in messages:
                history.append(
                    f"role: {message['result']['author']['role']}, message: {message['result']['content']['content'][0]['text']['content']}")
            return history
        except:
            return []

    def add_user_data(self, data):
        for key in data.keys():
            if data.get(key, None):
                user_data = UserData()
                user_data.user_id = self.id
                user_data.text = data.get(key, '')
                user_data.type = key
                db.session.add(user_data)
                db.session.commit()

    def add_user_data_question(self, data):
        # user_data_with_empty_text = UserData.query.filter(UserData.user_id == self.id,
        #                                                   UserData.text.is_(None)).all()
        # for ud in user_data_with_empty_text:
        #     db.session.delete(ud)

        db.session.commit()
        user_data = UserData()
        user_data.user_id = self.id
        user_data.question = data.get('question_text', '')
        user_data.type = data.get('variable', '')
        db.session.add(user_data)
        db.session.commit()

        return user_data.id

    def get_main_vacancy(self):
        if uv:=UserVacancy.query.filter(UserVacancy.user_id == self.id, UserVacancy.is_main == True).first():
            return uv.get_vacancy()
        return False

    def update_user_profile(self, data):
        self.profile = None
        db.session.commit()
        self.profile = data
        db.session.commit()

    def is_profile_complete(self):
        """
        Проверяет, что все поля в профиле заполнены.
        Пустыми считаются: "", пустые списки, пустые словари.
        Словари внутри списков проверяются только на наличие их самих, а не на заполненность их полей.
        """

        def is_value_filled(value):
            if isinstance(value, str):
                return value.strip() != ""  # Строка не должна быть пустой
            elif isinstance(value, list):
                # Проверяем, что список не пуст и содержит хотя бы один непустой элемент
                return len(value) > 0 and all(
                    isinstance(item, dict) or is_value_filled(item) for item in value
                )
            elif isinstance(value, dict):
                return len(value) > 0 and all(
                    is_value_filled(v) for v in value.values())  # Проверяем словари, кроме тех, что внутри списка
            else:
                return value is not None  # Для других типов проверяем, что значение не None

        return is_value_filled(self.profile)

    def check_completeness(self):
        data = self.profile
        completeness = 0

        if data.get('education')['main_education'] in ['', {}]:
            return 0
        else:
            completeness += 25
        if data.get('hard_skills') in ['', []]:
            return 0
        else:
            completeness += 20
        if data.get('soft_skills') in ['', []]:
            return 0
        else:
            completeness += 20
        if data.get('work_experience') in ['', []]:
            return 0
        else:
            completeness += 25

        if data.get('additional_information') != '':
            completeness += 10

        return completeness

    def create_personal_ya_assistant(self):
        kb = KnowledgeBase(self)

    def get_status(self):
        status = {'name': 'начальный', 'color': 'grey'}
        if self.profile_filled:
            status = {'name': 'в работе у Найты', 'color': 'grey'}
        if self.coincidences_done:
            status = {'name': 'прескрининг пройден', 'color': 'grey'}
            user_vacancy = UserVacancy.query.filter(UserVacancy.user_id == self.id,
                                                    UserVacancy.is_main.is_(True)).first()
            if user_vacancy:
                if user_vacancy.value >= Config.MIN_COINCEDENCE_VALUE:
                    status = {'name': 'прескрининг пройден', 'color': 'green'}
                else:
                    status = {'name': 'прескрининг пройден', 'color': 'red'}
        return status

    def get_ya_thread(self):
        ya = YAGPT()
        return ya.get_thread(self.current_ya_thread)

    def get_ya_thread_messages(self):
        ya = YAGPT()
        return ya.get_messages_list(self.current_ya_thread)

    def get_AI_profile(self):
        from app.yagpt.prompts import get_profile_prompt
        messages = self.get_ya_thread_messages()
        history = []
        for message in messages:
            history.append(f"role: {message['result']['author']['role']}, message: {message['result']['content']['content'][0]['text']['content']}")

        ya = YAGPT()
        response = ya.completion(text = get_profile_prompt('\n'.join(history)))
        if response:
            return response
        return None

class Message(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    receiver_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    message_type = Column(Enum('text', 'photo', 'video', 'audio', 'document', 'other', name='message_type_enum'), default='text')
    text = Column(Text)
    content = Column(JSONB)
    btns = Column(JSONB)
    callback = Column(Text)
    disable_answer = Column(Boolean, default=False)
    sent = Column(DateTime, default=datetime.now)

    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])

class Resume(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    user = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    data = Column(JSONB)
    created = Column(DateTime, default=datetime.now)
    updated = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    source = Column(Text)
    link = Column(Text)

class UserData(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text)
    text = Column(Text)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    type = Column(Text)
    created = Column(DateTime, default=datetime.now)

    user = db.relationship("User", foreign_keys=[user_id])

class Vacancy(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text)
    description = Column(Text)

    # Реляция "многие-ко-многим" с User через UserVacancy
    users = db.relationship("User", secondary="user_vacancy", back_populates="vacancies")

    def get_json(self):
        return {'id': self.id, 'name': self.name, 'description': self.description}

class UserVacancy(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    vacancy_id = Column(Integer, ForeignKey('vacancy.id', ondelete='CASCADE'))
    is_main = Column(Boolean, default=False)
    former_main = Column(Boolean, default=False)
    value = Column(Integer)
    positive = Column(Text)
    negative = Column(Text)
    recommendations = Column(Text)
    created = Column(DateTime, default=datetime.now)

    def get_vacancy(self):
        return Vacancy.query.filter(Vacancy.id == self.vacancy_id).first()

    def get_user(self):
        return User.query.filter(User.id == self.user_id).first()

class YaAssistantMessage(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    message_id = Column(Text)
    content = Column(JSONB)

# Декоратор для сохранения исходящего сообщения в базе данных
def outgoing_message(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        data = args[0]
        try:
            message = Message()
            message.text = data['content']
            message.message_type = data['type']
            message.sender_id = current_user.id
            db.session.add(message)
            db.session.commit()
        except Exception as e:
            logging.warning(f'Не удалось сохранить исходящее сообщение. {e}')
        return func(*args, **kwargs)
    return wrapper

# Декоратор для сохранения входящего сообщения в базе данных
def incoming_message(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        data = args[0]
        message = None
        try:
            message = Message()
            message.text = data['text']
            message.message_type = data['type']
            if 'btns' in data:
                message.btns = data['btns']
            if 'callback' in data:
                message.callback = data['callback']
            if 'disable_answer' in data:
                message.disable_answer = data['disable_answer']
            message.receiver_id = current_user.id
            db.session.add(message)
            db.session.commit()
        except Exception as e:
            logging.warning(f'Не удалось сохранить входящее сообщение. {e}')

        # Добавляем сообщение в kwargs
        kwargs['message'] = message
        return func(*args, **kwargs)
    return wrapper