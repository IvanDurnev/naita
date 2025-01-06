import base64
import logging
from datetime import datetime
from http.client import responses
import hashlib
from sqlalchemy import Boolean
from config import Config
import requests
import json
import os
import pathlib
from app.yagpt import prompts
from app import redis_client, db
import mimetypes
from time import sleep


class YAGPT:
    def __init__(self):
        self.catalog_id = Config.YANDEX_CATALOG_ID
        self.yc_api_key = Config.YC_API_KEY
        self.iam_token = YAGPT.get_yandex_iam_token()
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.iam_token}',
            'x-folder-id': Config.YANDEX_CATALOG_ID
        }

    @staticmethod
    def create_yandex_iam_token():
        iam_url = 'https://iam.api.cloud.yandex.net/iam/v1/tokens'
        payload = {
            'yandexPassportOauthToken': Config.YC_OAUTH_TOKEN
        }
        iam = requests.post(url=iam_url, data=json.dumps(payload))

        try:
            with open(Config.IAM_CREDENTIALS_FILE, 'w') as token:
                token.write(f"{iam.json()['iamToken']}\n{iam.json()['expiresAt']}")
            return iam.json()['iamToken'].strip()
        except Exception as e:
            logging.info(e)
            return None

    @staticmethod
    def get_yandex_iam_token():
        if os.path.exists(Config.IAM_CREDENTIALS_FILE):
            with open(Config.IAM_CREDENTIALS_FILE, 'r') as f:
                iam_token = f.readline().strip()
                expiration_date = f.readline().strip()
                expires_at = datetime.strptime(expiration_date[:26] + expiration_date[-1:], '%Y-%m-%dT%H:%M:%S.%fZ')
                if datetime.now() > expires_at:
                    logging.info('IAM токен истек, генерируем новый')
                    return YAGPT.create_yandex_iam_token()
                else:
                    return iam_token
        else:
            return YAGPT.create_yandex_iam_token()

    def completion(self, text, model=Config.YANDEX_GPT_MODEL, stream=False, temperature=0.6, max_tokens=2000):
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.iam_token}',
            'x-folder-id': Config.YANDEX_CATALOG_ID
        }
        payload = {
            'modelUri': f'gpt://{Config.YANDEX_CATALOG_ID}/{model}',
            "completionOptions": {
                "stream": stream,
                "temperature": temperature,
                "maxTokens": str(max_tokens)
            },
            "messages": [
                {
                    "role": "user",
                    "text": text
                }
            ]
        }

        response = requests.post(url=url, headers=headers, data=json.dumps(payload))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        return response.json()['result']['alternatives'][0]['message']['text']

    def create_assistant(
            self,
            name='Найта',
            description='',
            expiration_config=None,
            labels=None,
            model_uri=None,
            instruction=None,
            prompt_truncation_options=None,
            completion_options=None,
            tools=None
    ):
        """
        Создает ассистента в системе Yandex.
        """
        if expiration_config is None:
            expiration_config = {'expirationPolicy': 'STATIC',
                                 'ttlDays': '60'}
        if labels is None:
            labels = {}
        if model_uri is None:
            model_uri = f"gpt://{self.catalog_id}/yandexgpt/rc"
        if instruction is None:
            instruction = prompts.KNOWLEDGE_BASE_ASSISTANT_INSTRUCTION
        if prompt_truncation_options is None:
            prompt_truncation_options = {"maxPromptTokens": "100000"}
        if completion_options is None:
            completion_options = {"maxTokens": "10000",
                                  "temperature": "0.7"
                                  }
        if tools is None:
            tools = []

        url = 'https://rest-assistant.api.cloud.yandex.net/assistants/v1/assistants'

        body = {
            "folderId": self.catalog_id,
            "name": name,
            "description": description,
            "expirationConfig": expiration_config,
            "labels": labels,
            "modelUri": model_uri,
            "instruction": instruction,
            "promptTruncationOptions": prompt_truncation_options,
            "completionOptions": completion_options,
            "tools": tools
        }
        response = requests.post(url=url, headers=self.headers, data=json.dumps(body))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            logging.info(f'Ассистент создан {response.json()}')
            return response.json()
        return None

    def get_assistant(self, assistant_id):
        url = f'https://rest-assistant.api.cloud.yandex.net/assistants/v1/assistants/{assistant_id}'
        response = requests.get(url=url, headers=self.headers)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return response.json()
        return None

    def get_assistants_list(self):
        url = 'https://rest-assistant.api.cloud.yandex.net/assistants/v1/assistants'
        params = {
            'folderId': self.catalog_id
        }
        response = requests.get(url=url, headers=self.headers, params=params)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return response.json()
        return None

    def del_assistant(self, assistant_id) -> bool:
        url = f'https://rest-assistant.api.cloud.yandex.net/assistants/v1/assistants/{assistant_id}'
        response = requests.delete(url=url, headers=self.headers)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return True
        return False

    def del_all_assistants(self):
        assistants = self.get_assistants_list()
        for a in assistants['assistants']:
            self.del_assistant(a['id'])
        return True

    def ask_assistant(self, content, user):
        # create yandex user
        if not user.ya_user_id:
            self.create_user(user)
        # create or retrieve thread
        if not user.current_ya_thread:
            thread = self.create_thread(user)
        self.create_message(content, user)
        run = self.create_run(user.ya_assistant_id, user.current_ya_thread)
        if os.environ.get('DEBUG'):
            logging.info(f'Запущен RUN {run}')
        if run:
            result = self.run_listen(run['id'])
            # if os.environ.get('DEBUG'):
            #     from pprint import pprint
            #     pprint(result)
            return result['result']['completed_message']['content']['content'][0]['text']['content']
        return None

    def create_user(self, user, labels=None, expiration_config=None):
        url = 'https://rest-assistant.api.cloud.yandex.net/users/v1/users'
        if expiration_config is None:
            expiration_config = {
                "expirationPolicy": "STATIC",
                "ttlDays": "60"
            }
        body = {
            "folderId": self.catalog_id,
            "name": f'{user.id} {user.first_name} {user.last_name}',
            "description": "",
            "source": "web",
            "expirationConfig": expiration_config,
            "labels": labels
        }
        response = requests.post(url=url, headers=self.headers, data=json.dumps(body))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            user.ya_user_id = response.json()['id']
            db.session.commit()
            return response.json()
        return None

    def create_message(self, content, user, labels=None):
        from app.models import YaAssistantMessage
        url = 'https://rest-assistant.api.cloud.yandex.net/assistants/v1/messages'
        body = {
            "threadId": user.current_ya_thread,
            "author": {
                "id": user.ya_user_id,
                "role": "USER"
            },
            "labels": labels,
            "content": {
                "content": [
                    {
                        "text": {
                            "content": content
                        }
                    }
                ]
            }
        }
        response = requests.post(url=url, headers=self.headers, data=json.dumps(body))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            ya_assistant_message = YaAssistantMessage()
            ya_assistant_message.user_id = user.id
            ya_assistant_message.message_id = response.json().get('id')
            ya_assistant_message.content = response.json()
            db.session.add(ya_assistant_message)
            db.session.commit()
            return response.json()
        return None

    def create_run(self, assistant_id, thread_id, labels=None):
        url = 'https://rest-assistant.api.cloud.yandex.net/assistants/v1/runs'
        body = {
            "assistantId": assistant_id,
            "threadId": thread_id,
            "labels": labels,
            "additionalMessages": None,
            "stream": False
        }
        response = requests.post(url=url, headers=self.headers, data=json.dumps(body))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return response.json()
        return None

    def run_listen(self, run_id):
        url = f'https://rest-assistant.api.cloud.yandex.net/assistants/v1/runs/listen'
        params = {
            'runId': run_id
        }
        response = requests.get(url=url, headers=self.headers, params=params)
        if os.environ.get('DEBUG'):
            logging.info(f'Запущен RUN LISTEN {response.__dict__}')
        decoded_content = response.content.decode('utf-8')
        json_objects = decoded_content.strip().split("\n")
        # Обрабатываем каждый объект
        for json_str in json_objects:
            try:
                data = json.loads(json_str)  # Парсим JSON
                if data['result']['event_type'] == 'DONE':
                    return data
            except json.JSONDecodeError as e:
                print(f"Ошибка декодирования JSON: {e}")
        return None

    def create_thread(self, user, expiration_config=None, labels=None):
        url = 'https://rest-assistant.api.cloud.yandex.net/assistants/v1/threads'
        if expiration_config is None:
            expiration_config = {
                "expirationPolicy": "STATIC",
                "ttlDays": "60"
            }
        body = {
            'folderId': self.catalog_id,
            'messages': [],
            'name': f'тред пользователя {user.id}',
            'description': '',
            'defaultMessageAuthorId': f'{user.ya_user_id}',
            'expirationConfig': expiration_config,
            'labels': labels
        }
        response = requests.post(url=url, headers=self.headers, data=json.dumps(body))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            user.current_ya_thread = response.json().get('id')
            db.session.commit()
            return response.json()
        return None

    def create_file(
            self,
            name,
            content,
            description='',
            mime_type='text/plain',
            labels=None,
            expiration_config=None
    ) -> None:
        if expiration_config is None:
            expiration_config = {
                "expirationPolicy": "STATIC",
                "ttlDays": "60"
            }
        url = 'https://rest-assistant.api.cloud.yandex.net/files/v1/files'
        body = {
            "folderId": self.catalog_id,
            "name": name,
            "description": description,
            "mimeType": mime_type,
            "content": base64.b64encode(content).decode('utf-8'),
            "labels": labels,
            "expirationConfig": expiration_config
        }

        try:
            response = requests.post(url=url, headers=self.headers, data=json.dumps(body))
            if os.environ.get('DEBUG'):
                logging.info(f'добавление файла {name}')
                logging.info(response.status_code)
                logging.info(response.json())
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.error(f'Не удалось добавить в Яндекс ассистент файл {name}, {e}')
        return None


    def get_file(self, file_id):
        url = f'https://rest-assistant.api.cloud.yandex.net/files/v1/files/{file_id}'
        response = requests.get(url=url, headers=self.headers)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return response.json()
        return None

    def get_files_list(self):
        url = f'https://rest-assistant.api.cloud.yandex.net/files/v1/files'
        params = {
            "folderId": self.catalog_id
        }
        response = requests.get(url=url, headers=self.headers, params=params)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return response.json()
        return None

    def del_file(self, file_id) -> bool:
        url = f'https://rest-assistant.api.cloud.yandex.net/files/v1/files/{file_id}'
        response = requests.delete(url=url, headers=self.headers)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return True
        return False

    def del_all_files(self):
        files = self.get_files_list()
        for file in files['files']:
            self.del_file(file['id'])
        return True

    def create_search_index(
            self,
            file_ids: list,
            name: str,
            description: str,
            expiration_config=None,
            labels=None,
            text_search_index=None
    ):
        if expiration_config is None:
            expiration_config = {
                "expirationPolicy": "STATIC",
                "ttlDays": "60"
            }
        if text_search_index is None:
            text_search_index = {
                "chunkingStrategy": {
                    "staticStrategy": {
                        "maxChunkSizeTokens": "800",
                        "chunkOverlapTokens": "400"
                    }
                }
            }
        url = f'https://rest-assistant.api.cloud.yandex.net/assistants/v1/searchIndex'

        body = {
            "folderId": self.catalog_id,
            "fileIds": file_ids,
            "name": name,
            "description": description,
            "expirationConfig": expiration_config,
            "labels": labels,
            "textSearchIndex": text_search_index
        }

        response = requests.post(url=url, headers=self.headers, data=json.dumps(body))

        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())

        if response.status_code == 200:
            return response.json()
        return None

    def get_search_index(self, search_index_id):
        url = f'https://rest-assistant.api.cloud.yandex.net/assistants/v1/searchIndex/{search_index_id}'
        response = requests.get(url=url, headers=self.headers)

        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())

        if response.status_code == 200:
            return response.json()
        return None

    def get_search_indexes_list(self):
        url = 'https://rest-assistant.api.cloud.yandex.net/assistants/v1/searchIndex'
        params = {
            "folderId": self.catalog_id,
        }
        response = requests.get(url=url, headers=self.headers, params=params)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())

        if response.status_code == 200:
            return response.json()
        return None

    def del_search_index(self, search_index_id) -> bool:
        url = f'https://rest-assistant.api.cloud.yandex.net/assistants/v1/searchIndex/{search_index_id}'
        response = requests.delete(url=url, headers=self.headers)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return True
        return False

    def del_all_search_indices(self):
        indices = self.get_search_indexes_list()
        for i in indices['indices']:
            self.del_search_index(i['id'])
        logging.info('Все поисковые индексы удалены')
        redis_key = f"{Config.REDIS_KEY_PREFIX}:common_search_index"
        try:
            redis_client.delete(redis_key)
        except:
            pass
        return True

    def get_operation_status(self, operation_id):
        url = f'https://operation.api.cloud.yandex.net/operations/{operation_id}'
        response = requests.get(url=url, headers=self.headers)
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        if response.status_code == 200:
            return response.json()
        return False

