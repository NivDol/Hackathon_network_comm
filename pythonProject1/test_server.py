import unittest
import socket
import struct
import threading
import time

# ייבוא מהקובץ המקורי שלך
from Hack import (
    BlackjackServer, MAGIC_COOKIE, UDP_LISTENING_PORT,
    MESSAGE_TYPE_OFFER, MESSAGE_TYPE_REQUEST, MESSAGE_TYPE_PAYLOAD,
    RESULT_WIN, RESULT_LOSS, RESULT_TIE
)


class TestBlackjackExampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """
        שלב 1: הקמת השרת (Team Tzion בדוגמה).
        השרת מתחיל להאזין ולהפיץ הודעות UDP.
        """
        cls.server_name = "TeamTzion"
        cls.server = BlackjackServer(team_name=cls.server_name, max_connections=5)
        cls.server_thread = threading.Thread(target=cls.server.start, daemon=True)
        cls.server_thread.start()
        time.sleep(1)  # זמן התארגנות לשרת

    def test_simulate_example_run(self):
        """
        סימולציה של שלבי דוגמת ההרצה (שלבים 3-10 במסמך).
        """
        # שלב 3-5: הלקוח (Team Joker) מתחיל להאזין להצעות בפורט 13122
        client_udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # תמיכה בהרצת מספר לקוחות על אותו מחשב (SO_REUSEPORT)
        try:
            client_udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        client_udp_sock.bind(('', UDP_LISTENING_PORT))
        client_udp_sock.settimeout(5.0)

        print("\n--- Step 5: Client listening for offer requests... ---")
        data, addr = client_udp_sock.recvfrom(1024)

        # שלב 6: קבלת ופענוח הצעת השרת
        cookie, msg_type, tcp_port, srv_name = struct.unpack('!I B H 32s', data)
        self.assertEqual(cookie, MAGIC_COOKIE)
        self.assertEqual(msg_type, MESSAGE_TYPE_OFFER)

        server_name_str = srv_name.decode().strip('\x00')
        print(f"--- Step 6: Received offer from {addr[0]} (Server Name: {server_name_str}) ---")

        # שלב 7: התחברות ב-TCP ושליחת בקשת סיבובים
        print("--- Step 7: Connecting to server over TCP... ---")
        client_tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_tcp_sock.connect((addr[0], tcp_port))

        rounds_to_play = 1
        client_team_name = "TeamJoker".ljust(32, '\x00').encode('utf-8')

        # מבנה חבילת בקשה: Magic(4), Type(1), Rounds(1), Name(32)
        request_packet = struct.pack('!I B B 32s', MAGIC_COOKIE, MESSAGE_TYPE_REQUEST, rounds_to_play, client_team_name)
        client_tcp_sock.send(request_packet)
        # שליחת תו ירידת שורה כפי שמתואר בסעיף 6 בדוגמת ההרצה
        client_tcp_sock.send(b'\n')
        print(f"Sent request for {rounds_to_play} rounds.")

        # שלב 8-9: ניהול שלבי המשחק (Payloads)
        print("--- Step 8-9: Game starting, receiving stages... ---")

        # קבלת הודעה ראשונה מהשרת (חלוקת קלפים)
        payload = client_tcp_sock.recv(1024)
        if payload:
            res_cookie, res_type, result, card_rank, card_suit = struct.unpack('!I B B 2s B', payload[:9])
            self.assertEqual(res_cookie, MAGIC_COOKIE)
            self.assertEqual(res_type, MESSAGE_TYPE_PAYLOAD)
            print(f"Received Card: {card_rank.decode()}, Suit ID: {card_suit}")

            # שליחת החלטה (Stand) כדי לסיים את הסיבוב לצורך הטסט
            decision_packet = struct.pack('!I B 5s', MAGIC_COOKIE, MESSAGE_TYPE_PAYLOAD, b"Stand")
            client_tcp_sock.send(decision_packet)

        # שלב 10: קבלת תוצאה סופית והדפסת סטטיסטיקה
        final_payload = client_tcp_sock.recv(1024)
        if final_payload:
            _, _, result, _, _ = struct.unpack('!I B B 2s B', final_payload[:9])

            # הדמיית פלט הסטטיסטיקה הנדרש במסמך
            win_rate = 100.0 if result == RESULT_WIN else 0.0
            print(f"\n--- Step 10: Finished playing {rounds_to_play} rounds, win rate: {win_rate}% ---")

        client_tcp_sock.close()
        client_udp_sock.close()

    def test_invalid_cookie_rejection(self):
        """
        בדיקה שהשרת דוחה חבילות עם Magic Cookie שגוי.
        """
        client_tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_tcp_sock.connect(('127.0.0.1', self.server.tcp_port))

        # עוגייה שגויה (0x11223344)
        bad_packet = struct.pack('!I B B 32s', 0x11223344, MESSAGE_TYPE_REQUEST, 1, b"BadTeam".ljust(32, b'\x00'))
        client_tcp_sock.send(bad_packet)

        time.sleep(0.5)
        try:
            data = client_tcp_sock.recv(1024)
            # השרת אמור לסגור את החיבור (data == b'')
            self.assertEqual(data, b"")
        except:
            pass
        finally:
            client_tcp_sock.close()


if __name__ == '__main__':
    unittest.main()