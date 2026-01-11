import socket
import threading
import struct
import random
import time

# --- קבועים גלובליים ---
MAGIC_COOKIE = 0xabcddcba
UDP_LISTENING_PORT = 13122

# סוגי הודעות
MESSAGE_TYPE_OFFER = 0x2
MESSAGE_TYPE_REQUEST = 0x3
MESSAGE_TYPE_PAYLOAD = 0x4

# תוצאות סיבוב (Server Payload)
RESULT_IN_PROGRESS = 0x0
RESULT_TIE = 0x1
RESULT_LOSS = 0x2
RESULT_WIN = 0x3


class BlackjackProtocol:
    """מחלקה לניהול פורמט החבילות והמרת נתונים לבתים"""

    @staticmethod
    def pack_offer(server_name, tcp_port):
        """יצירת חבילת Offer (UDP)"""
        padded_name = server_name[:32].ljust(32, '\x00').encode('utf-8')
        return struct.pack('!I B H 32s', MAGIC_COOKIE, MESSAGE_TYPE_OFFER, tcp_port, padded_name)

    @staticmethod
    def unpack_request(data):
        """פיענוח חבילת Request (TCP) מהלקוח"""
        if len(data) < 38:
            return None
        return struct.unpack('!I B B 32s', data)

    @staticmethod
    def pack_server_payload(result, rank, suit):
        """יצירת חבילת Payload מהשרת (תוצאה וקלף)"""
        rank_str = str(rank).zfill(2).encode('utf-8')
        return struct.pack('!I B B 2s B', MAGIC_COOKIE, MESSAGE_TYPE_PAYLOAD, result, rank_str, suit)

    @staticmethod
    def unpack_client_payload(data):
        """פיענוח החלטת השחקן (Hittt/Stand)"""
        if len(data) < 10:
            return None
        magic, msg_type, decision = struct.unpack('!I B 5s', data)
        return magic, msg_type, decision.decode('utf-8').strip('\x00').strip()


class BlackjackEngine:
    """ניהול חוקי המשחק והקלפים"""

    @staticmethod
    def get_shuffled_deck():
        """יצירת חפיסה בת 52 קלפים וערבובה"""
        deck = [(rank, suit) for rank in range(1, 14) for suit in range(4)]
        random.shuffle(deck)
        return deck

    @staticmethod
    def get_card_value(rank):
        """חישוב ערך הקלף: 2-10 כערכם, JQK=10, Ace=11"""
        if 2 <= rank <= 10:
            return rank
        if rank > 10:  # J, Q, K
            return 10
        if rank == 1:  # Ace
            return 11
        return 0

    @staticmethod
    def calculate_hand_sum(hand):
        """חישוב סכום היד הכולל"""
        return sum(BlackjackEngine.get_card_value(card[0]) for card in hand)


class BlackjackServer:
    """השרת המרכזי המנהל את התקשורת וריבוי השחקנים"""

    def __init__(self, team_name="CyberCasino_7"):
        self.team_name = team_name
        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_socket.bind(('0.0.0.0', 0))
        self.tcp_port = self.tcp_socket.getsockname()[1]
        self.running = True

    def start(self):
        """הפעלת השרת: שידור UDP וקבלת TCP"""
        self.tcp_socket.listen(5)
        print(f"Server started, listening on port {self.tcp_port}")

        # הפעלת תהליכון שידור הצעות (Offer)
        threading.Thread(target=self.broadcast_offers, daemon=True).start()

        while self.running:
            try:
                client_sock, addr = self.tcp_socket.accept()
                threading.Thread(target=self.handle_client, args=(client_sock, addr)).start()
            except Exception as e:
                print(f"Error accepting connection: {e}")

    def broadcast_offers(self):
        """שידור הודעת Offer ב-UDP פעם בשנייה"""
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        offer_packet = BlackjackProtocol.pack_offer(self.team_name, self.tcp_port)

        while self.running:
            try:
                udp_sock.sendto(offer_packet, ('<broadcast>', UDP_LISTENING_PORT))
                time.sleep(1)
            except:
                pass

    def handle_client(self, sock, addr):
        """ניהול סשן של שחקן (מספר סיבובים וסגירה)"""
        try:
            data = sock.recv(1024)
            if not data: return

            unpacked = BlackjackProtocol.unpack_request(data)
            if not unpacked or unpacked[0] != MAGIC_COOKIE:
                return

            magic, m_type, rounds_to_play, client_name_bytes = unpacked
            client_name = client_name_bytes.decode('utf-8').strip('\x00')
            print(f"Client '{client_name}' connected for {rounds_to_play} rounds.")

            for _ in range(rounds_to_play):
                self.play_game_round(sock)

            print(f"Finished session with {client_name}. Closing connection.")

        except Exception as e:
            print(f"Session error with {addr}: {e}")
        finally:
            sock.close()

    def play_game_round(self, sock):
        """ניהול לוגיקת סיבוב בודד"""
        deck = BlackjackEngine.get_shuffled_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]

        # 1. חלוקה ראשונית
        for card in player_hand:
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, card[0], card[1]))
        sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, dealer_hand[0][0], dealer_hand[0][1]))

        # 2. תור השחקן
        player_bust = False
        while True:
            data = sock.recv(1024)
            if not data: break

            decision = BlackjackProtocol.unpack_client_payload(data)
            if not decision or decision[2] == "Stand":
                break

            if decision[2] == "Hittt":
                new_card = deck.pop()
                player_hand.append(new_card)
                if BlackjackEngine.calculate_hand_sum(player_hand) > 21:
                    sock.send(BlackjackProtocol.pack_server_payload(RESULT_LOSS, new_card[0], new_card[1]))
                    player_bust = True
                    break
                sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, new_card[0], new_card[1]))

        # 3. תור הדילר
        if not player_bust:
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, dealer_hand[1][0], dealer_hand[1][1]))

            while BlackjackEngine.calculate_hand_sum(dealer_hand) < 17:
                new_card = deck.pop()
                dealer_hand.append(new_card)
                sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, new_card[0], new_card[1]))

            p_total = BlackjackEngine.calculate_hand_sum(player_hand)
            d_total = BlackjackEngine.calculate_hand_sum(dealer_hand)

            if d_total > 21:
                result = RESULT_WIN
            elif p_total > d_total:
                result = RESULT_WIN
            elif d_total > p_total:
                result = RESULT_LOSS
            else:
                result = RESULT_TIE

            sock.send(BlackjackProtocol.pack_server_payload(result, 0, 0))


# --- דוגמה ללוגיקת הלקוח (לצורך הסטטיסטיקות בסיום) ---
"""
בצד הלקוח, לאחר חיבור ה-TCP ושליחת ה-Request:

wins = 0
rounds_played = X
for i in range(rounds_played):
    # משחקים את הסיבוב...
    # אם הגיע RESULT_WIN מהשרת: wins += 1

win_rate = (wins / rounds_played) * 100
print(f"Finished playing {rounds_played} rounds, win rate: {win_rate}%")
tcp_socket.close()
"""

if __name__ == "__main__":
    server = BlackjackServer()
    server.start()