from twilio.rest import Client


class SMSNotifier:
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number

    def send_sms(self, to_number: str, message: str) -> bool:
        if not self.account_sid or not self.auth_token or not self.from_number:
            return False
        try:
            client = Client(self.account_sid, self.auth_token)
            client.messages.create(
                body=message,
                from_=self.from_number,
                to=to_number,
            )
            return True
        except Exception:
            return False

    def format_low_stock_message(
        self, product_name: str, picker_name: str, time_str: str
    ) -> str:
        return (
            f"LOW STOCK ALERT\n"
            f"Product: {product_name}\n"
            f"Flagged by: {picker_name}\n"
            f"Time: {time_str}"
        )

    def format_restock_message(
        self, product_name: str, restock_qty: int, order_number: str, picker_name: str, time_str: str
    ) -> str:
        return (
            f"RESTOCK NEEDED\n"
            f"{restock_qty} x {product_name}\n"
            f"Order #{order_number}\n"
            f"Flagged by: {picker_name}\n"
            f"Time: {time_str}"
        )
