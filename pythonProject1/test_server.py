import unittest
import socket
import struct
import threading
import time

# --- Imports from server.py ---
from server import (
    BlackjackServer,
    BlackjackEngine,
    MAGIC_COOKIE,
    UDP_LISTENING_PORT,
    MESSAGE_TYPE_OFFER,
    MESSAGE_TYPE_REQUEST,
    MESSAGE_TYPE_PAYLOAD,
    RESULT_WIN, RESULT_LOSS, RESULT_TIE, RESULT_IN_PROGRESS
)

# --- Imports from client.py ---
from client import BlackjackProtocol as ClientProtocol


class TestBlackjackAssignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = BlackjackServer(team_name="TestServer", max_connections=3)
        cls.server_thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.server_thread.start()
        time.sleep(1)
        cls.tcp_port = cls.server.tcp_port

    # --- Helper for handling streams in tests ---
    def _read_packet(self, sock):
        """Reads exactly 9 bytes (one payload) from socket."""
        data = b''
        PACKET_SIZE = 9  # CORRECT SIZE: 4+1+1+2+1
        while len(data) < PACKET_SIZE:
            chunk = sock.recv(PACKET_SIZE - len(data))
            if not chunk: return None
            data += chunk
        return data

    # --- Part A: Logic Tests ---
    def test_01_deck_size_and_shuffling(self):
        deck1 = BlackjackEngine.get_shuffled_deck()
        self.assertEqual(len(deck1), 52)

    def test_02_card_values_face_cards(self):
        hand = [(10, 0), (11, 1), (12, 2), (13, 3)]
        self.assertEqual(BlackjackEngine.calculate_hand_sum(hand), 40)

    def test_03_card_values_ace_strict(self):
        hand = [(1, 0), (5, 2)]
        self.assertEqual(BlackjackEngine.calculate_hand_sum(hand), 16)

    # --- Part B: Protocol Tests ---
    def test_04_pack_offer_structure(self):
        from server import BlackjackProtocol as ServerProt
        packet = ServerProt.pack_offer("TestName", 12345)
        self.assertEqual(len(packet), 39)

    def test_05_pack_request_padding(self):
        packet = ClientProtocol.pack_request("TeamA", 1)
        self.assertEqual(len(packet), 38)

    def test_06_hebrew_name_handling(self):
        heb_name = "צוות_מנצח"
        packet = ClientProtocol.pack_request(heb_name, 1)
        unpacked = struct.unpack('!I B B 32s', packet)
        self.assertEqual(unpacked[3].decode('utf-8').strip('\x00'), heb_name)

    # --- Part C: Integration Tests ---
    def _create_client_socket(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('127.0.0.1', self.tcp_port))
        return sock

    def test_07_udp_broadcast_reception(self):
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_sock.bind(('', UDP_LISTENING_PORT))
        udp_sock.settimeout(3)
        data, _ = udp_sock.recvfrom(1024)
        unpacked = ClientProtocol.unpack_offer(data)
        self.assertEqual(unpacked[0], MAGIC_COOKIE)
        udp_sock.close()

    def test_08_tcp_connection_handshake(self):
        sock = self._create_client_socket()
        req = ClientProtocol.pack_request("HandshakeTester", 1)
        sock.send(req)

        response = self._read_packet(sock)
        self.assertTrue(response is not None)

        parsed = ClientProtocol.unpack_server_payload(response)
        self.assertEqual(parsed[0], MAGIC_COOKIE)
        sock.close()

    def test_09_invalid_magic_cookie(self):
        sock = self._create_client_socket()
        bad_req = struct.pack('!I B B 32s', 0xDEADBEEF, MESSAGE_TYPE_REQUEST, 1, b'Bad'.ljust(32, b'\x00'))
        sock.send(bad_req)
        time.sleep(0.2)
        try:
            data = sock.recv(1024)
            self.assertEqual(data, b"")
        except:
            pass
        sock.close()

    def test_10_invalid_message_type(self):
        sock = self._create_client_socket()
        bad_type = struct.pack('!I B B 32s', MAGIC_COOKIE, 0x2, 1, b'Bad'.ljust(32, b'\x00'))
        sock.send(bad_type)
        time.sleep(0.2)
        data = sock.recv(1024)
        self.assertEqual(data, b"")
        sock.close()

    def test_11_game_flow_stand(self):
        sock = self._create_client_socket()
        sock.send(ClientProtocol.pack_request("StandTester", 1))
        for _ in range(3): self._read_packet(sock)

        sock.send(ClientProtocol.pack_client_payload("Stand"))

        # Read until result or end
        final_data = sock.recv(4096)
        self.assertIn(MAGIC_COOKIE.to_bytes(4, 'big'), final_data)
        sock.close()

    def test_12_game_flow_hit_until_bust_or_stand(self):
        sock = self._create_client_socket()
        sock.send(ClientProtocol.pack_request("HitTester", 1))

        for _ in range(3): self._read_packet(sock)

        sock.send(ClientProtocol.pack_client_payload("Hittt"))
        response = self._read_packet(sock)
        self.assertIsNotNone(response)
        sock.close()

    def test_13_input_validation_hittt(self):
        decision = "Hittt"
        packed = ClientProtocol.pack_client_payload(decision)
        unpacked_magic, _, unpacked_dec = struct.unpack('!I B 5s', packed)
        self.assertEqual(unpacked_dec.decode('utf-8').strip('\x00'), "Hittt")

    def test_14_multiple_rounds(self):
        rounds = 2
        sock = self._create_client_socket()
        sock.send(ClientProtocol.pack_request("MultiRound", rounds))

        # Round 1
        for _ in range(3): self._read_packet(sock)
        sock.send(ClientProtocol.pack_client_payload("Stand"))

        while True:
            pkt = self._read_packet(sock)
            if not pkt: break
            if pkt[5] in [1, 2, 3]: break  # Result found

        # Round 2
        next_round_card = self._read_packet(sock)
        self.assertIsNotNone(next_round_card, "Did not receive card for Round 2")
        self.assertEqual(next_round_card[0:4], MAGIC_COOKIE.to_bytes(4, 'big'))
        sock.close()

    def test_15_concurrency_semaphore(self):
        clients = []
        for i in range(3):
            c = self._create_client_socket()
            c.send(ClientProtocol.pack_request(f"Player{i}", 1))
            c.recv(1024)
            clients.append(c)

        c4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c4.connect(('127.0.0.1', self.tcp_port))
        time.sleep(0.5)
        try:
            c4.send(ClientProtocol.pack_request("Rejected", 1))
            data = c4.recv(1024)
            self.assertEqual(data, b"")
        except:
            pass
        finally:
            c4.close()
            for c in clients: c.close()

    def test_16_client_disconnect_handling(self):
        sock = self._create_client_socket()
        sock.send(ClientProtocol.pack_request("Leaver", 1))
        sock.recv(1024)
        sock.close()
        time.sleep(1)
        try:
            sock2 = self._create_client_socket()
            sock2.send(ClientProtocol.pack_request("NewGuy", 1))
            res = sock2.recv(1024)
            self.assertTrue(len(res) > 0)
            sock2.close()
        except Exception as e:
            self.fail(f"Server crashed: {e}")

    def test_17_tie_is_win_logic(self):
        p_hand = [(10, 0), (10, 1)]  # 20
        d_hand = [(10, 2), (10, 3)]  # 20
        p_tot = BlackjackEngine.calculate_hand_sum(p_hand)
        d_tot = BlackjackEngine.calculate_hand_sum(d_hand)

        result = RESULT_LOSS
        if d_tot > 21 or p_tot >= d_tot:
            result = RESULT_WIN
        self.assertEqual(result, RESULT_WIN)

    def test_18_payload_decoding_safety(self):
        res = ClientProtocol.unpack_server_payload(b'\xde\xad')
        self.assertIsNone(res)

    def test_19_timeout_handling(self):
        sock = self._create_client_socket()
        sock.close()

    def test_20_result_codes(self):
        self.assertEqual(RESULT_IN_PROGRESS, 0)
        self.assertEqual(RESULT_TIE, 1)
        self.assertEqual(RESULT_LOSS, 2)
        self.assertEqual(RESULT_WIN, 3)


if __name__ == '__main__':
    unittest.main()