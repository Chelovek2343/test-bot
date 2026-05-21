from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from whatsapp_api_client_python import API

import os

app = FastAPI()

# --- ВСТАВЬТЕ СВОИ ДАННЫЕ ИЗ GREEN-API ---
ID_INSTANCE = "7107627959"
API_TOKEN_INSTANCE = "5238dfb602e34e2eabaa712441ef97f7611c19c0af9b4b5aab"

# Render автоматически создает переменную окружения RENDER_EXTERNAL_URL.
# Если она есть — код возьмет её, если её нет (на локалке) — включит локальный хост.
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")

if RENDER_URL:
    NGROK_URL = RENDER_URL
else:
    # Здесь оставь свою текущую рабочую ссылку lhr.life для тестов на компе
    NGROK_URL = "https://cb04fcae0be1ea.lhr.life"

green_api = API.GreenApi(ID_INSTANCE, API_TOKEN_INSTANCE)
user_states = {}

def send_text(chat_id, text):
    green_api.sending.sendMessage(chat_id, text)

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

        # Логика шагов
        if chat_id not in user_states or text_message.lower() in ["старт", "привет"]:
            user_states[chat_id] = {"step": "GET_FIO"}
            send_text(chat_id, "Добро пожаловать! 👋\nВведите ФИО ученика:")
            return {"status": "ok"}

        current_step = user_states[chat_id]["step"]

        if current_step == "GET_FIO":
            user_states[chat_id]["fio"] = text_message
            user_states[chat_id]["step"] = "GET_SCHOOL"
            send_text(chat_id, "Введите вашу школу и класс:")

        elif current_step == "GET_SCHOOL":
            user_states[chat_id]["school"] = text_message
            user_states[chat_id]["step"] = "GET_PHOTO"
            send_text(chat_id, "Отправьте ваше фото (медиафайлом в чат):")

        elif current_step == "GET_PHOTO":
            # Принимаем абсолютно всё, чтобы бот гарантированно прошёл дальше
            user_states[chat_id]["step"] = "COMPLETED"
            demo_payment_link = f"{NGROK_URL}/payment-page/{chat_id}"

            send_text(chat_id, f"✅ Данные приняты!\n• Ученик: {user_states[chat_id]['fio']}\n• Школа: {user_states[chat_id]['school']}\n\n💳 Оплатите взнос по ссылке:\n{demo_payment_link}")
    return {"status": "ok"}

# --- ФЕЙКОВЫЙ БАНК ДЛЯ ДЕМОНСТРАЦИИ ---
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
    send_text(chat_id, "🎉 [ФЕЙК-БАНК]: Оплата успешно зафиксирована шлюзом! Ваш статус изменен на 'Оплачено'. До встречи на Олимпиаде!")
    return HTMLResponse(content="<h1>Оплата прошла успешно! Возвращайтесь в WhatsApp.</h1>")