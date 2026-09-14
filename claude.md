# CLAUDE.md

## Project Overview

This project is a Telegram bot for the company "Импорт без проблем" that helps users calculate the cost of importing a car from South Korea (🇰🇷) to Russia (🇷🇺). The bot provides a user-friendly interface, fetches car information from external sources (such as Encar), and guides users through the process.

## Key Features

- **Welcome Flow:** Greets users and provides a main menu for navigation.
- **Subscription Check:** Ensures users are subscribed to a specific Telegram channel before accessing full functionality.
- **Car Information Retrieval:** Extracts and displays detailed car information (make, model, trim, price, year, mileage, transmission, etc.) from Encar links.
- **Error Handling:** Gracefully manages and reports errors to users.
- **Company Branding:** Sends the company logo as part of the welcome sequence.

## Main Technologies

- **Python**
- **pyTelegramBotAPI** (telebot)
- **Requests** (for HTTP requests to external APIs)
- **Cloudinary** (for hosting images/logos)

## File Structure

- `main.py` — Main bot logic, handlers, and utility functions.
- `claude.md` — Project documentation (this file).

## How It Works

1. **User starts the bot** and is greeted with a welcome message and the company logo.
2. **Subscription check**: If the user is not subscribed to the required channel, they are prompted to do so.
3. **Car info retrieval**: Users can submit a link to a car on Encar. The bot fetches and parses the car's details using Encar's API.
4. **Results**: The bot formats and sends the car's information, including price, year, mileage, transmission type, and more.

## Example Usage

1. User sends `/start`.
2. Bot replies with a welcome message and logo.
3. If not subscribed, user is prompted to subscribe.
4. User sends a link to a car on Encar.
5. Bot replies with detailed information about the car.

## China Flow (global.che168.com)

- Both `https://global.che168.com/{locale}/detail/{infoid}` links and old `m.che168.com` links (`infoid=` in the query) are calculated. Old links are converted to the global link with the same infoid first.
- `che168_scraper.py` calls the JSON API at `globalapi.che168.com/api/v1` (`carinfo/{infoid}` and `specparam?specid=`). The HTML pages are behind a Tencent EdgeOne captcha, so never scrape them.
- The API gives prices in USD. The site converts from yuan at a fixed rate (`CHE168_CNY_PER_USD`, default 6.575), so the bot recovers the exact yuan price and calculates in CNY as before. A log warning "site CNY/USD rate may have changed" means the rate needs re-deriving.
- Car age comes from `manufacturedate`, falling back to `regdate`. Horsepower depends on fuel type: engine Ps for petrol/diesel, motor Ps for EV and range extender, combined system Ps for hybrids. It is never defaulted; if missing, the bot asks the user.
- Tests: `venv/bin/python -m unittest discover -s tests -v` (fixtures are real API responses).

## Customization

- **Channel username**: Set the `CHANNEL_USERNAME` variable in `main.py` to your Telegram channel.
- **Logo URL**: Update the `logo_url` variable if you want to use a different image.

## Error Handling

- The bot deletes previous error messages before sending a new one to avoid clutter.
- All errors are logged for debugging.

## Contact

For questions or support, please contact the developers or the company "Импорт без проблем".
