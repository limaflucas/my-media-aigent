"""Application entry point and Telegram Bot lifecycle manager for my-media-aigent."""

import logging
import sys

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import settings
from bot.middleware import error_handler
from bot.handlers.overseerr import (
    start_command,
    seerr_command,
    handle_message,
    handle_callback_query,
)

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
)
logger = logging.getLogger(__name__)


def main() -> None:
    """Initializes settings, builds the Telegram Application, registers route handlers, and starts polling."""
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    api_key = settings.OVERSEERR_API_KEY.get_secret_value()

    if not token or not api_key:
        logger.critical("TELEGRAM_BOT_TOKEN and OVERSEERR_API_KEY must be configured.")
        sys.exit(1)

    logger.info("Starting Telegram Bot (my-media-aigent)...")

    # Build application
    application = ApplicationBuilder().token(token).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("seerr", seerr_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Error Handler
    application.add_error_handler(error_handler)

    # Run bot polling loop
    application.run_polling()


if __name__ == "__main__":
    main()