def is_admin(user_id: int) -> bool:
    """
    Checks if the given user ID is in the list of admin IDs.

    Args:
        user_id (int): The user ID to check.

    Returns:
        bool: True if the user is an admin, False otherwise.
    """
    from config import ADMIN_IDS
    
    return user_id in ADMIN_IDS

def reply_or_send(context, update, message, reply_to_message_id=None):
    """
    Sends a message or replies to an existing message.

    Args:
        context (Updater): The updater context.
        update (Update): The update object.
        message (str): The message to send/reply with.
        reply_to_message_id (int, optional): Message ID to reply to. Defaults to None.

    Returns:
        Message: The sent/replied message object.
    """
    if reply_to_message_id:
        return context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message,
            reply_to_message_id=reply_to_message_id
        )
    else:
        return context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message
        )
