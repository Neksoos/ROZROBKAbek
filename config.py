import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # твої поля
    default_locale: str = "uk"
    app_version: str = os.getenv("APP_VERSION", "dev")

    # 🔥 нове поле — токен адміна
    ADMIN_SECRET: str = "CHANGE_ME"

    class Config:
        env_file = ".env"
        extra = "ignore"  # щоб не падало від зайвих полів


settings = Settings()