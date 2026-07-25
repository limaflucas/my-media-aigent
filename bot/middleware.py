"""Global error handling and user authorization middleware for Telegram Bot."""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from config import settings

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the exception and send an error notification to the Telegram user if possible.

    Args:
        update: The Telegram update object associated with the error.
        context: Callback context containing exception information.
    """
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ An unexpected error occurred. Please try again later."
            )
        except Exception:
            pass


def is_user_allowed(user_id: int) -> bool:
    """Checks if a Telegram user ID is authorized to interact with the bot.

    Args:
        user_id: The Telegram integer user ID.

    Returns:
        True if the user whitelist is empty or if user_id is in TELEGRAM_ALLOWED_USERS, else False.
    """
    if not settings.TELEGRAM_ALLOWED_USERS:
        return True
    return user_id in settings.TELEGRAM_ALLOWED_USERS
