"""Standalone webhook test - bypasses holiday guard for connectivity testing."""
from flask import Flask

app = Flask(__name__)


@app.route('/health', methods=['GET', 'POST'])
def health():
    return {"status": "ok", "port": 5000, "test_mode": True}


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
