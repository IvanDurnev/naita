from flask import url_for
from flask_mail import Message as MailMessage
from app import db, login, Config, mail
from flask_login import UserMixin, login_user
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


@login.user_loader
def load_user(id):
    return User.query.get(int(id))


class User(UserMixin, db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False, unique=True)
    sex = Column(Text)
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
    relocation_ready = Column(Text, comment='Готовность к релокации', info={"check_unfilled": True, "question": "Готовы ли вы к релокации?"}, default='')
    remote_ready = Column(Text, comment='Готовность к удаленной работе', info={"check_unfilled": True, "question": "Готовы ли вы к удаленной работе?"}, default='')
    professional_experience = Column(Text, comment='Опыт работы', info={"check_unfilled": True, "question": "Опишите ваш опыт"}, default='')
    skills = Column(Text, comment='Описание навыков', info={"check_unfilled": True, "question": "Напишите, пожалуйста, подробно ваши профессиональные навыки."}, default='')
    education = Column(Text, comment='Образования', info={"check_unfilled": True, "question": "Напишите, пожалуйста, какое у вас образование (учебное заведение, специальность, год окончания, ученая степень)?"}, default='')
    profile_assessment = Column(Text, default='')

    # Отношения для полученных и отправленных сообщений
    sent_messages = db.relationship("Message", foreign_keys='Message.sender_id', back_populates="sender",
                                 cascade="all, delete-orphan")
    received_messages = db.relationship("Message", foreign_keys='Message.receiver_id', back_populates="receiver",
                                     cascade="all, delete-orphan")

    # Отношения для опыта, навыков, образования
    # professional_experience = db.relationship("ProfessionalExperience", back_populates="owner", info={"check_unfilled": True, "question": "Напишите, пожалуйста, ваши места работы и должности"})
    # skills = db.relationship("Skills", back_populates="owner", info={"check_unfilled": True, "question": "Напишите, пожалуйста, подробно ваши профессиональные навыки."})
    # education = db.relationship("Education", back_populates="owner", info={"check_unfilled": True, "question": "Напишите, пожалуйста, какое у вас образование (учебное заведение, специальность, год окончания, ученая степень)?"})

    def get_avatar(self):
        return url_for('static', filename='avatars/' + str(self.id) + '.jpg')

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

    def profile_complete(self):
        return self.first_name and self.second_name and self.last_name and self.phone and self.city and self.relocation_ready and self.remote_ready and self.education and self.professional_experience and self.skills

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
ФИО, вакансия, подходящие под эту вакансию навыки пользователя, подходящее образование, подходящий опыт, общая оценка совместимости от 1 до 5, резюме, разъяснение оценки
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


class Message(db.Model):
    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    receiver_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
    message_type = Column(Enum('text', 'photo', 'video', 'audio', 'document', 'other', name='message_type_enum'), default='text')
    text = Column(Text)
    content = Column(JSONB)
    callback = Column(Text)
    sent = Column(DateTime, default=datetime.now)

    sender = db.relationship("User", foreign_keys=[sender_id])
    receiver = db.relationship("User", foreign_keys=[receiver_id])


# class ProfessionalExperience(db.Model):
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
#     company = Column(Text)
#     position = Column(Text)
#     description = Column(Text)
#     start_date = Column(DateTime)
#     end_date = Column(DateTime)
#     is_current = Column(Boolean)
#     responsibilities = Column(Text)
#     technologies = Column(Text)
#
#     owner = db.relationship("User", foreign_keys=[user_id])
#
# class Skills(db.Model):
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
#     name = Column(Text)
#     level = Column(Integer)
#
#     owner = db.relationship("User", foreign_keys=[user_id])
#
# class Education(db.Model):
#     id = Column(Integer, primary_key=True, autoincrement=True)
#     user_id = Column(Integer, ForeignKey('user.id', ondelete='CASCADE'))
#     university = Column(Text)
#     specialty = Column(Text)
#     degree = Column(Text)
#     start_date = Column(DateTime)
#     end_date = Column(DateTime)
#     description = Column(Text)
#
#     owner = db.relationship("User", foreign_keys=[user_id])