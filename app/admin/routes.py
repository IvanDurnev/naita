from app import db
from flask import render_template, request, session, Response, jsonify
from flask_login import current_user, login_user, login_required
from app.admin import bp
from app.models import User, Vacancy, UserVacancy, Message
import logging
import datetime
import pandas as pd
import os

from config import Config


@bp.route('/admin')
def admin():
    vacancies_sql = 'select * from vacancy;'
    users_vacancies_sql = '''
select *
from vacancy v
full join public.user_vacancy uv on v.id = uv.vacancy_id
inner join public."user" u on u.id = uv.user_id
where
    u.is_admin is not true
order by v.id, u.id;
    '''
    vacancies_df = pd.read_sql_query(sql=vacancies_sql, con=db.engine)
    users_vacancies_df = pd.read_sql_query(sql=users_vacancies_sql, con=db.engine)

    # Фильтрация users_vacancies_df по условию is_main is True
    filtered_users_vacancies = users_vacancies_df[users_vacancies_df['is_main'] == True]

    # Группировка пользователей по vacancy_id
    grouped_users = (
        filtered_users_vacancies.groupby('vacancy_id')
        .apply(lambda x: x.to_dict(orient='records'))  # Преобразуем записи в списки словарей
        .reset_index(name='users')  # Создаем колонку users
    )

    # Объединение всех вакансий с данными о пользователях
    summary_df = pd.merge(
        vacancies_df,
        grouped_users,
        how='left',
        left_on='id',
        right_on='vacancy_id'
    )

    # Заполнение пустых списков для вакансий без связанных пользователей
    summary_df['users'] = summary_df['users'].apply(lambda x: x if isinstance(x, list) else [])

    vacancies = summary_df.to_dict(orient='records')

    return render_template('admin/index.html', vacancies=vacancies)

@bp.post('/admin/verify_email')
def verify_email():
    email = f"{request.get_json().get('email')}@otpbank.ru"
    admin = User.query.filter(User.email == email).first()

    if not admin:
        admin = User()
        admin.email = email
        db.session.add(admin)

    if not admin.name:
        admin.name = f'admin:{request.get_json().get("email")}'
    admin.is_admin = True
    admin.auth_code = 12345
    admin.auth_code_expiry = datetime.datetime.now() + datetime.timedelta(minutes=10)
    db.session.commit()
    db.session.commit()
    session['user_email'] = admin.email
    return Response(status=200)

@bp.post('/admin/verify_email_code')
def verify_email_code():
    try:
        if not (admin:= User.query.filter(User.email == session['user_email']).first()):
            return Response(status=404)

        if admin.verify_auth_code(request.get_json()['code']):
            login_user(admin, remember=True)
            return Response(status=200)
        return Response(status=403)
    except Exception as e:
        logging.error(f'Не удалось залогинить пользователя по электронной почте. {e}')
        return Response(status=500)

@bp.get('/admin/vacancy/<vid>')
def vacancy_detailed(vid):
    vacancy = Vacancy.query.filter_by(id=vid).first()
    user_vacancies = UserVacancy.query.filter(UserVacancy.vacancy_id == vid,
                                              UserVacancy.is_main.is_(True)).all()
    return render_template('admin/vacancion_detailed.html',
                           title=vacancy.name,
                           users=user_vacancies
                           )

@bp.post('/admin/vacancy/profile')
def get_user_profile():
    user = User.query.get(int(request.get_json().get('uid')))
    primary_vacancy = UserVacancy.query.filter(UserVacancy.user_id == user.id,
                                               UserVacancy.former_main.is_(True)).first()
    if primary_vacancy:
        primary_vacancy = primary_vacancy.get_vacancy().name
    else:
        primary_vacancy = '-'

    return {'name': user.name,
            'email': user.email,
            'primary_vacancy': primary_vacancy,
            'status': user.get_status()}

@bp.post('/admin/vacancy/messages')
def get_user_dialog():
    uid = int(request.get_json().get('uid'))
    messages = (Message.query.filter(
        (Message.receiver_id == uid) | (Message.sender_id == uid))
                .order_by(Message.sent).all())
    messages_json = [
        {
            'id': message.id,
            'sender_id': message.sender_id,
            'receiver_id': message.receiver_id,
            'type': message.message_type,
            'text': message.text,
            'content': message.content,
            'callback': message.callback,
            'disable_answer': message.disable_answer,
            'btns': message.btns,
            'sent': message.sent.isoformat() if message.sent else None
        }
        for message in messages
    ]

    return jsonify(messages_json), 200

@bp.post('/admin/vacancy/result')
def get_user_result():
    user = User.query.get(int(request.get_json().get('uid')))
    primary_vacancy = UserVacancy.query.filter(UserVacancy.user_id == user.id,
                                               UserVacancy.is_main.is_(True)).first()

    return {'positive': primary_vacancy.positive,
            'negative': primary_vacancy.negative,
            'recommendations': primary_vacancy.recommendations,
            'value': primary_vacancy.value}

@bp.post('/admin/vacancy/files')
def get_user_files():
    # Получение списка файлов
    all_files = get_all_files_in_user_directory(os.path.join(Config.STATIC_FOLDER, 'users', str(request.get_json().get('uid'))))
    download_links = []
    for file in all_files:
        download_links.append({
            'filename': file,
            'path': os.path.join(Config.STATIC_FOLDER, 'users', str(request.get_json().get('uid')), file),
            'comment': ''
        })
    return download_links


def get_all_files_in_user_directory(user_directory):
    file_list = []
    for root, dirs, files in os.walk(user_directory):
        for file in files:
            # Создаем относительный путь начиная от корневой папки пользователя
            relative_path = os.path.relpath(os.path.join(root, file), user_directory)
            file_list.append(relative_path)
    return file_list