import gevent.monkey
gevent.monkey.patch_all()

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_cors import CORS
from flask_socketio import SocketIO
from flask_mail import Mail
from config import Config
import logging
from flask_bootstrap import Bootstrap5 as Bootstrap
from flask_session import Session
import gevent
import redis


db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = 'auth.login'
login.login_message = u'Пожалуйста, авторизуйтесь.'
bootstrap = Bootstrap()
cors = CORS()
socketio = SocketIO()
mail = Mail()
sess = Session()
redis_client = redis.Redis(host=Config.REDIS_HOST, port=Config.REDIS_PORT, db=Config.REDIS_DB)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    login.init_app(app)
    bootstrap.init_app(app)
    cors.init_app(app)
    sess.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*", async_mode='gevent')
    mail.init_app(app)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp)

    from app.chat import bp as chat_bp
    app.register_blueprint(chat_bp, prefix="/chat")

    return app