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
    url = f"https://api.greenapi.com/waInstance{ID_INSTANCE}/sendMessage/{API_TOKEN_INSTANCE}"
    payload = {"chatId": chat_id, "message": text}
    headers = {"Content-Type": "application/json"}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            print(f"❌ Green-API вернул статус {response.status_code}: {response.text}")
            return None
        return response.json()
    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")
        return None


@app.post("/webhook")
async def handle_webhook(request: Request):
    data = await request.json()
    if data.get("typeWebhook") == "incomingMessageReceived":
        sender_data = data.get("senderData", {})
        message_data = data.get("messageData", {})
        chat_id = sender_data.get("chatId")

        text_message = ""
        if message_data.get("typeMessage") == "textMessage":
            text_message = message_data.get("textMessageData", {}).get("textMessage", "").strip()

        db: Session = SessionLocal()
        try:
            user = db.query(User).filter(User.chat_id == chat_id).first()

            # Новый пользователь или сброс
            if not user or text_message.lower() in ["старт"]:
                if user:
                    db.delete(user)
                    db.commit()
                user = User(chat_id=chat_id, step="GET_FIO")
                db.add(user)
                db.commit()
                send_text(chat_id, "Добро пожаловать! 👋\nВведите ФИО ученика (Пример:ТунТунов Сахур Сахурович): ")
                return {"status": "ok"}

            if user.step == "GET_FIO":
                user.fio = text_message
                user.step = "GET_SCHOOL"
                db.commit()
                send_text(chat_id, "Введите вашу школу и класс(Пример: Школа 67, 67 класс):")

            elif user.step == "GET_SCHOOL":
                user.school = text_message
                user.step = "GET_PHOTO"
                db.commit()
                send_text(chat_id, "Отправьте ваше фото (медиафайлом в чат):")

            elif user.step == "GET_PHOTO":
                if message_data.get("typeMessage") == "imageMessage":
                    file_url = message_data.get("fileMessageData", {}).get("downloadUrl", "")
                    if file_url:
                        photo_url = upload_photo_to_cloudinary(file_url, chat_id)
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