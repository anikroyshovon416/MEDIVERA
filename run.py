import os

from app import create_app
from app.config import DevelopmentConfig, ProductionConfig

environment = os.getenv("FLASK_ENV", "development").strip().lower()
config = ProductionConfig if environment == "production" else DevelopmentConfig

app = create_app(config)

if __name__ == "__main__":
    # Debug mode is controlled by FLASK_DEBUG and is never enabled in production.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")))
