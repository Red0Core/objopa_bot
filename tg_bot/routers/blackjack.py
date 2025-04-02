from aiogram import Router, F
from aiogram.types import InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from services.blackjack_game import BlackjackGame
from services.message_queue import MessageQueue

router = Router()
game = BlackjackGame()

def game_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Взять карту", callback_data=f"hit:{user_id}")
    builder.button(text="✋ Стоп", callback_data=f"stand:{user_id}")
    return builder.as_markup()

def end_game_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Начать заново", callback_data=f"restart:{user_id}")
    builder.button(text="❌ Закрыть", callback_data=f"close_menu:{user_id}")
    return builder.as_markup()

@router.callback_query(lambda c: c.data == "start_blackjack")
@MessageQueue.rate_limit()
async def start_blackjack(callback: CallbackQuery):
    user_id = callback.from_user.id
    game.start_game(user_id)
    state = game.get_game_state(user_id)

    return {
        "chat_id": callback.message.chat.id,
        "message_id": callback.message.message_id,
        "text": \
            f"Игрок: {callback.from_user.full_name}\n\n" \
            f"Ваши карты: {', '.join(state['player_hand'])} (Очки: {state['player_score']})\n" \
            f"Карта дилера: {state['dealer_hand'][0]}" \
        ,
        "reply_markup": game_keyboard(user_id),
    }

@router.callback_query(lambda c: c.data.startswith("hit"))
@MessageQueue.rate_limit()
async def hit_command(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        await callback.answer("Это не ваша игра!", show_alert=True)
        return

    card = game.hit(user_id)
    state = game.get_game_state(user_id)
    res = {
            "chat_id": callback.message.chat.id,
            "message_id": callback.message.message_id,
            "reply_markup": game_keyboard(user_id)
    }
    if state["status"] == "busted":
        game.update_score(user_id, "dealer")
        scores = game.get_scores(user_id)
        res["text"] = \
            f"Игрок: {callback.from_user.full_name}\n\n" \
            f"Вы взяли карту {card}. У вас перебор! (Очки: {state['player_score']})\n" \
            f"Карты дилера: {', '.join(state['dealer_hand'])} (Очки: {state['dealer_score']})\n\n" \
            f"🏆 Счёт: Игрок {scores['player']} – {scores['dealer']} Дилер"
        res['reply_markup'] = end_game_keyboard(user_id)
        
    else:
        res["text"] = \
            f"Игрок: {callback.from_user.full_name}\n\n" \
            f"Вы взяли карту {card}. Ваши карты: {', '.join(state['player_hand'])} (Очки: {state['player_score']})\n" \
            f"Карта дилера: {state['dealer_hand'][0]}"
        
    
    await callback.answer()
    return res

@router.callback_query(lambda c: c.data.startswith("stand"))
@MessageQueue.rate_limit()
async def stand_command(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        await callback.answer("Это не ваша игра!", show_alert=True)
        return

    dealer_score = game.stand(user_id)
    state = game.get_game_state(user_id)
    if state["player_score"] > dealer_score or dealer_score > 21:
        winner = "player"
    else:
        winner = "dealer"
    game.update_score(user_id, winner)
    scores = game.get_scores(user_id)
    res = {
            "chat_id": callback.message.chat.id,
            "message_id": callback.message.message_id,
            "reply_markup": end_game_keyboard(user_id)
    }
    result = "Вы выиграли!" if winner == "player" else "Вы проиграли."
    res["text"] = \
        f"Игрок: {callback.from_user.full_name}\n\n" \
        f"Ваши карты: {', '.join(state['player_hand'])} (Очки: {state['player_score']})\n" \
        f"Карты дилера: {', '.join(state['dealer_hand'])} (Очки: {dealer_score})\n{result}\n\n" \
        f"🏆 Счёт: Игрок {scores['player']} – {scores['dealer']} Дилер"
    
    await callback.answer()
    return res

@router.callback_query(lambda c: c.data.startswith("restart"))
@MessageQueue.rate_limit()
async def restart_game(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        await callback.answer("Это не ваша игра!", show_alert=True)
        return

    game.start_game(user_id)
    state = game.get_game_state(user_id)
    res = {
            "chat_id": callback.message.chat.id,
            "message_id": callback.message.message_id,
            "reply_markup": game_keyboard(user_id)
    }
    res["text"] = \
        f"Игрок: {callback.from_user.full_name}\n\n" \
        f"Ваши карты: {', '.join(state['player_hand'])} (Очки: {state['player_score']})\n" \
        f"Карта дилера: {state['dealer_hand'][0]}"

    await callback.answer()
    return res

@router.callback_query(lambda c: c.data.startswith("close_menu"))
async def close_menu(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        await callback.answer("Это не ваша игра!", show_alert=True)
        return

    # Удаляем сообщение
    await callback.message.delete()

    # Удаляем данные игрока
    if user_id in game.players:
        del game.players[user_id]
    if user_id in game.scores:
        del game.scores[user_id]
