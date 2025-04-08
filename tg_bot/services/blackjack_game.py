import random


class BlackjackGame:
    def __init__(self):
        self.players = {}  # Хранение состояния игроков
        self.scores = {}  # Хранение побед {user_id: {"player": 0, "dealer": 0}}

    def start_game(self, user_id):
        deck = self._generate_deck()
        self.players[user_id] = {
            "deck": deck,
            "player_hand": [self._draw_card(deck), self._draw_card(deck)],
            "dealer_hand": [self._draw_card(deck), self._draw_card(deck)],
            "status": "playing",
        }

        if user_id not in self.scores:
            self.scores[user_id] = {"player": 0, "dealer": 0}

    def _generate_deck(self):
        suits = ["♠", "♥", "♦", "♣"]
        values = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
        return [f"{v}{s}" for v in values for s in suits]

    def _draw_card(self, deck):
        return deck.pop(random.randint(0, len(deck) - 1))

    def _calculate_score(self, hand):
        values = {
            "2": 2,
            "3": 3,
            "4": 4,
            "5": 5,
            "6": 6,
            "7": 7,
            "8": 8,
            "9": 9,
            "10": 10,
            "J": 10,
            "Q": 10,
            "K": 10,
            "A": 11,
        }
        score = sum(values[card[:-1]] for card in hand)
        # Обработка тузов
        aces = sum(1 for card in hand if card[:-1] == "A")
        while score > 21 and aces:
            score -= 10
            aces -= 1
        return score

    def hit(self, user_id):
        player = self.players[user_id]
        card = self._draw_card(player["deck"])
        player["player_hand"].append(card)
        if self._calculate_score(player["player_hand"]) > 21:
            player["status"] = "busted"
        return card

    def stand(self, user_id):
        player = self.players[user_id]
        dealer_score = self._calculate_score(player["dealer_hand"])
        while dealer_score < 17:
            card = self._draw_card(player["deck"])
            player["dealer_hand"].append(card)
            dealer_score = self._calculate_score(player["dealer_hand"])
        player["status"] = "stand"
        return dealer_score

    def get_game_state(self, user_id):
        player = self.players[user_id]
        player_score = self._calculate_score(player["player_hand"])
        dealer_score = self._calculate_score(player["dealer_hand"])
        return {
            "player_hand": player["player_hand"],
            "dealer_hand": player["dealer_hand"],
            "player_score": player_score,
            "dealer_score": dealer_score,
            "status": player["status"],
        }

    def update_score(self, user_id, winner):
        if winner == "player":
            self.scores[user_id]["player"] += 1
        elif winner == "dealer":
            self.scores[user_id]["dealer"] += 1

    def get_scores(self, user_id):
        return self.scores.get(user_id, {"player": 0, "dealer": 0})
