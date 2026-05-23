from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    chat_id = Column(String, primary_key=True)
    fio = Column(String, nullable=True)
    school = Column(String, nullable=True)
    photo_received = Column(Boolean, default=False)
    photo_url = Column(String, nullable=True)
    step = Column(String, default="GET_FIO")
    payment_status = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())