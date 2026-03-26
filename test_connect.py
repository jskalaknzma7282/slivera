from max_client import MaxClient

def main():
    client = MaxClient()
    try:
        client.connect()
        print("✅ Handshake успешен!")
        
        # Тест запроса кода
        phone = input("Введите номер телефона (+7...): ")
        token = client.request_code(phone)
        print(f"Токен для кода: {token}")
        
        code = input("Введите код из SMS: ")
        auth_data = client.verify_code(token, code)
        print(f"Данные авторизации: {auth_data}")
        
        reg_token = auth_data.get('tokenAttrs', {}).get('REGISTER', {}).get('token')
        if reg_token:
            final_token = client.register(reg_token)
            print(f"✅ Регистрация успешна! Токен: {final_token}")
        else:
            print("Аккаунт уже существует, вход выполнен")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        client.close()

if __name__ == "__main__":
    main()
