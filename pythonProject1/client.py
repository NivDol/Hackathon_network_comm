import socket
import struct
import sys
import select
import time


# ==========================================
# 1. PROTOCOL DEFINITIONS
# ==========================================
class Protocol:
    MAGIC_COOKIE = 0xabcddcba
    PORT = 13122

    # Message Types
    MSG_OFFER = 0x2
    MSG_REQUEST = 0x3
    MSG_PAYLOAD = 0x4

    # Result Codes
    RES_NOT_OVER = 0x0
    RES_TIE = 0x1
    RES_LOSS = 0x2
    RES_WIN = 0x3

    # Packet Sizes
    SIZE_GAME_PAYLOAD = 9
    SIZE_OFFER = 39


# ==========================================
# 2. MESSAGE PARSER
# ==========================================
class MessageParser:
    def __init__(self):
        self.buffer = b""

    def add_data(self, data):
        self.buffer += data

    def has_complete_message(self):
        return len(self.buffer) >= Protocol.SIZE_GAME_PAYLOAD

    def parse_next(self):
        if len(self.buffer) < Protocol.SIZE_GAME_PAYLOAD: return None

        # 1. Validate Cookie
        magic, _ = struct.unpack('!I B', self.buffer[:5])
        if magic != Protocol.MAGIC_COOKIE:
            self.buffer = b""
            return None

        # 2. Extract Packet
        packet_bytes = self.buffer[:Protocol.SIZE_GAME_PAYLOAD]
        self.buffer = self.buffer[Protocol.SIZE_GAME_PAYLOAD:]

        _, _, result, rank_bytes, suit_int = struct.unpack('!I B B 2s B', packet_bytes)

        return {
            "result": result,
            "rank": self._decode_rank(rank_bytes),
            "suit": self._decode_suit(suit_int)
        }

    def _decode_rank(self, r_bytes):
        try:
            r_str = r_bytes.decode('utf-8')
            r_val = int(r_str)
            if r_val == 0: return None  # Hide dummy cards
            return {1: "Ace", 11: "Jack", 12: "Queen", 13: "King"}.get(r_val, r_str)
        except:
            return r_bytes.decode('utf-8')

    def _decode_suit(self, s_int):
        return {0: "Spades ♠", 1: "Clubs ♣", 2: "Diamonds ♦", 3: "Hearts ♥"}.get(s_int, "?")


