import pytest
from fulfillment.auth import hash_password, verify_password, is_bcrypt_hash


def test_hash_password_returns_bcrypt_string():
    hashed = hash_password("mysecret")
    assert hashed.startswith("$2b$")
    assert hashed != "mysecret"


def test_verify_password_correct():
    hashed = hash_password("mysecret")
    assert verify_password("mysecret", hashed) is True


def test_verify_password_wrong():
    hashed = hash_password("mysecret")
    assert verify_password("wrongpassword", hashed) is False


def test_verify_password_empty_stored_returns_false():
    assert verify_password("anything", "") is False


def test_is_bcrypt_hash_true():
    hashed = hash_password("test")
    assert is_bcrypt_hash(hashed) is True


def test_is_bcrypt_hash_false_plain_text():
    assert is_bcrypt_hash("plaintext123") is False


def test_is_bcrypt_hash_false_empty():
    assert is_bcrypt_hash("") is False
