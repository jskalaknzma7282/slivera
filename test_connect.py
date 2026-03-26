from max_client import MaxClient

def main():
    client = MaxClient()
    try:
        client.connect()
        print("✅ Подключение и handshake успешны!")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    main()