class KnowledgeBase:
    def __init__(self, user):
        self.user_id = user.id
        self.common_files_path = os.path.join(Config.STATIC_FOLDER, 'knowledge_base')
        self.private_files_path = os.path.join(Config.STATIC_FOLDER, 'users', str(user.id))
        self.files = []
        for path in os.listdir(self.common_files_path):
            self.files.append(
                {
                    'filename': path,
                    'hash': self.calculate_file_hash(os.path.join(self.common_files_path, path)),
                    'ya_file_id': '',
                    'created': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                    'updated': '',
                    'private': 0
                }
            )
        for path in os.listdir(self.private_files_path):
            full_path = os.path.join(self.private_files_path, path)
            if os.path.isfile(full_path):
                mime_type, _ = mimetypes.guess_type(full_path)
                if mime_type in ('text/plain', 'application/pdf'):
                    self.files.append(
                        {
                            'filename': path,
                            'hash': self.calculate_file_hash(os.path.join(self.private_files_path, path)),
                            'ya_file_id': '',
                            'created': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                            'updated': '',
                            'private': 1
                        }
                    )
        self.search_index = {}
        # загрузить общедоступные и личные файлы
        self.check_files_integrity(user)
        # создать поисковый индекс
        self.check_search_index(user)
        # создать ассистента
        self.check_personal_assistant(user)

    def calculate_file_hash(self, path):
        sha256_hash = hashlib.sha256()
        with open(path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def check_files_integrity(self, user):
        ya = YAGPT()
        updated_files = []
        deleted_files = []
        new_files = []

        # Список текущих файлов в папке
        current_files = {file.get('filename'): file for file in self.files}

        # Получение всех ключей в Redis, связанных с файлами
        redis_common_files_pattern = f"{Config.REDIS_KEY_PREFIX}:common_files:*"
        redis_common_files_keys = redis_client.keys(redis_common_files_pattern)
        redis_private_files_pattern = f"{Config.REDIS_KEY_PREFIX}:private_files_user_{user.id}:*"
        redis_private_files_keys = redis_client.keys(redis_private_files_pattern)

        # Сравнение текущих файлов с файлами в Redis
        for redis_key in redis_common_files_keys+redis_private_files_keys:
            filename = redis_key.decode("utf-8").split(":")[-1]
            file_path = os.path.join(self.private_files_path, filename) if redis_client.hget(redis_key, "private") else os.path.join(self.common_files_path, filename)

            if filename not in current_files:
                # Файл есть в Redis, но нет в папке
                deleted_files.append(filename)
            else:
                # Файл есть и в папке, и в Redis – проверяем хэш
                file = current_files[filename]
                redis_hash = redis_client.hget(redis_key, "hash").decode("utf-8")
                if file.get("hash") != redis_hash:
                    updated_files.append(file)
                    redis_client.hset(redis_key, 'hash', self.calculate_file_hash(file_path))
                    try:
                        ya.del_file(redis_client.hget(redis_key, "ya_file_id").decode("utf-8"))
                        with open(file_path, "rb") as f:
                            mime_type, _ = mimetypes.guess_type(os.path.join(self.common_files_path, filename))
                            if file := ya.create_file(name=filename, content=f.read(), mime_type=mime_type,
                                                      description=filename):
                                redis_client.hset(redis_key, 'ya_file_id', file.get('id', ''))
                                redis_client.hset(redis_key, 'updated', datetime.now().strftime('%d.%m.%Y %H:%M:%S'))
                    except Exception as e:
                        logging.error(
                            f'KnowledgeBase.check_files_integrity() не удалось обновить в ya файл {file}. {e}')

        # Поиск новых файлов
        for filename, file in current_files.items():
            redis_key = f"{Config.REDIS_KEY_PREFIX}:private_files_user_{user.id}:{filename}" if file['private'] else f"{Config.REDIS_KEY_PREFIX}:common_files:{filename}"
            file_path = os.path.join(self.private_files_path, filename) if redis_client.hget(redis_key,"private") else os.path.join(self.common_files_path, filename)

            if not redis_client.exists(redis_key):
                # новые файлы записываем в redis и в яндекс
                print(redis_key, file)
                redis_client.hset(redis_key, mapping=file)
                try:
                    with open(os.path.join(self.common_files_path, filename), "rb") as f:
                        mime_type, _ = mimetypes.guess_type(os.path.join(self.common_files_path, filename))
                        if file:=ya.create_file(name=filename, content=f.read(), mime_type=mime_type, description=filename):
                            redis_client.hset(redis_key, 'ya_file_id', file.get('id', ''))
                except Exception as e:
                    logging.error(f'KnowledgeBase.check_files_integrity() не удалось добавить в ya файл {file}. {e}')
                new_files.append(file)

        # Логирование результатов
        if os.environ.get('DEBUG'):
            if new_files:
                logging.info(f"Добавлены новые файлы: {[file.get('filename') for file in new_files]}")
            if updated_files:
                logging.info(f"Обновлены файлы: {[file.get('filename') for file in updated_files]}")
            if deleted_files:
                logging.info(f"Удалены файлы: {deleted_files}")

        # Удаление записей о файлах, которые отсутствуют в папке
        for filename in deleted_files:
            redis_key = f"{Config.REDIS_KEY_PREFIX}:common_files:{filename}"
            if not redis_client.exists(redis_key):
                redis_key = f"{Config.REDIS_KEY_PREFIX}:private_files_user_{user.id}:{filename}"
            try:
                ya.del_file(redis_client.hget(redis_key, "ya_file_id").decode("utf-8"))
                logging.info(f"Запись о файле {filename} удалена из Ya.")
                redis_client.delete(redis_key)
                logging.info(f"Запись о файле {filename} удалена из Redis.")
            except Exception as e:
                logging.info(f'Не удалось удалить файл {filename}')

        # Обновление self.files данными из Redis
        self.files = [
            {
                'filename': key.decode('utf-8').split(":")[-1],
                **{field.decode('utf-8'): value.decode('utf-8') for field, value in
                   redis_client.hgetall(key).items()}
            }
            for key in redis_client.keys(f"{Config.REDIS_KEY_PREFIX}:common_files:*")
        ]

        return {
            'updated_files': updated_files,
            'deleted_files': deleted_files,
            'new_files': new_files
        }

    def check_search_index(self, user):
        ya = YAGPT()

        # ya.get_search_indexes_list()
        # ya.del_all_search_indices()
        #
        # return

        # Получение ключа Redis с поисковым индексом
        # redis_key = f"{Config.REDIS_KEY_PREFIX}:common_search_index"
        # search_index = redis_client.hgetall(redis_key)
        if not user.current_search_index:
            ya_search_index_operation = ya.create_search_index(
                name='Найта',
                file_ids=[file.get('ya_file_id') for file in self.files],
                description='Главный поисковый индекс'
            )
            operation_result = None
            if ya_search_index_operation:
                while True:
                    response = ya.get_operation_status(ya_search_index_operation.get('id'))
                    if response.get('done'):
                        operation_result = response.get('response')
                        break
                    else:
                        sleep(1)

            self.search_index = {
                'id': operation_result.get('id'),
                'name': operation_result.get('name'),
                'description': operation_result.get('description'),
                'created': operation_result.get('createdAt'),
                'updated': operation_result.get('updatedAt')
            }
            # redis_client.hmset(redis_key, self.search_index)
            user.current_search_index = self.search_index
            db.session.commit()
            return True

        self.search_index = user.current_search_index
        return True

    def check_personal_assistant(self, user):
        ya = YAGPT()

        if not user.ya_assistant_id:
            tools = [
                {
                    "searchIndex": {
                        "searchIndexIds": [
                            user.current_search_index['id']
                        ],
                        "maxNumResults": "1"
                    }
                }
            ]
            assistant = ya.create_assistant(
                name=f'Найта_{user.id}',
                description=f'Персональный ассистент пользователя {user.id}',
                tools=tools
            )
            user.ya_assistant_id = assistant.get('id')
            db.session.commit()
            return True

        assistant = ya.get_assistant(user.ya_assistant_id)
        if assistant:
            if os.environ.get('DEBUG'):
                logging.info(f'У пользователя есть ассистент {assistant}')
            return True

class CV:
    pass