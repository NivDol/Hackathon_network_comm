import socket
import threading
import struct
import random
import time

# --- Global Constants ---
MAGIC_COOKIE = 0xabcddcba
UDP_LISTENING_PORT = 13122
MESSAGE_TYPE_OFFER = 0x2
MESSAGE_TYPE_REQUEST = 0x3
MESSAGE_TYPE_PAYLOAD = 0x4

RESULT_IN_PROGRESS = 0x0
RESULT_TIE = 0x1
RESULT_LOSS = 0x2
RESULT_WIN = 0x3


class BlackjackProtocol:
    """
    Handles strict binary packing/unpacking.
    Includes BOTH Server and Client methods so the Test Suite can use them.
    """

    @staticmethod
    def pack_offer(server_name, tcp_port):
        name_bytes = server_name.encode('utf-8')
        if len(name_bytes) > 32: name_bytes = name_bytes[:32]
        padded_name = name_bytes.ljust(32, b'\x00')
        return struct.pack('!I B H 32s', MAGIC_COOKIE, MESSAGE_TYPE_OFFER, tcp_port, padded_name)

    @staticmethod
    def unpack_request(data):
        if len(data) < 38: return None
        return struct.unpack('!I B B 32s', data)

    @staticmethod
    def pack_server_payload(result, rank, suit):
        rank_str = str(rank).zfill(2).encode('utf-8')
        return struct.pack('!I B B 2s B', MAGIC_COOKIE, MESSAGE_TYPE_PAYLOAD, result, rank_str, suit)

    @staticmethod
    def unpack_client_payload(data):
        if len(data) < 10: return None
        magic, msg_type, decision = struct.unpack('!I B 5s', data)
        return magic, msg_type, decision.decode('utf-8', errors='replace').strip('\x00').strip()

    # --- MISSING METHODS ADDED FOR TESTS ---
    @staticmethod
    def pack_request(team_name, rounds):
        """Used by the Test Suite to simulate a client connecting."""
        name_bytes = team_name.encode('utf-8')
        if len(name_bytes) > 32: name_bytes = name_bytes[:32]
        padded_name = name_bytes.ljust(32, b'\x00')
        return struct.pack('!I B B 32s', MAGIC_COOKIE, MESSAGE_TYPE_REQUEST, rounds, padded_name)

    @staticmethod
    def pack_client_payload(decision):
        """Used by the Test Suite to simulate sending a move."""
        return struct.pack('!I B 5s', MAGIC_COOKIE, MESSAGE_TYPE_PAYLOAD, decision.encode('utf-8'))
    # ---------------------------------------


class BlackjackEngine:
    @staticmethod
    def get_shuffled_deck():
        deck = [(rank, suit) for rank in range(1, 14) for suit in range(4)]
        random.shuffle(deck)
        return deck

    @staticmethod
    def calculate_hand_sum(hand):
        total = 0
        for rank, _ in hand:
            if rank == 1:
                total += 11  # STRICT RULE: Ace is 11
            elif rank >= 10:
                total += 10
            else:
                total += rank
        return total


class BlackjackServer:
    def __init__(self, team_name="CyberCasino_7", max_connections=5):
        self.team_name = team_name
        self.max_conn_limit = max(1, min(max_connections, 20))
        self.connection_semaphore = threading.Semaphore(self.max_conn_limit)

        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_socket.bind(('0.0.0.0', 0))
        self.tcp_port = self.tcp_socket.getsockname()[1]
        self.running = True

    def start(self):
        self.tcp_socket.listen(20)
        print(f"Server started on port {self.tcp_port}. Max players: {self.max_conn_limit}")
        threading.Thread(target=self.broadcast_offers, daemon=True).start()

        while self.running:
            try:
                client_sock, addr = self.tcp_socket.accept()
                threading.Thread(target=self.manage_connection, args=(client_sock, addr)).start()
            except Exception as e:
                print(f"Accept error: {e}")

    def broadcast_offers(self):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        broadcast_addr = '255.255.255.255'
        offer_packet = BlackjackProtocol.pack_offer(self.team_name, self.tcp_port)

        while self.running:
            try:
                udp_sock.sendto(offer_packet, (broadcast_addr, UDP_LISTENING_PORT))
                time.sleep(1)
            except Exception as e:
                print(f"Broadcast error: {e}")

    def manage_connection(self, sock, addr):
        if not self.connection_semaphore.acquire(blocking=False):
            sock.close()
            return
        try:
            self.handle_client(sock, addr)
        finally:
            self.connection_semaphore.release()

    def handle_client(self, sock, addr):
        try:
            sock.settimeout(3000)
            data = sock.recv(1024)
            if not data: return

            unpacked = BlackjackProtocol.unpack_request(data)

            # --- FIX FOR TEST 10: Validate Message Type ---
            # Added check: unpacked[1] != MESSAGE_TYPE_REQUEST
            if not unpacked or unpacked[0] != MAGIC_COOKIE or unpacked[1] != MESSAGE_TYPE_REQUEST:
                return
                # ----------------------------------------------

            _, _, rounds, name_bytes = unpacked
            name = name_bytes.decode('utf-8', errors='replace').strip('\x00')
            print(f"Player '{name}' connected.")

            for _ in range(rounds):
                self.play_game_round(sock)
        except Exception:
            pass
        finally:
            sock.close()

    def play_game_round(self, sock):
        deck = BlackjackEngine.get_shuffled_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]

        for rank, suit in player_hand:
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, rank, suit))
        sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, dealer_hand[0][0], dealer_hand[0][1]))

        while BlackjackEngine.calculate_hand_sum(player_hand) < 21:
            data = sock.recv(1024)
            if not data: break
            decision = BlackjackProtocol.unpack_client_payload(data)
            if not decision or decision[2] == "Stand": break

            if decision[2] == "Hittt":
                new_card = deck.pop()
                player_hand.append(new_card)
                if BlackjackEngine.calculate_hand_sum(player_hand) > 21:
                    sock.send(BlackjackProtocol.pack_server_payload(RESULT_LOSS, new_card[0], new_card[1]))
                    return
                sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, new_card[0], new_card[1]))

        sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, dealer_hand[1][0], dealer_hand[1][1]))
        while BlackjackEngine.calculate_hand_sum(dealer_hand) < 17:
            new_card = deck.pop()
            dealer_hand.append(new_card)
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_IN_PROGRESS, new_card[0], new_card[1]))

        p_total = BlackjackEngine.calculate_hand_sum(player_hand)
        d_total = BlackjackEngine.calculate_hand_sum(dealer_hand)

        if d_total > 21 or p_total > d_total:
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_WIN, 0, 0))
        elif d_total > p_total:
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_LOSS, 0, 0))
        else:
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_TIE, 0, 0))
            sock.send(BlackjackProtocol.pack_server_payload(RESULT_WIN, 0, 0))


if __name__ == "__main__":
    server = BlackjackServer()
    server.start()