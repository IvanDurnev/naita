import os
import dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv.load_dotenv()


class Config(object):
    LOG_TO_STDOUT = os.environ.get('LOG_TO_STDOUT')
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'sdlasdkjfh-834970987zsdfasdfl-Dskfj843723o42'
    STATIC_FOLDER = os.path.join(basedir, 'app', 'static')
    UPLOAD_FOLDER = os.path.join(basedir, 'app', 'static', 'uploads')

    # flask_socketio
    SESSION_TYPE='filesystem'

    # Postgres
    user = os.environ.get('POSTGRES_USER')
    pw = os.environ.get('POSTGRES_PW')
    url = os.environ.get('POSTGRES_URL')
    db = os.environ.get('POSTGRES_DB')
    SQLALCHEMY_DATABASE_URI = f'postgresql+psycopg2://{user}:{pw}@{url}/{db}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # YANDEX
    YC_OAUTH_TOKEN = os.getenv('YC_OAUTH_TOKEN')
    IAM_CREDENTIALS_FILE = os.path.join(basedir, 'app', 'static', 'credentials', 'iam_token.json')
    YANDEX_CLOUD_ID = os.getenv('YANDEX_CLOUD_ID')
    YANDEX_CATALOG_ID = os.getenv('YANDEX_CATALOG_ID')
    YANDEX_GPT_MODEL = os.getenv('YANDEX_GPT_MODEL')
    YC_API_KEY = os.getenv('YC_API_KEY')

    #VK_ID
    VK_APP_ID = os.getenv('VK_APP_ID')
    VK_ID_KEY = os.getenv('VK_ID_KEY')
    VK_ID_SERVICE_KEY = os.getenv('VK_ID_SERVICE_KEY')
    VK_ID_REDIRECT_URI = os.getenv('VK_ID_REDIRECT_URI')

    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') or None
    OPENAI_GPT_ASSISTANT_ID = os.getenv('OPENAI_GPT_ASSISTANT_ID') or None
    OPENAI_PROXY_ADDR=os.getenv('OPENAI_PROXY_ADDR') or None
    OPENAI_ENABLED = os.getenv('OPENAI_ENABLED') or None

    # MAIL
    SMTP_SERVER = os.environ.get('SMTP_SERVER')
    MAIL_SERVER = os.environ.get('IMAP_SERVER')
    SMTP_PORT = os.environ.get('SMTP_PORT')
    IMAP_PORT = os.environ.get('IMAP_PORT')
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    MAIL_FOR_FEEDBACK = os.environ.get('MAIL_FOR_FEEDBACK').split()

    # НАСТРОЙКИ АНАЛИТИКИ
    MIN_COINCEDENCE_VALUE = int(os.environ.get('MIN_COINCEDENCE_VALUE')) or 7