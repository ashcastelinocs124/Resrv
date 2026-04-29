from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Discord
    discord_token: str = ""
    discord_guild_id: int = 0
    queue_channel_id: int = 0
    admin_channel_id: int = 0

    # OpenAI (for DM intent classification)
    openai_api_key: str = ""

    # Database
    database_path: str = "reserv.db"

    # Staff auth
    staff_username: str = "admin"
    staff_password: str = "changeme"
    auth_secret: str = "dev-secret-change-me"
    auth_token_ttl_hours: int = 12

    # Queue behaviour
    queue_reset_hour: int = 0  # midnight
    reminder_minutes: int = 30
    grace_minutes: int = 10
    agent_tick_seconds: int = 10

    # Email verification (SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""             # falls back to smtp_username when empty
    verification_code_ttl_minutes: int = 10
    verification_max_codes_per_hour: int = 5

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
