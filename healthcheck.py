
import requests
import os
import time
import sys

def check_bot_status():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN не установлен")
        return False
        
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        response = requests.get(url)
        data = response.json()
        if data["ok"]:
            print(f"Бот работает: @{data['result']['username']}")
            return True
        else:
            print(f"Ошибка: {data}")
            return False
    except Exception as e:
        print(f"Ошибка при проверке статуса бота: {e}")
        return False

if __name__ == "__main__":
    # Даем время боту запуститься
    time.sleep(10)
    
    # Проверяем статус
    if check_bot_status():
        sys.exit(0)  # Успешное завершение
    else:
        sys.exit(1)  # Ошибка
