import os
import sys
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from database import engine, SessionLocal, Base
from models import User
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from markupsafe import Markup

load_dotenv()

import cloudinary
import cloudinary.uploader
import requests as req

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

app = FastAPI()

class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        if username == os.getenv("ADMIN_USERNAME") and password == os.getenv("ADMIN_PASSWORD"):
            request.session.update({"token": "admin"})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("token") == "admin"

authentication_backend = AdminAuth(secret_key=os.getenv("SECRET_KEY", "supersecret"))

admin = Admin(app, engine, authentication_backend=authentication_backend)


ID_INSTANCE = os.getenv("ID_INSTANCE")
API_TOKEN_INSTANCE = os.getenv("API_TOKEN_INSTANCE")

if not ID_INSTANCE or not API_TOKEN_INSTANCE:
    print("❌ Критическая ошибка: ID_INSTANCE или API_TOKEN_INSTANCE не заданы!")
    sys.exit(1)

RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
NGROK_URL = RENDER_URL if RENDER_URL else "http://localhost:8000"

# Создаём таблицы при старте
Base.metadata.create_all(bind=engine)


def send_text(chat_id: str, text: str):
    url = f"https://graph.facebook.com/v25.0/{os.getenv('WHATSAPP_PHONE_ID')}/messages"
    headers = {
        "Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": chat_id,
        "type": "text",
        "text": {"body": text}
    }
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            print(f"❌ Meta API вернул статус {response.status_code}: {response.text}")
            return None
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return None


@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == os.getenv("VERIFY_TOKEN"):
        return int(params.get("hub.challenge"))
    return {"error": "Invalid verify token"}

@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]

        if "messages" not in value:
            return {"status": "ok"}

        message = value["messages"][0]
        chat_id = message["from"]
        message_type = message["type"]

        text_message = ""
        if message_type == "text":
            text_message = message["text"]["body"].strip()

        db: Session = SessionLocal()
        try:
            user = db.query(User).filter(User.chat_id == chat_id).first()

            if not user or text_message.lower() in ["старт", "привет"]:
                if user:
                    db.delete(user)
                    db.commit()
                user = User(chat_id=chat_id, step="GET_FIO")
                db.add(user)
                db.commit()
                send_text(chat_id, "Добро пожаловать! 👋\nВведите ФИО ученика:")
                return {"status": "ok"}

            if user.step == "GET_FIO":
                if not all(c.isalpha() or c.isspace() for c in text_message) or len(text_message) < 5:
                    send_text(chat_id, "❌ Пожалуйста, введите корректное ФИО.\n\nПример: Иванов Иван Иванович")
                else:
                    user.fio = text_message
                    user.step = "GET_SCHOOL"
                    db.commit()
                    send_text(chat_id, "Введите вашу школу и класс:\n\nПример: Школа №5, 10 класс")

            elif user.step == "GET_SCHOOL":
                if len(text_message) < 3:
                    send_text(chat_id, "❌ Пожалуйста, введите корректное название школы и класс.\n\nПример: Школа №5, 10 класс")
                else:
                    user.school = text_message
                    user.step = "GET_PHOTO"
                    db.commit()
                    send_text(chat_id, "Отправьте ваше фото (медиафайлом в чат):")

            elif user.step == "GET_PHOTO":
                if message_type == "image":
                    image_id = message["image"]["id"]
                    # Получаем URL фото через Meta API
                    media_url_response = requests.get(
                        f"https://graph.facebook.com/v25.0/{image_id}",
                        headers={"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}"}
                    )
                    media_url = media_url_response.json().get("url")

                    # Скачиваем фото
                    photo_response = requests.get(
                        media_url,
                        headers={"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}"}
                    )
                    # Загружаем в Cloudinary
                    result = cloudinary.uploader.upload(
                        photo_response.content,
                        folder="olympiad_photos",
                        public_id=f"participant_{chat_id}",
                        overwrite=True
                    )
                    photo_url = result.get("secure_url")

                    if not photo_url:
                        send_text(chat_id, "❌ Не удалось загрузить фото, попробуйте ещё раз.")
                        return {"status": "ok"}

                    user.photo_url = photo_url
                    user.photo_received = True
                    user.step = "COMPLETED"
                    db.commit()
                    demo_payment_link = f"{NGROK_URL}/payment-page/{chat_id}"
                    send_text(chat_id, f"✅ Данные приняты!\n• Ученик: {user.fio}\n• Школа: {user.school}\n\n💳 Оплатите взнос по ссылке:\n{demo_payment_link}")
                else:
                    send_text(chat_id, "❌ Пожалуйста, отправьте именно фото, не документ и не видео.")

            elif user.step == "COMPLETED":
                send_text(chat_id, "✅ Вы уже зарегистрированы! Если хотите начать заново — напишите 'Старт'.")

        finally:
            db.close()

    except Exception as e:
        print(f"❌ Ошибка обработки вебхука: {e}")

    return {"status": "ok"}


