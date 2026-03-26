import socket
import ssl

print("1. Тест TCP подключения...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
try:
    sock.connect(('api.oneme.ru', 443))
    print("✅ TCP подключён")
except Exception as e:
    print(f"❌ TCP ошибка: {e}")
    exit()

print("2. Тест TLS...")
context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
try:
    ssl_sock = context.wrap_socket(sock, server_hostname='api.oneme.ru')
    print("✅ TLS подключён")
    print(f"   Версия TLS: {ssl_sock.version()}")
    print(f"   Шифр: {ssl_sock.cipher()}")
except Exception as e:
    print(f"❌ TLS ошибка: {e}")
    exit()

print("3. Отправка тестовых данных...")
try:
    # Отправляем простой ping (opcode 1)
    import struct
    # Заголовок: ver=10, cmd=0, seq=1, opcode=1, длина=0
    header = bytearray(10)
    header[0] = 10
    header[1] = 0
    header[2] = 0
    header[3] = 1
    header[4] = 0
    header[5] = 1
    header[6] = 0
    header[7] = 0
    header[8] = 0
    header[9] = 0
    ssl_sock.send(header)
    print("✅ Отправлен ping")
    
    print("4. Ожидание ответа...")
    response = ssl_sock.recv(1024)
    print(f"✅ Получен ответ: {len(response)} байт")
    print(f"   Hex: {response.hex()[:100]}...")
except Exception as e:
    print(f"❌ Ошибка: {e}")

ssl_sock.close()