# ==========================================
# 3. GAME CLIENT
# ==========================================
class BlackjackClient:
    def __init__(self):
        self.player_name = "NivTheMaster"
        self.parser = MessageParser()
        self.tcp_socket = None
        self.rounds_to_play = 1

        # State Tracking
        self.cards_seen = 0
        self.waiting_for_hit_response = False
        self.game_active = False
        self.dealer_first_card_str = ""  # Stores the face-up card to reprint later

    def start(self):
        print(f"--- Client: {self.player_name} ---")
        while True:
            try:
                val = input("Enter rounds to play (1-9): ").strip()
                self.rounds_to_play = int(val)
                break
            except ValueError:
                pass

        print(f"Listening for offers on UDP port {Protocol.PORT}...")
        self.listen_for_offers()

    def listen_for_offers(self):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        udp_sock.bind(("", Protocol.PORT))

        while True:
            data, addr = udp_sock.recvfrom(1024)
            if len(data) < Protocol.SIZE_OFFER: continue

            magic, m_type, port, name_b = struct.unpack('!I B H 32s', data[:Protocol.SIZE_OFFER])

            if magic == Protocol.MAGIC_COOKIE and m_type == Protocol.MSG_OFFER:
                # Name Cleanup
                server_name = name_b.decode('utf-8', 'ignore').replace('\x00', '').strip()
                print(f"\nFound Server '{server_name}' at {addr[0]}:{port}")
                self.connect(addr[0], port)
                break

    def connect(self, ip, port):
        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.connect((ip, port))
            print(f"Connected! Requesting {self.rounds_to_play} rounds.")

            time.sleep(0.1)  # Handshake Stabilizer

            req = struct.pack('!I B B 32s', Protocol.MAGIC_COOKIE, Protocol.MSG_REQUEST, self.rounds_to_play,
                              self.player_name.encode().ljust(32, b'\0'))
            self.tcp_socket.send(req)

            self.run_game_loop()

        except OSError as e:
            print(f"\n🚫 Connection Error: {e}")
        finally:
            if self.tcp_socket: self.tcp_socket.close()
            print("\n--- Session Finished ---")

    def run_game_loop(self):
        rounds_finished = 0
        self.reset_round()

        print("\n--- Round 1 Starting ---")

        while rounds_finished < self.rounds_to_play:
            # Check Network
            try:
                readable, _, _ = select.select([self.tcp_socket], [], [], 0.5)
            except OSError:
                return

            if readable:
                try:
                    chunk = self.tcp_socket.recv(1024)
                    if not chunk:
                        print("\n⚠️ Server closed the connection unexpectedly.")
                        return

                    self.parser.add_data(chunk)

                    while self.parser.has_complete_message():
                        msg = self.parser.parse_next()
                        if msg:
                            round_over = self.handle_game_message(msg)
                            if round_over:
                                rounds_finished += 1
                                if rounds_finished < self.rounds_to_play:
                                    print(f"\n--- Round {rounds_finished + 1} Starting ---")
                                    self.reset_round()
                                else:
                                    print("\n=== All rounds completed! ===")
                                    return
                except ConnectionResetError:
                    print("\n⚠️ Connection Reset by Server.")
                    return

            # Check User Input
            else:
                self.check_for_user_input()

    def reset_round(self):
        self.cards_seen = 0
        self.waiting_for_hit_response = False
        self.game_active = True
        self.dealer_first_card_str = ""

    def handle_game_message(self, msg):
        self.cards_seen += 1

        # --- Logic to Identify Card Owner ---
        owner = "❓ Unknown"
        card_str = f"{msg['rank']} of {msg['suit']}"

        if self.cards_seen <= 2:
            owner = "🎴 YOUR CARD"
        elif self.cards_seen == 3:
            owner = "🃏 DEALER'S FACE UP"
            # STORE THIS CARD TO PRINT IT AGAIN LATER
            if msg['rank'] is not None:
                self.dealer_first_card_str = card_str
        elif self.waiting_for_hit_response:
            owner = "🎴 YOUR CARD (Hit)"
            self.waiting_for_hit_response = False
        else:
            # If not waiting for a hit, it must be the dealer revealing/hitting
            owner = "🃏 DEALER'S CARD"

        if msg['rank'] is not None:
            print(f"{owner}: {card_str}")

        res = msg['result']
        if res != Protocol.RES_NOT_OVER:
            if res == Protocol.RES_TIE:
                print("\n=== 🤝 TIE 🤝 ===")
            elif res == Protocol.RES_LOSS:
                print("\n=== ❌ LOST ❌ ===")
            elif res == Protocol.RES_WIN:
                print("\n=== 🏆 WON 🏆 ===")
            self.game_active = False
            return True

        return False

    def check_for_user_input(self):
        if not self.game_active: return
        if self.cards_seen < 3: return
        if self.waiting_for_hit_response: return

        if select.select([self.tcp_socket], [], [], 0)[0]: return

        while True:
            choice = input("\n👉 [1] Hit | [2] Stand: ").strip()
            if choice == '1':
                self.send_command("Hittt")
                self.waiting_for_hit_response = True
                break
            elif choice == '2':
                # --- UPDATE: Show ALL Dealer Cards ---
                print("\n⬇️ --- Dealer's Turn (Revealing Hand) --- ⬇️")
                # REPRINT THE FIRST CARD so the user sees the full hand
                if self.dealer_first_card_str:
                    print(f"🃏 DEALER'S CARD: {self.dealer_first_card_str}")

                self.send_command("Stand")
                self.game_active = False
                break

    def send_command(self, cmd_str):
        # Format: Magic(4) + Type(1) + Command(5s)
        pkt = struct.pack('!I B 5s', Protocol.MAGIC_COOKIE, Protocol.MSG_PAYLOAD, cmd_str.encode())
        self.tcp_socket.send(pkt)
        print(f"   Sent: {cmd_str}")


if __name__ == "__main__":
    BlackjackClient().start()