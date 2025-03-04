import requests
import os
import time
import sys
import subprocess
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def check_running_bots():
    """Проверяет, нет ли других запущенных экземпляров бота с тем же токеном"""
    try:
        # Ищем другие процессы Python с названием bot.py
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        processes = result.stdout.split('\n')
        bot_processes = [p for p in processes if 'python' in p and 'bot.py' in p and not 'grep' in p]
        
        # Если найдено больше одного процесса (включая текущий), завершаем их
        if len(bot_processes) > 1:
            logging.warning(f"Найдено {len(bot_processes)} запущенных экземпляров бота, завершаем их...")
            
            # Сначала завершаем по SIGTERM
            for process in bot_processes:
                try:
                    pid = int(process.split()[1])
                    logging.info(f"Завершение процесса бота с PID: {pid}")
                    subprocess.run(['kill', '-15', str(pid)])
                except Exception as e:
                    logging.error(f"Ошибка при завершении процесса SIGTERM: {e}")
            
            # Даем время на завершение процессов
            time.sleep(3)
            
            # Принудительно завершаем, если нужно
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            remaining = [p for p in result.stdout.split('\n') if 'python' in p and 'bot.py' in p and not 'grep' in p]
            
            if remaining:
                logging.warning(f"Остались {len(remaining)} процессов, принудительно завершаем...")
                for process in remaining:
                    try:
                        pid = int(process.split()[1])
                        logging.info(f"Принудительное завершение процесса бота с PID: {pid}")
                        subprocess.run(['kill', '-9', str(pid)])
                    except Exception as e:
                        logging.error(f"Ошибка при завершении процесса SIGKILL: {e}")
                
                time.sleep(2)
            
            return True
        return True
    except Exception as e:
        logging.error(f"Ошибка при проверке запущенных ботов: {e}")
        return False

def check_bot_status():
    """Проверяет статус бота через Telegram API"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logging.error("TELEGRAM_BOT_TOKEN не установлен")
        return False
        
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        response = requests.get(url)
        data = response.json()
        if data["ok"]:
            logging.info(f"Бот работает: @{data['result']['username']}")
            return True
        else:
            logging.error(f"Ошибка: {data}")
            return False
    except Exception as e:
        logging.error(f"Ошибка при проверке статуса бота: {e}")
        return False

if __name__ == "__main__":
    logging.info("Запуск проверки перед стартом бота...")
    
    # Проверяем и завершаем другие экземпляры бота
    if not check_running_bots():
        logging.error("Ошибка при проверке запущенных ботов")
        sys.exit(1)
    
    # Проверяем доступность API Telegram
    time.sleep(5)  # Даем время для завершения других процессов
    
    if check_bot_status():
        logging.info("Проверка успешна, бот готов к запуску")
        sys.exit(0)  # Успешное завершение
    else:
        logging.error("Ошибка при проверке статуса бота")
        sys.exit(1)  # Ошибка
