import stripe
import logging
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode
import asyncio

stripe.api_key = os.getenv("STRIPE_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CardChecker:
    DECLINE_CODES = {
        'insufficient_funds': '51',
        'lost_card': '41',
        'stolen_card': '41',
        'expired_card': '54',
        'incorrect_cvc': '55',
        'incorrect_number': '14',
        'invalid_cvc': '55',
        'invalid_expiry_month': '05',
        'invalid_expiry_year': '06',
        'card_declined': '05',
        'generic_decline': '05',
        'fraudulent': '78',
        'card_not_supported': '16',
        'currency_not_supported': '50',
        'duplicate_transaction': '94',
        'processing_error': '96',
        'card_velocity_exceeded': '91',
        'do_not_honor': '05',
        'do_not_try_again': '86',
        'pickup_card': '35',
    }

    BANK_NAMES = {
        '4': 'VISA',
        '5': 'MASTERCARD',
        '3': 'AMEX',
        '6': 'DISCOVER',
    }

    @staticmethod
    def parse_card(card_string: str) -> dict:
        card_string = card_string.strip()

        # Detect primary separator: pipe, comma, or whitespace (in priority order)
        if '|' in card_string:
            parts = [p.strip() for p in card_string.split('|')]
        elif ',' in card_string:
            parts = [p.strip() for p in card_string.split(',')]
        else:
            parts = card_string.split()

        # Remove any empty parts produced by splitting
        parts = [p for p in parts if p]

        if len(parts) == 3:
            # Expiry is combined as month/year in the second field: number|month/year|cvc
            card_num = parts[0].replace(' ', '').replace('-', '')
            expiry = parts[1]
            cvc = parts[2]

            if '/' in expiry:
                exp_month, exp_year = expiry.split('/', 1)
                exp_month = exp_month.strip()
                exp_year = exp_year.strip()
            else:
                return None

        elif len(parts) >= 4:
            # Expiry is split across two separate fields: number|month|year|cvc
            card_num = parts[0].replace(' ', '').replace('-', '')
            exp_month = parts[1]
            exp_year = parts[2]
            cvc = parts[3]

            # Handle month/year combined in the month field (e.g. space-separated with slash)
            if '/' in exp_month:
                exp_month, exp_year = exp_month.split('/', 1)
                exp_month = exp_month.strip()
                exp_year = exp_year.strip()
                # cvc was already set to parts[2]; shift it to parts[3] is not needed
                # because in this branch parts[1] held month/year, so cvc is parts[2]
                cvc = parts[2]

        else:
            return None

        if len(exp_year) == 2:
            exp_year = '20' + exp_year

        return {
            "number": card_num,
            "exp_month": exp_month,
            "exp_year": exp_year,
            "cvc": cvc
        }

    @staticmethod
    def validate_card(card_data: dict) -> tuple:
        if not card_data:
            return False, "Invalid format"
        
        card_num = card_data.get("number", "")
        if not card_num.isdigit() or not (13 <= len(card_num) <= 19):
            return False, "Invalid card number"
        
        try:
            month = int(card_data.get("exp_month", ""))
            if not (1 <= month <= 12):
                return False, "Invalid month"
        except:
            return False, "Invalid month"
        
        try:
            year = int(card_data.get("exp_year", ""))
            if year < 2025:
                return False, "Expired"
        except:
            return False, "Invalid year"
        
        cvc = card_data.get("cvc", "")
        if not cvc.isdigit() or not (3 <= len(cvc) <= 4):
            return False, "Invalid CVC"
        
        return True, None

    @staticmethod
    def get_bank(card_num: str) -> str:
        first_digit = card_num[0]
        return CardChecker.BANK_NAMES.get(first_digit, 'UNKNOWN')

    @staticmethod
    async def check_card(card_data: dict, amount: int = 100) -> dict:
        try:
            token = stripe.Token.create(
                card={
                    "number": card_data["number"],
                    "exp_month": card_data["exp_month"],
                    "exp_year": card_data["exp_year"],
                    "cvc": card_data["cvc"],
                }
            )
            
            charge = stripe.Charge.create(
                amount=amount,
                currency="usd",
                source=token.id,
                capture=False
            )
            
            return {
                "status": "LIVE",
                "code": "00",
                "message": "Card approved"
            }
        
        except stripe.error.CardError as e:
            decline_code = e.decline_code or "05"
            code = CardChecker.DECLINE_CODES.get(decline_code, "05")
            
            if decline_code == 'insufficient_funds':
                status = "INSUFF"
            else:
                status = "DEAD"
            
            return {
                "status": status,
                "code": code,
                "message": str(e.user_message)
            }
        
        except Exception as e:
            return {
                "status": "ERROR",
                "code": "96",
                "message": str(e)
            }

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /check number|month|year|cvc")
        return
    
    card_string = ' '.join(context.args)
    card_data = CardChecker.parse_card(card_string)
    
    valid, error = CardChecker.validate_card(card_data)
    if not valid:
        await update.message.reply_text(f"❌ {error}")
        return
    
    await update.message.reply_text("🔄 Checking card...")
    
    result = await CardChecker.check_card(card_data, amount=100)
    bank = CardChecker.get_bank(card_data["number"])
    cc = card_data["number"]
    
    response = f"**{result['status']} | {result['code']} | {cc} | {bank}**"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)

async def bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send card list (one per line): number|month|year|cvc")
    context.user_data['bulk_mode'] = 'check'

async def auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /auth number|month|year|cvc")
        return
    
    card_string = ' '.join(context.args)
    card_data = CardChecker.parse_card(card_string)
    
    valid, error = CardChecker.validate_card(card_data)
    if not valid:
        await update.message.reply_text(f"❌ {error}")
        return
    
    await update.message.reply_text("🔄 0-authing card...")
    
    result = await CardChecker.check_card(card_data, amount=0)
    bank = CardChecker.get_bank(card_data["number"])
    cc = card_data["number"]
    
    response = f"**{result['status']} | {result['code']} | {cc} | {bank}**"
    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)

async def mass_auth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send card list (one per line): number|month|year|cvc")
    context.user_data['bulk_mode'] = 'auth'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('bulk_mode') not in ['check', 'auth']:
        return
    
    bulk_mode = context.user_data['bulk_mode']
    cards = update.message.text.strip().split('\n')
    amount = 100 if bulk_mode == 'check' else 0
    
    await update.message.reply_text(f"🔄 Processing {len(cards)} cards...")
    
    for i, card_string in enumerate(cards):
        card_data = CardChecker.parse_card(card_string)
        valid, error = CardChecker.validate_card(card_data)
        
        if not valid:
            await update.message.reply_text(f"❌ {card_string} - {error}")
        else:
            result = await CardChecker.check_card(card_data, amount=amount)
            bank = CardChecker.get_bank(card_data["number"])
            cc = card_data["number"]
            
            response = f"**{result['status']} | {result['code']} | {cc} | {bank}**"
            await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
        
        if i < len(cards) - 1:
            await asyncio.sleep(2)
    
    context.user_data['bulk_mode'] = None

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("bulk", bulk_command))
    app.add_handler(CommandHandler("auth", auth_command))
    app.add_handler(CommandHandler("mass_auth", mass_auth_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == '__main__':
    main()