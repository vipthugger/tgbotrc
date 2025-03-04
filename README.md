
# Telegram Bot для модерации объявлений

## Развертывание на Render.com

### Шаги для деплоя:

1. Создайте аккаунт на [Render.com](https://render.com)

2. Нажмите "New" и выберите "Web Service"

3. Подключите свой GitHub репозиторий или выберите "Build and deploy from a Git repository"

4. Настройте деплой:
   - **Name**: Имя вашего бота
   - **Environment**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`

5. В разделе "Environment Variables" добавьте:
   - `TELEGRAM_BOT_TOKEN` = ваш токен от @BotFather

6. Нажмите "Create Web Service"

### Важные особенности:

- Render.com предоставляет бесплатный уровень, но с ограничениями
- Бесплатный сервис уходит в сон после 15 минут неактивности
- Для постоянной работы бота рекомендуется использовать платный тариф
