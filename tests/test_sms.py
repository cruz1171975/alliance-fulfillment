import pytest
from unittest.mock import patch, MagicMock
from fulfillment.sms import SMSNotifier


def test_format_low_stock_message():
    notifier = SMSNotifier(account_sid="test", auth_token="test", from_number="+10000000000")
    msg = notifier.format_low_stock_message(
        product_name="Isopropyl Alcohol 99% - 1 Gallon",
        picker_name="Maria",
        time_str="10:42 AM",
    )
    assert "LOW STOCK ALERT" in msg
    assert "Isopropyl Alcohol" in msg
    assert "Maria" in msg
    assert "10:42 AM" in msg


def test_format_restock_message():
    notifier = SMSNotifier("", "", "")
    msg = notifier.format_restock_message(
        product_name="Acetone - 1 Gallon",
        restock_qty=10,
        order_number="1234",
        picker_name="Maria",
        time_str="2:30 PM",
    )
    assert "RESTOCK NEEDED" in msg
    assert "10" in msg
    assert "Acetone - 1 Gallon" in msg
    assert "1234" in msg
    assert "Maria" in msg
    assert "2:30 PM" in msg


def test_send_sms_disabled_when_no_credentials():
    notifier = SMSNotifier(account_sid="", auth_token="", from_number="")
    result = notifier.send_sms("+15551234567", "test message")
    assert result is False


@patch("fulfillment.sms.Client")
def test_send_sms_calls_twilio(mock_client_cls):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(sid="SM123")
    mock_client_cls.return_value = mock_client

    notifier = SMSNotifier(account_sid="AC_test", auth_token="token", from_number="+10000000000")
    result = notifier.send_sms("+15551234567", "test message")

    assert result is True
    mock_client.messages.create.assert_called_once_with(
        body="test message",
        from_="+10000000000",
        to="+15551234567",
    )