@app.get("/payment-page/{chat_id}")
async def payment_page(chat_id: str):
    return HTMLResponse(content=f"""
    <html>
        <body style="font-family: sans-serif; text-align: center; padding: 50px; background: #f4f6f9;">
            <div style="background: white; max-width: 400px; margin: auto; padding: 30px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
                <h2>Имитация оплаты M-Bank</h2>
                <p style="font-size: 24px; color: #2ecc71; font-weight: bold;">500 KGS</p>
                <form action="/fake-bank-process" method="post">
                    <input type="hidden" name="chat_id" value="{chat_id}">
                    <button type="submit" style="padding: 15px 30px; font-size: 18px; background: #2ecc71; color: white; border: none; border-radius: 5px; cursor: pointer; width: 100%;">
                        Оплатить демонстрационно
                    </button>
                </form>
            </div>
        </body>
    </html>
    """)


@app.post("/fake-bank-process")
async def fake_bank_process(request: Request):
    form = await request.form()
    chat_id = form.get("chat_id")
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.chat_id == chat_id).first()
        if user:
            user.payment_status = True
            db.commit()
    finally:
        db.close()
    send_text(chat_id, "🎉 [ФЕЙК-БАНК]: Оплата успешно зафиксирована! До встречи на Олимпиаде!")
    return HTMLResponse(content="<h1>Оплата прошла успешно! Возвращайтесь в WhatsApp.</h1>")
class UserAdmin(ModelView, model=User):
    column_list = [
        User.chat_id,
        User.fio,
        User.school,
        User.step,
        User.photo_received,
        User.photo_url,
        User.payment_status,
        User.created_at,
    ]

    column_formatters = {
        User.photo_url: lambda m, a: Markup(f'<a href="{m.photo_url}" target="_blank">Открыть фото</a>') if m.photo_url else "-"
    }
    name = "Пользователь"
    name_plural = "Пользователи"
    icon = "fa-solid fa-users"

admin.add_view(UserAdmin)

def upload_photo_to_cloudinary(file_url: str, chat_id: str):
    try:
        response = req.get(file_url)
        result = cloudinary.uploader.upload(
            response.content,
            folder="olympiad_photos",
            public_id=f"participant_{chat_id}",
            overwrite=True
        )
        return result.get("secure_url")
    except Exception as e:
        print(f"❌ Ошибка загрузки фото: {e}")
        return None

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    return HTMLResponse(content="""
    <html><body style="font-family: sans-serif; max-width: 800px; margin: auto; padding: 40px;">
         <h1>Пользовательское соглашение</h1>
        <p>Используя бота вы соглашаетесь на сбор данных для регистрации на мероприятие.</p>

        <h2>Условия приёма и возврата платежей</h2>
        <p>Оплата производится через платёжный шлюз Bakai Bank.</p>
        <p>Возврат средств осуществляется в течение 5 рабочих дней.</p>

        <p>Контакт: timerlansultanov124@gmail.com</p>
    </body></html>""")

@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return HTMLResponse(content="""
    <html><body style="font-family: sans-serif; max-width: 800px; margin: auto; padding: 40px;">
        <h1>Пользовательское соглашение</h1>
        <p>Используя бота вы соглашаетесь на сбор данных для регистрации на мероприятие.</p>
        <p>Контакт: evelone015@gmail.com</p>
    </body></html>""")

@app.get("/delete-data", response_class=HTMLResponse)
async def delete_data():
    return HTMLResponse(content="""
    <html><body style="font-family: sans-serif; max-width: 800px; margin: auto; padding: 40px;">
        <h1>Удаление данных</h1>
        <p>Для удаления ваших данных напишите на: evelone015@gmail.com</p>
    </body></html>""")