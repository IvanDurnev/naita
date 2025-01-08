from app import db
from flask import render_template, request, session, Response
from flask_login import current_user, login_user
from app.admin import bp
from app.models import User, Vacancy, UserVacancy
import logging
import datetime
import pandas as pd

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

    # Преобразование DataFrame в список словарей
    # vacancies = vacancies_df.to_dict(orient='records')

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

    # Итоговый DataFrame
    print(summary_df.head())

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