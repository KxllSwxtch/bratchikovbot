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

## Customization

- **Channel username**: Set the `CHANNEL_USERNAME` variable in `main.py` to your Telegram channel.
- **Logo URL**: Update the `logo_url` variable if you want to use a different image.

## Error Handling

- The bot deletes previous error messages before sending a new one to avoid clutter.
- All errors are logged for debugging.

## Contact

For questions or support, please contact the developers or the company "Импорт без проблем".
