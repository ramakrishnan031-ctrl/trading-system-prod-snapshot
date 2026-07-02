"""Tests for the C-1 pre-commit secret scanner (deploy/hooks/secret_scan.py).

All fake secrets are CONSTRUCTED AT RUNTIME (concatenation) so this test file
contains no literal secret -- otherwise the scanner would (correctly) block the
commit that adds this very test. Verifies planted fakes are caught AND that
placeholders / non-secret defaults / normal code are NOT false-positived.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[2] / "deploy" / "hooks" / "secret_scan.py"
_spec = importlib.util.spec_from_file_location("secret_scan", _MOD_PATH)
secret_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secret_scan)


# ── fakes built at runtime (no literal secret in this file) ──────────────────
def _fake_telegram_token() -> str:
    return "1234567890" + ":" + ("A" * 35)          # \d{8,10}:[A-Za-z0-9_-]{35}


def _fake_hex64() -> str:
    return "ab12" * 16                               # 64 hex chars (token_hex(32) shape)


def _fake_base32_totp() -> str:
    return "K5" * 16                                 # 32 chars in [A-Z2-7]


def _fake_api_secret() -> str:
    return "abcd1234efgh5678ijkl"                    # 20 chars, mixed alnum


# ── planted secrets ARE blocked ──────────────────────────────────────────────
def test_telegram_bot_token_blocked():
    findings = secret_scan.scan_content("some/file.py", f'TELEGRAM_BOT_TOKEN={_fake_telegram_token()}')
    assert any("bot_token" in f for f in findings), findings


def test_webhook_hex64_secret_blocked():
    findings = secret_scan.scan_content("config/app.yaml", f"WEBHOOK_SECRET={_fake_hex64()}")
    assert any("cred_assign" in f for f in findings), findings


def test_totp_base32_seed_blocked():
    findings = secret_scan.scan_content("x.env", f"ZERODHA_TOTP_LFL836={_fake_base32_totp()}")
    assert any("cred_assign" in f for f in findings), findings


def test_api_secret_assignment_blocked():
    findings = secret_scan.scan_content("cfg.py", f'ZERODHA_API_SECRET_X = "{_fake_api_secret()}"')
    assert any("cred_assign" in f for f in findings), findings


def test_real_dotenv_file_blocked():
    findings = secret_scan.scan_content(".env", "SOME=thing\n")
    assert any("env_file" in f for f in findings), findings


def test_env_example_nonplaceholder_credential_blocked():
    # credential-named key in the template with a non-placeholder value → blocked
    findings = secret_scan.scan_content(".env.example", "ZERODHA_API_KEY_LFL836=abc123live")
    assert any("env_example_nonplaceholder" in f for f in findings), findings


# ── clean content is NOT false-positived ─────────────────────────────────────
def test_placeholder_env_example_passes():
    clean = (
        "ZERODHA_API_KEY_LFL836=FILL_WHEN_READY\n"
        "ZERODHA_API_SECRET_LFL836=FILL_WHEN_READY\n"
        "ZERODHA_TOTP_LFL836=FILL_WHEN_READY\n"
        "TELEGRAM_BOT_TOKEN=FILL_WHEN_READY\n"
        "TELEGRAM_CHANNEL_PRIMARY=FILL_CHANNEL_ID_1\n"
        "WEBHOOK_SECRET=FILL_WHEN_READY\n"
    )
    assert secret_scan.scan_content(".env.example", clean) == []


def test_env_example_nonsecret_default_allowed():
    # a non-credential, non-secret default in a template is fine (no false positive)
    assert secret_scan.scan_content(".env.example", "LOG_LEVEL=INFO\nAPP_MODE=production") == []


def test_env_var_reference_not_flagged():
    # reading a secret from the environment is correct, not a leak
    code = 'api_key = os.environ.get("ZERODHA_API_KEY_LFL836")'
    assert secret_scan.scan_content("main.py", code) == []


def test_normal_code_not_flagged():
    code = (
        "def place_order(symbol, qty):\n"
        "    tag = 'entry'\n"
        "    return adapter.place(symbol, qty, tag=tag)\n"
    )
    assert secret_scan.scan_content("orders/x.py", code) == []


def test_placeholder_values_allowed():
    for val in ("FILL_WHEN_READY", "<your_key>", "CHANGE_ME", "your_api_key", "xxxxxx", "${API_KEY}"):
        assert secret_scan.scan_content("cfg", f"API_KEY={val}") == [], val


# ── .xlsx layer (C-1 gap: a binary credentials.xlsx bypasses the text scanner) ──
def _xlsx_bytes(rows) -> bytes:
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_credentials_secret_cells_blocked():
    # a credentials.xlsx with real-looking secret cells must be caught
    data = _xlsx_bytes([
        ["Account_ID", "Kite_Password", "API_Key", "API_Secret", "2FA_Code"],
        ["LFL836", "shortpw12", "apikey1234567xyz", _fake_api_secret(), _fake_base32_totp()],
    ])
    findings = secret_scan.scan_xlsx("credentials.xlsx", data)
    assert any("xlsx_secret_value" in f for f in findings), findings


def test_xlsx_bot_token_cell_blocked():
    data = _xlsx_bytes([["Telegram", "Bot_Token"], ["1", _fake_telegram_token()]])
    findings = secret_scan.scan_xlsx("creds.xlsx", data)
    assert any("xlsx_bot_token" in f for f in findings), findings


def test_xlsx_clean_workbook_passes():
    # a benign spreadsheet (short values, no secret shapes) is NOT false-positived
    data = _xlsx_bytes([
        ["Symbol", "Qty", "Note"],
        ["RELIANCE", 100, "ok"],
        ["INFY", 50, "hold"],
    ])
    assert secret_scan.scan_xlsx("reports/output/x.xlsx", data) == []


def test_xlsx_unparseable_fails_closed():
    # an .xlsx we cannot open must BLOCK (a binary we can't inspect could hide a secret)
    findings = secret_scan.scan_xlsx("credentials.xlsx", b"PK\x03\x04 not really a workbook")
    assert findings, "unparseable .xlsx must be blocked (fail closed)"
    assert any(("xlsx_unparseable" in f) or ("xlsx_unscannable" in f) for f in findings), findings
